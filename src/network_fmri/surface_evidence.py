"""Extract merged standalone reconstructions for human inspection."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import zipfile

from network_fmri.mriqc import _git, _gitlink, _require_dataset, _sha256
from network_fmri.processing import ProcessingManager
from network_fmri.qa.freesurfer import REQUIRED_OUTPUTS
from network_fmri.stages import StageError
from network_fmri.surface_inventory import reconstruction_inventory

RECEIPT = "code/network_fmri/surface-evidence.json"


def extract_subject_archive(source: Path, subject: str, destination: Path) -> dict[str, str]:
    inventory = reconstruction_inventory(source, subject)
    if not set(REQUIRED_OUTPUTS).issubset(inventory):
        raise StageError(f"incomplete reconstruction for sub-{subject}")
    root = destination / f"sub-{subject}"
    if root.exists() or root.is_symlink():
        raise StageError("surface extraction destination already exists")
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            parts = PurePosixPath(info.filename).parts
            if f"sub-{subject}" not in parts or info.is_dir():
                continue
            relative = Path(*parts[parts.index(f"sub-{subject}") + 1:])
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst)
    if reconstruction_inventory(root, subject) != inventory:
        raise StageError("extracted reconstruction does not match archive")
    return inventory


def prepare_surface_evidence(config, *, runner=subprocess.run) -> Path:
    stages = ProcessingManager(config, runner=runner).plan()
    stage = next(s for s in stages if s.stage == "anatomical")
    if stage.state != "complete" or stage.application != "FreeSurfer-8.2.0":
        raise StageError("standalone FreeSurfer must be merged before surface review")
    study = config.mechababs.study_dir.resolve()
    source = study / stage.project
    _require_dataset(study, runner=runner)
    identity = _require_dataset(source, runner=runner)
    commit = _git(source, "rev-parse", "HEAD", runner=runner)
    if _gitlink(study, "HEAD", stage.project, runner=runner) != commit:
        raise StageError("FreeSurfer derivative is not registered at its merged commit")
    raw_commit = _gitlink(source, commit, "sourcedata/raw", runner=runner)
    if raw_commit != _git(config.paths.bids_dir, "rev-parse", "HEAD", runner=runner):
        raise StageError("FreeSurfer input differs from current curated BIDS")
    from network_fmri.surface_corrections import correction_state
    correction = correction_state(config, runner=runner)
    if correction and correction["phase"] == "ready":
        if _gitlink(source, commit, "sourcedata/FreeSurferEdits", runner=runner) != correction["input_commit"]:
            raise StageError("corrected reconstruction does not use the sealed edit inputs")
    tracked = _git(source, "ls-tree", "-r", "--name-only", commit, runner=runner).splitlines()
    archives = []
    for subject in config.subjects:
        matches = [p for p in tracked if "/" not in p and p.startswith(f"sub-{subject}_") and p.endswith(".zip")]
        if len(matches) != 1:
            raise StageError(f"expected one merged reconstruction for sub-{subject}")
        path = source / matches[0]
        if not path.is_file():
            runner(("datalad", "get", "-d", str(source), str(path)), check=True)
        archives.append((subject, path))
    destination = source.with_name(source.name + "+review")
    with tempfile.TemporaryDirectory(prefix=".surface-review-", dir=source.parent) as temporary:
        staging = Path(temporary)
        inventories = {subject: extract_subject_archive(path, subject, staging / "subjects")
                       for subject, path in archives}
        receipt = {"schema_version": 1, "source_dataset_id": identity,
                   "source_dataset_commit": commit, "source_project": stage.project,
                   "input_datalad_commit": raw_commit,
                   "archives": [{"path": path.name, "sha256": _sha256(path)} for _, path in archives],
                   "inventories": inventories}
        if destination.exists() or destination.is_symlink():
            _require_dataset(destination, runner=runner)
            review_commit = _git(destination, "rev-parse", "HEAD", runner=runner)
            if _gitlink(study, "HEAD", destination.relative_to(study).as_posix(), runner=runner) != review_commit:
                raise StageError("surface evidence is not registered at its current commit")
            if json.loads((destination / RECEIPT).read_text()) != receipt:
                raise StageError("surface evidence changed; previous review preserved")
            for subject, expected in inventories.items():
                if reconstruction_inventory(destination / "subjects" / f"sub-{subject}", subject) != expected:
                    raise StageError("published surface evidence changed")
            return destination / "subjects"
        if _git(source, "rev-parse", "HEAD", runner=runner) != commit:
            raise StageError("FreeSurfer source changed during evidence extraction")
        (staging / RECEIPT).parent.mkdir(parents=True)
        (staging / RECEIPT).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        (staging / "dataset_description.json").write_text(json.dumps({
            "Name": "FreeSurfer 8.2.0 review evidence", "BIDSVersion": "1.10.0",
            "DatasetType": "derivative", "GeneratedBy": [{"Name": "network_fmri"}],
        }) + "\n")
        staging.rename(destination)
    runner(("datalad", "create", "--force", str(destination)), check=True)
    runner(("datalad", "save", "-d", str(destination), "-m", "Extract standalone FreeSurfer evidence"), check=True)
    runner(("datalad", "save", "-d", str(study), "-m", "Register surface evidence", str(destination)), check=True)
    return destination / "subjects"
