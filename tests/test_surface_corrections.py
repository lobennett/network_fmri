"""Manual edits must remain traceable and cannot silently change other outputs."""
import json
from pathlib import Path
import subprocess

import nibabel as nib
import numpy as np
import pytest

from network_fmri.surface_inventory import reconstruction_inventory


@pytest.mark.parametrize("command,options", [
    ("checkout", ["--subject", "s03", "--kind", "wm", "--reviewer", "LB", "--reason", "hole", "--output", "/tmp/edits"]),
    ("submit-corrections", []), ("cancel-corrections", []),
])
def test_surface_commands_are_reachable(command, options):
    from network_fmri.cli import get_parser
    parsed = get_parser().parse_args(["surfaces", command, "workflow.toml", *options])
    assert parsed.surface_command == command


def reconstruction(root):
    subject = root / "sub-s03"
    for name in ("wm.mgz", "brain.finalsurfs.mgz", "ribbon.mgz"):
        path = subject / "mri" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        nib.save(nib.MGHImage(np.zeros((3, 3, 3), dtype=np.float32), np.eye(4)), path)
    (subject / "scripts").mkdir()
    (subject / "scripts/recon-all.done").write_text("finished")
    return subject


def paint(path, *, affine=None):
    img = nib.load(path)
    data = img.get_fdata().astype(np.float32)
    data[1, 1, 1] = 255
    nib.save(nib.MGHImage(data, img.affine if affine is None else affine), path)


def test_only_declared_inputs_may_change_and_grid_is_preserved(tmp_path):
    from network_fmri.surface_corrections import check_edits
    import shutil
    baseline = reconstruction(tmp_path / "baseline")
    work = tmp_path / "work/sub-s03"
    shutil.copytree(baseline, work)
    paint(work / "mri/wm.mgz")
    assert check_edits(baseline, work, "s03", "wm") == ["mri/wm.mgz"]
    paint(work / "mri/ribbon.mgz")
    with pytest.raises(ValueError, match="outside"):
        check_edits(baseline, work, "s03", "wm")


def test_resampling_and_noop_are_rejected(tmp_path):
    from network_fmri.surface_corrections import check_edits
    import shutil
    baseline = reconstruction(tmp_path / "baseline")
    work = tmp_path / "work/sub-s03"
    shutil.copytree(baseline, work)
    with pytest.raises(ValueError, match="no edits"):
        check_edits(baseline, work, "s03", "wm")
    paint(work / "mri/wm.mgz", affine=np.diag([2, 2, 2, 1]))
    with pytest.raises(ValueError, match="grid"):
        check_edits(baseline, work, "s03", "wm")


def test_pial_edit_can_add_manedit_file(tmp_path):
    from network_fmri.surface_corrections import check_edits
    import shutil
    baseline = reconstruction(tmp_path / "baseline")
    work = tmp_path / "work/sub-s03"
    shutil.copytree(baseline, work)
    shutil.copyfile(work / "mri/brain.finalsurfs.mgz", work / "mri/brain.finalsurfs.manedit.mgz")
    paint(work / "mri/brain.finalsurfs.manedit.mgz")
    assert check_edits(baseline, work, "s03", "pial") == ["mri/brain.finalsurfs.manedit.mgz"]


def correction_input(tmp_path, kind="wm"):
    from tests.test_freesurfer_app import anatomy
    from network_fmri.freesurfer_app import _sha256
    bids = tmp_path / "bids"
    t1, t2 = anatomy(bids), anatomy(bids, "T2w", "04")
    inputs = tmp_path / "edits"
    subject = reconstruction(inputs / "subjects")
    record = {"subject": "sub-s03", "kind": kind, "build": "freesurfer-8.2.0",
              "inputs": [{"path": p.relative_to(bids).as_posix(), "sha256": _sha256(p)} for p in (t1, t2)],
              "inventory": reconstruction_inventory(subject, "s03"), "reviewer": "LB", "reason": "test"}
    (inputs / "code").mkdir()
    (inputs / "code/sub-s03.json").write_text(json.dumps(record))
    for path in subject.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    return bids, inputs


