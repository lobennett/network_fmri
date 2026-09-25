"""Version manual FreeSurfer inputs and run them through an upstream campaign."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

from network_fmri.campaign import Campaign, read_table
from network_fmri.freesurfer_app import copy_reconstruction
from network_fmri.milestones import write_json_atomic
from network_fmri.mriqc import _git, _gitlink, _require_dataset, _sha256
from network_fmri.surface_inventory import reconstruction_inventory

STATE = "code/network_fmri/surface-correction.json"
EDIT_FILES = {"wm": {"mri/wm.mgz"}, "pial": {"mri/brain.finalsurfs.manedit.mgz"}}
EDIT_FILES["wm-pial"] = EDIT_FILES["wm"] | EDIT_FILES["pial"]


def check_edits(baseline: Path, work: Path, subject: str, kind: str) -> list[str]:
    """Permit declared edits only, with no resampling or unrelated file changes."""
    import nibabel as nib
    import numpy as np

    before = reconstruction_inventory(baseline, subject)
    after = reconstruction_inventory(work, subject)
    changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
    if not changed:
        raise ValueError(f"no edits for sub-{subject}")
    if kind not in EDIT_FILES or set(changed) - EDIT_FILES[kind] or before.keys() - after.keys():
        raise ValueError(f"changes outside declared correction inputs for sub-{subject}: {changed}")
    for relative in changed:
        original = baseline / relative
        if not original.exists() and relative.endswith("brain.finalsurfs.manedit.mgz"):
            original = baseline / "mri/brain.finalsurfs.mgz"
        a, b = nib.load(original), nib.load(work / relative)
        if a.shape != b.shape or not np.allclose(a.affine, b.affine, atol=1e-5, rtol=0):
            raise ValueError(f"image grid changed: {relative}")
        if not np.isfinite(b.get_fdata()).all():
            raise ValueError(f"non-finite edit values: {relative}")
    return changed


def correction_state(config, *, runner=subprocess.run):
    path = config.mechababs.study_dir / STATE
    if not path.exists() and not path.is_symlink():
        study = config.mechababs.study_dir
        if (study / ".git").exists() and _git(study, "ls-tree", "--name-only", "HEAD", "--", STATE, runner=runner):
            raise RuntimeError("committed correction state is missing from the working tree")
        return None
    if path.is_symlink():
        raise RuntimeError("correction state must not be a symlink")
    value = json.loads(path.read_text())
    committed = _git(config.mechababs.study_dir, "show", f"HEAD:{STATE}", runner=runner)
    if json.loads(committed) != value:
        raise RuntimeError("correction state is not committed")
    if (value.get("original_campaign") != config.mechababs.campaign
            or value.get("subjects") != list(config.subjects)
            or value.get("raw_commit") != _git(config.paths.bids_dir, "rev-parse", "HEAD", runner=runner)):
        raise RuntimeError("correction state does not match this workflow and BIDS input")
    if value.get("phase") not in {"editing", "initializing", "ready"}:
        raise RuntimeError("unknown correction phase")
    if value["phase"] in {"initializing", "ready"}:
        inputs = Path(value["input_dataset"])
        _require_dataset(inputs, runner=runner)
        if (_git(inputs, "rev-parse", "HEAD", runner=runner) != value["input_commit"]
                or _gitlink(config.mechababs.study_dir, "HEAD", inputs.relative_to(config.mechababs.study_dir).as_posix(),
                            runner=runner) != value["input_commit"]):
            raise RuntimeError("sealed correction input commit changed")
        for relative, expected in value.get("campaign_files", {}).items():
            path = config.mechababs.study_dir / relative
            if path.is_symlink() or _sha256(path) != expected:
                raise RuntimeError("correction campaign configuration changed")
    return value


def campaign_config(config, state):
    """Keep MRIQC in its original campaign; corrections own anatomy and fMRIPrep."""
    label = state["campaign"] if state["phase"] == "ready" else state["parent_campaign"]
    if label == config.mechababs.campaign:
        return config.mechababs
    return replace(config.mechababs, campaign=label,
                   apps=tuple(a for a in config.mechababs.apps if a.name != "mriqc"))


@contextmanager
def correction_lock(config):
    from network_fmri.handoff import _lock_path
    with _lock_path(config.mechababs.study_dir).open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("stop the processing controller before preparing corrections") from error
        yield


def _save(config, paths, message, runner):
    runner(("datalad", "save", "-d", str(config.mechababs.study_dir), "-m", message,
            *(str(p) for p in paths)), check=True)


def _record_state(config, state, runner):
    path = config.mechababs.study_dir / STATE
    write_json_atomic(path, state)
    _save(config, [path], "Record FreeSurfer correction handoff", runner)


def checkout_corrections(config, output: Path, subjects, kind: str, reviewer: str,
                         reason: str, *, runner=subprocess.run) -> Path:
    """Create writable copies and immediately block old surface approval."""
    from network_fmri.processing import ProcessingManager
    from network_fmri.surface_evidence import prepare_surface_evidence

    subjects = tuple(subjects)
    if (not subjects or len(set(subjects)) != len(subjects) or set(subjects) - set(config.subjects)
            or kind not in EDIT_FILES or not reviewer.strip() or not reason.strip()):
        raise ValueError("specify sample subjects, correction kind, reviewer, and reason")
    output = output.resolve()
    if output.exists() or output.is_symlink() or output.is_relative_to(config.mechababs.study_dir.resolve()):
        raise ValueError("use a new work directory outside the study")
    with correction_lock(config):
        previous = correction_state(config, runner=runner)
        if previous and previous["phase"] != "ready":
            raise RuntimeError("a correction is already pending")
        manager = ProcessingManager(config, runner=runner)
        stages = manager.plan()
        if next(s for s in stages if s.stage == "anatomical").application != "FreeSurfer-8.2.0":
            raise RuntimeError("corrections require the standalone FreeSurfer 8.2.0 application")
        if next(s for s in stages if s.stage == "fmriprep").state not in {"ready", "blocked"}:
            raise RuntimeError("fMRIPrep has already started; use a new study for a new analysis")
        evidence = prepare_surface_evidence(config, runner=runner)
        anatomical = next(s for s in stages if s.stage == "anatomical")
        parent = campaign_config(config, previous).campaign if previous else config.mechababs.campaign
        revision = (previous["revision"] + 1) if previous else 1
        while (config.mechababs.study_dir / "sourcedata" / f"freesurfer-edits-{revision}").exists():
            revision += 1
        state = {"schema_version": 1, "phase": "editing", "revision": revision,
                 "original_campaign": config.mechababs.campaign, "parent_campaign": parent,
                 "campaign": f"{config.mechababs.campaign}-edit{revision}",
                 "subjects": list(config.subjects), "edited_subjects": list(subjects),
                 "kind": kind, "reviewer": reviewer.strip(), "reason": reason.strip(),
                 "workspace": str(output), "baseline": str(evidence), "source_project": anatomical.project,
                 "raw_commit": _git(config.paths.bids_dir, "rev-parse", "HEAD", runner=runner),
                 "previous_state_commit": _git(config.mechababs.study_dir, "rev-parse", "HEAD", runner=runner),
                 "evidence_receipt_sha256": _sha256(evidence.parent / "code/network_fmri/surface-evidence.json")}
        # Record the block before exposing an editable copy, including on copy failure.
        _record_state(config, state, runner)
        output.mkdir(parents=True)
        for subject in subjects:
            source = evidence / f"sub-{subject}"
            expected = reconstruction_inventory(source, subject)
            copy_reconstruction(source, output / f"sub-{subject}")
            if reconstruction_inventory(output / f"sub-{subject}", subject) != expected:
                raise RuntimeError("reconstruction changed while preparing edit copy")
        (output / "README.txt").write_text(
            "Edit only the declared FreeSurfer inputs. Submit with network-fmri surfaces submit-corrections.\n")
        return output


def cancel_corrections(config, *, runner=subprocess.run):
    """Cancel an unsubmitted edit round, preserving the working copy and Git history."""
    with correction_lock(config):
        state = correction_state(config, runner=runner)
        if not state or state["phase"] != "editing":
            raise RuntimeError("only unsubmitted corrections can be cancelled")
        study = config.mechababs.study_dir
        commit = state["previous_state_commit"]
        if _git(study, "ls-tree", "--name-only", commit, "--", STATE, runner=runner):
            previous = json.loads(_git(study, "show", f"{commit}:{STATE}", runner=runner))
            _record_state(config, previous, runner)
        else:
            (study / STATE).unlink()
            _save(config, [study / STATE], "Cancel unsubmitted FreeSurfer corrections", runner)
        return {"state": "cancelled", "workspace_preserved": state["workspace"]}


def _baseline_records(config, state, runner):
    from network_fmri.surface_evidence import RECEIPT
    baseline = Path(state["baseline"])
    path = baseline.parent / RECEIPT
    if _sha256(path) != state["evidence_receipt_sha256"]:
        raise RuntimeError("baseline receipt changed")
    receipt = json.loads(path.read_text())
    source = config.mechababs.study_dir / state["source_project"]
    _require_dataset(source, runner=runner)
    if _git(source, "rev-parse", "HEAD", runner=runner) != receipt["source_dataset_commit"]:
        raise RuntimeError("baseline source commit changed")
    records = {}
    for archive in receipt["archives"]:
        path = source / archive["path"]
        if _sha256(path) != archive["sha256"]:
            raise RuntimeError("baseline archive changed")
        with zipfile.ZipFile(path) as stream:
            names = stream.namelist()
            for subject in config.subjects:
                name = f"FreeSurfer-8.2.0/code/sub-{subject}_reconstruction.json"
                if name in names:
                    if names.count(name) != 1 or subject in records:
                        raise RuntimeError("duplicate reconstruction receipt")
                    record = json.loads(stream.read(name))
                    if (record.get("status") != "success" or record.get("subject") != f"sub-{subject}"
                            or not re.search(r"(?<![\d.])8\.2\.0(?![\d.])", record.get("build", ""))):
                        raise RuntimeError("baseline reconstruction was not successful FreeSurfer 8.2.0")
                    records[subject] = record
    if set(records) != set(config.subjects):
        raise RuntimeError("missing original reconstruction receipts")
    for subject in config.subjects:
        if reconstruction_inventory(baseline / f"sub-{subject}", subject) != receipt["inventories"][subject]:
            raise RuntimeError("baseline reconstruction changed")
    return records, receipt


def _package_inputs(config, state, destination, runner):
    records, receipt = _baseline_records(config, state, runner)
    baseline, work = Path(state["baseline"]), Path(state["workspace"])
    edited = set(state["edited_subjects"])
    for subject in edited:
        check_edits(baseline / f"sub-{subject}", work / f"sub-{subject}", subject, state["kind"])
    destination.mkdir(parents=True)
    for subject in config.subjects:
        root = (work if subject in edited else baseline) / f"sub-{subject}"
        inventory = reconstruction_inventory(root, subject)
        record = {"subject": f"sub-{subject}", "kind": state["kind"] if subject in edited else "none",
                  "build": records[subject]["build"], "inputs": records[subject]["inputs"],
                  "inventory": inventory, "baseline_inventory": receipt["inventories"][subject],
                  "source_dataset_commit": receipt["source_dataset_commit"],
                  "reviewer": state["reviewer"], "reason": state["reason"]}
        with zipfile.ZipFile(destination / f"sub-{subject}_FreeSurferEdits.zip", "x", zipfile.ZIP_DEFLATED) as stream:
            for relative in inventory:
                stream.write(root / relative, f"FreeSurferEdits/subjects/sub-{subject}/{relative}")
            stream.writestr(f"FreeSurferEdits/code/sub-{subject}.json", json.dumps(record, indent=2))
        if (reconstruction_inventory(root, subject) != inventory
                or reconstruction_inventory(destination / f"sub-{subject}_FreeSurferEdits.zip", subject) != inventory):
            raise RuntimeError("edit input changed while packaging")
    write_json_atomic(destination / "correction.json", state)


def submit_corrections(config, *, runner=subprocess.run):
    """Seal edits, initialize a separate upstream campaign, and select it for handoff."""
    from network_fmri.study import _MECHABABS_URL, _BABS_URL
    import yaml

    with correction_lock(config):
        state = correction_state(config, runner=runner)
        if not state or state["phase"] not in {"editing", "initializing"}:
            raise RuntimeError("checkout corrections before submitting")
        study = config.mechababs.study_dir
        parent = campaign_config(config, state)
        requested = [("clusters", parent.cluster_file)] + [
            ("bids-app-configs", app.file) for app in parent.apps if app.name != "mriqc"]
        configs = [(kind, relative, _git(study, "show",
                    f"HEAD:{(parent.campaign_dir / kind / relative.name).relative_to(study)}", runner=runner))
                   for kind, relative in requested]
        destination = study / "sourcedata" / f"freesurfer-edits-{state['revision']}"
        if state["phase"] == "editing":
            if destination.exists():
                raise RuntimeError("correction inputs already exist; preserved for recovery")
            with tempfile.TemporaryDirectory(prefix=".surface-inputs-", dir=study / "sourcedata") as temp:
                staging = Path(temp) / "inputs"
                _package_inputs(config, state, staging, runner)
                staging.rename(destination)
            runner(("datalad", "create", "--force", str(destination)), check=True)
            runner(("datalad", "save", "-d", str(destination), "-m", "Seal manual FreeSurfer inputs"), check=True)
            _save(config, [destination], "Register FreeSurfer edit inputs", runner)
            state.update(phase="initializing", input_dataset=str(destination),
                         input_commit=_git(destination, "rev-parse", "HEAD", runner=runner))
            _record_state(config, state, runner)
        new_config = replace(config.mechababs, campaign=state["campaign"],
                             apps=tuple(a for a in config.mechababs.apps if a.name != "mriqc"))
        # Reuse the committed parent settings rather than newly installed templates.
        with tempfile.TemporaryDirectory(prefix="network-surface-correction-") as temp:
            directory = Path(temp)
            apps = []
            cluster = None
            expected = {}
            for kind, relative, content in configs:
                if relative.stem == "FreeSurfer-8.2.0":
                    value = yaml.safe_load(content)
                    value["mechababs"].pop("depends_on", None)
                    value["bids_app_args"]["--corrections-dir"] = '"${PWD}"/sourcedata/FreeSurferEdits/FreeSurferEdits'
                    value["input_datasets"] = {"FreeSurferEdits": {
                        "origin_url": str(destination), "is_zipped": True,
                        "path_in_babs": "sourcedata/FreeSurferEdits",
                        "required_files": ["*FreeSurferEdits.zip"]}}
                    content = yaml.safe_dump(value, sort_keys=False)
                target = directory / relative.name
                target.write_text(content)
                expected[new_config.campaign_dir / kind / relative.name] = content
                if kind == "bids-app-configs":
                    apps.append(str(target))
                else:
                    cluster = str(target)
            upstream = ("uvx", "--from", f"git+{_MECHABABS_URL}@{new_config.mechababs_commit}", "mechababs")
            if new_config.campaign_dir.exists():
                for path, content in expected.items():
                    if not path.is_file() or path.read_text().strip() != content.strip():
                        raise RuntimeError("incomplete or changed correction campaign configuration; original inputs preserved")
                runner((*upstream, "campaign", "update-env"), cwd=str(study), check=True,
                       env=dict(os.environ, MECHABABS_CAMPAIGN=new_config.campaign))
                # Upstream init saves only on success; a failed environment build
                # can leave its otherwise valid config/state skeleton untracked.
                _save(config, [new_config.campaign_dir], "Recover verified correction campaign", runner)
            else:
                runner((*upstream, "campaign", "init", new_config.campaign, "-d", str(study),
                        "--apps", ",".join(apps), "--cluster", cluster,
                        "--mechababs", f"{_MECHABABS_URL}@{new_config.mechababs_commit}",
                        "--babs", f"{_BABS_URL}@{new_config.babs_commit}"), cwd=str(study), check=True)
        campaign = Campaign(new_config, runner=runner)
        output = campaign.run("status").stdout
        rows = read_table(output) if output.strip() else ()
        if not rows:
            campaign.run("add-dataset", "--sourcedata", f"sourcedata/{new_config.raw_slot}")
            output = campaign.run("status").stdout
            rows = read_table(output) if output.strip() else ()
        if {(r["source_dataset"], r["app"]) for r in rows} != {
                (f"sourcedata/{new_config.raw_slot}", app.file.stem) for app in new_config.apps}:
            raise RuntimeError("correction campaign has unexpected data or apps")
        state["phase"] = "ready"
        state["campaign_files"] = {
            p.relative_to(study).as_posix(): _sha256(p)
            for p in new_config.campaign_dir.rglob("*.yaml") if ".venv" not in p.parts}
        _record_state(config, state, runner)
        return {"campaign": state["campaign"], "state": "ready", "next": "network-fmri processing run <config>"}


def refresh_surface_review(config, evidence: Path, state):
    """Archive the previous review and require fresh approval of corrected evidence."""
    from network_fmri.milestones import receipt_path
    from network_fmri.models import StageResult
    from network_fmri.qa.freesurfer import generate_surface_review, surface_review_directory

    root = surface_review_directory(config)
    manifest = root / "surface_review.tsv"
    metadata = manifest.with_suffix(".meta.json")
    if not manifest.exists():
        return generate_surface_review(config, evidence)
    old = Path(json.loads(metadata.read_text())["surface_root"]).resolve()
    if old == evidence.resolve():
        return None
    if not state or state["phase"] != "ready" or old != Path(state["baseline"]).resolve():
        raise RuntimeError("surface review refers to unexpected evidence; existing review preserved")
    history = root / "surface-review-history" / state["campaign"]
    history.mkdir(parents=True, exist_ok=False)
    approval = receipt_path(config.mechababs.study_dir, "surface-review-approved")
    moved = []
    for path in (manifest, metadata, approval):
        if path.exists():
            target = history / path.name
            path.rename(target)
            moved.extend((path, target))
    result = generate_surface_review(config, evidence)
    return StageResult(result.name, tuple(dict.fromkeys((*result.outputs, *moved))), result.details)