@pytest.mark.parametrize("kind,flags", [("wm", ["-autorecon2-wm", "-autorecon3"]),
                                      ("pial", ["-autorecon-pial"]),
                                      ("wm-pial", ["-autorecon2-wm", "-autorecon3"])])
def test_restart_preserves_t2_and_records_outputs(tmp_path, kind, flags):
    from network_fmri.freesurfer_app import run_correction
    bids, inputs = correction_input(tmp_path, kind)
    calls = []
    def runner(cmd, **kwargs):
        calls.append(cmd)
        (tmp_path / "out/subjects/sub-s03/scripts/recon-all.done").write_text("corrected")
    receipt = run_correction(bids, tmp_path / "out", "s03", corrections=inputs,
                             build="freesurfer-8.2.0", runner=runner)
    assert all(flag in calls[0] for flag in flags)
    assert "-T2pial" in calls[0] and "-i" not in calls[0]
    assert json.loads(receipt.read_text())["status"] == "success"
    assert (inputs / "subjects/sub-s03/scripts/recon-all.done").read_text() == "finished"
    assert not (inputs / "subjects/sub-s03/mri/wm.mgz").stat().st_mode & 0o200
    assert (tmp_path / "out/subjects/sub-s03/mri/wm.mgz").stat().st_mode & 0o200


def test_unchanged_subject_is_copied_without_rerun(tmp_path):
    from network_fmri.freesurfer_app import run_correction
    bids, inputs = correction_input(tmp_path, "none")
    receipt = run_correction(bids, tmp_path / "out", "s03", corrections=inputs,
                            build="freesurfer-8.2.0", runner=lambda *a, **k: pytest.fail("reran"))
    assert json.loads(receipt.read_text())["command"] == []


def test_copy_of_annex_files_is_writable_without_unlocking_original(tmp_path):
    from network_fmri.freesurfer_app import copy_reconstruction
    source = tmp_path / "source"
    source.mkdir()
    annex = tmp_path / "annex-object"
    annex.write_text("locked")
    annex.chmod(0o444)
    (source / "wm.mgz").symlink_to(annex)
    target = tmp_path / "edit"
    copy_reconstruction(source, target)
    assert not (target / "wm.mgz").is_symlink()
    assert (target / "wm.mgz").stat().st_mode & 0o200
    assert not annex.stat().st_mode & 0o200
    (target / "wm.mgz").write_text("edited")
    assert annex.read_text() == "locked"


def test_failed_restart_cannot_reuse_old_completion_marker(tmp_path):
    from network_fmri.freesurfer_app import run_correction
    bids, inputs = correction_input(tmp_path)
    def runner(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)
    with pytest.raises(subprocess.CalledProcessError):
        run_correction(bids, tmp_path / "out", "s03", corrections=inputs,
                       build="freesurfer-8.2.0", runner=runner)
    assert not (tmp_path / "out/subjects/sub-s03/scripts/recon-all.done").exists()
    assert json.loads((tmp_path / "out/code/sub-s03_reconstruction.json").read_text())["status"] == "failed"


def test_changed_input_or_build_is_rejected(tmp_path):
    from network_fmri.freesurfer_app import run_correction
    bids, inputs = correction_input(tmp_path)
    with pytest.raises(ValueError, match="build"):
        run_correction(bids, tmp_path / "out", "s03", corrections=inputs, build="other-8.2.0")
    (inputs / "subjects/sub-s03/mri/wm.mgz").chmod(0o644)
    paint(inputs / "subjects/sub-s03/mri/wm.mgz")
    with pytest.raises(ValueError, match="inventory"):
        run_correction(bids, tmp_path / "out", "s03", corrections=inputs, build="freesurfer-8.2.0")


def test_processing_selects_corrected_campaign_for_anatomy_and_fmriprep(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tests.test_processing import configuration, table
    from network_fmri.processing import ProcessingManager
    config = configuration(tmp_path)
    state = {"phase": "ready", "campaign": "network-v1-edit1"}
    monkeypatch.setattr("network_fmri.surface_corrections.correction_state", lambda *a, **k: state)
    calls = []
    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        apps = config.mechababs.apps if kwargs["env"]["MECHABABS_CAMPAIGN"] == "network-v1" else config.mechababs.apps[1:]
        return SimpleNamespace(stdout=table([
            {"source_dataset": "sourcedata/raw", "app": a.file.stem,
             "state": "merged" if a.name != "fmriprep" else "not started", "jobs": ""} for a in apps],
            ("source_dataset", "app", "state", "jobs")))
    stages = ProcessingManager(config, runner=runner).plan()
    assert stages[0].project.endswith("+network-v1")
    assert all(s.project.endswith("+network-v1-edit1") for s in stages[1:])


def test_pending_edits_block_fmriprep_even_with_old_approval(tmp_path, monkeypatch):
    from tests.test_processing import configuration
    from network_fmri.processing import require_stage_gate
    monkeypatch.setattr("network_fmri.surface_corrections.correction_state", lambda *a, **k: {"phase": "editing"})
    monkeypatch.setattr("network_fmri.pipeline.require_committed_surface_approval", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="correction"):
        require_stage_gate(configuration(tmp_path), "fmriprep")


def test_fresh_review_archives_old_approval_and_clears_reviewers(tmp_path):
    from types import SimpleNamespace
    from network_fmri.surface_corrections import refresh_surface_review
    from network_fmri.qa.freesurfer import generate_surface_review, REQUIRED_OUTPUTS
    from network_fmri.milestones import receipt_path
    config = SimpleNamespace(subjects=("s03",), mechababs=SimpleNamespace(study_dir=tmp_path / "study"))
    for name in ("old", "new"):
        for relative in REQUIRED_OUTPUTS:
            path = tmp_path / name / "sub-s03" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
    result = generate_surface_review(config, tmp_path / "old")
    manifest = result.outputs[0]
    original = manifest.read_bytes()
    approval = receipt_path(config.mechababs.study_dir, "surface-review-approved")
    approval.parent.mkdir(parents=True)
    approval.write_text('{"status":"success"}')
    state = {"campaign": "pilot-edit1", "phase": "ready", "baseline": str(tmp_path / "old")}
    result = refresh_surface_review(config, tmp_path / "new", state)
    assert manifest.read_bytes() != original
    assert "\tno\t\t\t" in manifest.read_text()
    assert not approval.exists()
    assert (manifest.parent / "surface-review-history/pilot-edit1/surface_review.tsv").read_bytes() == original
    assert refresh_surface_review(config, tmp_path / "new", state) is None


@pytest.mark.parametrize("failure", ["init", "add"])
def test_checkout_submit_and_campaign_handoff_with_real_git(tmp_path, monkeypatch, failure):
    """Use real commits/archives; replace only DataLad and the unavailable scheduler."""
    from dataclasses import replace
    from types import SimpleNamespace
    import shutil
    import zipfile
    import yaml
    from tests.test_processing import configuration, table
    from tests.test_mriqc_evidence import init, save, git
    from tests.test_freesurfer_app import anatomy
    from network_fmri.config import MechaBABSAppConfig
    from network_fmri.freesurfer_app import _sha256
    from network_fmri.processing import ProcessingManager
    from network_fmri.qa.freesurfer import REQUIRED_OUTPUTS
    from network_fmri.surface_corrections import checkout_corrections, submit_corrections, correction_state, cancel_corrections

    config = configuration(tmp_path)
    config.subjects = ("s03",)
    config.paths.freesurfer_license = tmp_path / "license.txt"
    config.mechababs = replace(config.mechababs, apps=tuple(
        MechaBABSAppConfig(name, Path(app + ".yaml")) for name, app in
        [("mriqc", "MRIQC-24.0.2"), ("anatomical", "FreeSurfer-8.2.0"), ("fmriprep", "fMRIPrep-25.2.5+full")]))
    study, raw = config.mechababs.study_dir, config.paths.bids_dir
    init(study, "study")
    init(raw, "raw")
    from network_fmri.study import StudyManager
    for kind, relative, content in StudyManager(config.mechababs, raw, tmp_path / "license.txt")._rendered_configs():
        path = config.mechababs.campaign_dir / ("bids-app-configs" if kind == "apps" else kind) / relative.name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (study / "sourcedata").mkdir()
    t1 = anatomy(raw)
    raw_commit = save(raw)
    save(study)
    source = study / "derivatives/FreeSurfer-8.2.0+network-v1"
    init(source, "original-surfaces")
    git(source, "-c", "protocol.file.allow=always", "clone", "-q", str(raw), "sourcedata/raw")
    git(source, "update-index", "--add", "--cacheinfo", f"160000,{raw_commit},sourcedata/raw")
    root = reconstruction(tmp_path / "reconstruction")
    for relative in REQUIRED_OUTPUTS:
        path = root / relative
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("output")
    record = {"subject": "sub-s03", "build": "freesurfer-8.2.0", "status": "success",
              "inputs": [{"path": t1.relative_to(raw).as_posix(), "sha256": _sha256(t1)}]}
    with zipfile.ZipFile(source / "sub-s03_FreeSurfer-8.2.0.zip", "w") as archive:
        for file in root.rglob("*"):
            if file.is_file():
                archive.write(file, "FreeSurfer-8.2.0/subjects/sub-s03/" + file.relative_to(root).as_posix())
        archive.writestr("FreeSurfer-8.2.0/code/sub-s03_reconstruction.json", json.dumps(record))
    original_commit = save(source)
    git(study, "update-index", "--add", "--cacheinfo", f"160000,{original_commit},{source.relative_to(study)}")
    save(study)
    captured = {}
    corrected_merged = False
    registered = False
    fail_add = failure == "add"
    init_calls = []
    def runner(command, **kwargs):
        nonlocal registered, fail_add
        command = tuple(map(str, command))
        if command[0].endswith("/mechababs"):
            label = kwargs["env"]["MECHABABS_CAMPAIGN"]
            if command[1] == "add-dataset":
                statefile = f".mechababs/campaigns/{label}/sourcedata+derivatives.tsv"
                if git(study, "status", "--porcelain", "--", statefile):
                    raise RuntimeError("upstream requires a committed campaign statefile")
                if fail_add:
                    fail_add = False
                    raise subprocess.CalledProcessError(1, command)
                registered = True
                return SimpleNamespace(stdout="registered")
            if label != "network-v1" and not registered:
                return SimpleNamespace(stdout="")
            apps = config.mechababs.apps if label == "network-v1" else config.mechababs.apps[1:]
            rows = [{"source_dataset": "sourcedata/raw", "app": a.file.stem, "jobs": "",
                     "state": "merged" if (label == "network-v1" or corrected_merged) and a.name != "fmriprep" else "not started"} for a in apps]
            return SimpleNamespace(stdout=table(rows, ("source_dataset", "app", "state", "jobs")))
        if command[0] == "uvx":
            if "update-env" in command:
                return SimpleNamespace(stdout="environment ready")
            init_calls.append(command)
            campaign = study / ".mechababs/campaigns/network-v1-edit1"
            cluster = Path(command[command.index("--cluster") + 1])
            (campaign / "clusters").mkdir(parents=True)
            shutil.copyfile(cluster, campaign / "clusters" / cluster.name)
            (campaign / "sourcedata+derivatives.tsv").write_text("source_dataset\tapp_config\n")
            for item in command[command.index("--apps") + 1].split(","):
                item = Path(item)
                target = campaign / "bids-app-configs" / item.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(item, target)
                captured[item.stem] = yaml.safe_load(item.read_text())
            if failure == "init":
                raise subprocess.CalledProcessError(1, command)
            save(study)
            return SimpleNamespace(stdout="")
        if command[0] == "datalad":
            if command[1] == "create":
                init(Path(command[-1]), "created")
            elif command[1] == "save":
                dataset = Path(command[command.index("-d") + 1])
                target = Path(command[-1])
                if target.is_dir() and target != dataset and (target / ".git").exists():
                    git(dataset, "update-index", "--add", "--cacheinfo",
                        f"160000,{git(target, 'rev-parse', 'HEAD')},{target.relative_to(dataset)}")
                save(dataset)
            return SimpleNamespace(stdout="")
        return subprocess.run(command, **kwargs)
    work = checkout_corrections(config, tmp_path / "edits", ["s03"], "wm", "LB", "White matter", runner=runner)
    assert correction_state(config, runner=runner)["phase"] == "editing"
    cancel_corrections(config, runner=runner)
    assert correction_state(config, runner=runner) is None
    assert work.is_dir()
    work = checkout_corrections(config, tmp_path / "edits2", ["s03"], "wm", "LB", "White matter", runner=runner)
    paint(work / "sub-s03/mri/wm.mgz")
    with pytest.raises(subprocess.CalledProcessError):
        submit_corrections(config, runner=runner)
    assert correction_state(config, runner=runner)["phase"] == "initializing"
    result = submit_corrections(config, runner=runner)
    assert len(init_calls) == 1
    assert result["campaign"] == "network-v1-edit1"
    assert correction_state(config, runner=runner)["phase"] == "ready"
    corrected = captured["FreeSurfer-8.2.0"]
    assert "depends_on" not in corrected["mechababs"]
    assert "FreeSurferEdits" in corrected["input_datasets"]
    assert captured["fMRIPrep-25.2.5+full"]["mechababs"]["depends_on"] == "FreeSurfer-8.2.0"
    assert git(source, "rev-parse", "HEAD") == original_commit
    stages = ProcessingManager(config, runner=runner).plan()
    assert stages[1].state == "ready" and stages[2].state == "blocked"
    # Mimic the compute node and merged BABS result, then use the real extraction/gate.
    from network_fmri.freesurfer_app import run_correction
    from network_fmri.surface_evidence import prepare_surface_evidence
    edit_dataset = study / "sourcedata/freesurfer-edits-1"
    with zipfile.ZipFile(edit_dataset / "sub-s03_FreeSurferEdits.zip") as archive:
        archive.extractall(tmp_path / "job-input")
    output = tmp_path / "job-output/FreeSurfer-8.2.0"
    def recon(cmd, **kwargs):
        (output / "subjects/sub-s03/scripts/recon-all.done").write_text("corrected")
    run_correction(raw, output, "s03", corrections=tmp_path / "job-input/FreeSurferEdits",
                   build="freesurfer-8.2.0", runner=recon)
    corrected = study / stages[1].project
    init(corrected, "corrected-surfaces")
    for path, origin in (("sourcedata/raw", raw), ("sourcedata/FreeSurferEdits", edit_dataset)):
        git(corrected, "-c", "protocol.file.allow=always", "clone", "-q", str(origin), path)
        git(corrected, "update-index", "--add", "--cacheinfo", f"160000,{git(origin, 'rev-parse', 'HEAD')},{path}")
    with zipfile.ZipFile(corrected / "sub-s03_FreeSurfer-8.2.0.zip", "w") as archive:
        for file in output.rglob("*"):
            if file.is_file():
                archive.write(file, "FreeSurfer-8.2.0/" + file.relative_to(output).as_posix())
    save(corrected)
    git(study, "update-index", "--add", "--cacheinfo",
        f"160000,{git(corrected, 'rev-parse', 'HEAD')},{corrected.relative_to(study)}")
    save(study)
    corrected_merged = True
    evidence = prepare_surface_evidence(config, runner=runner)
    assert evidence.parent.name.endswith("+network-v1-edit1+review")
    assert nib.load(evidence / "sub-s03/mri/wm.mgz").get_fdata()[1, 1, 1] == 255
    # Editing a committed campaign input never silently changes the selected rerun.
    (edit_dataset / "changed.txt").write_text("unexpected")
    with pytest.raises(RuntimeError, match="dirty"):
        ProcessingManager(config, runner=runner).plan()
