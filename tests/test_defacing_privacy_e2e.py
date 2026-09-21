"""Acceptance coverage for the boundary that keeps raw anatomy off persistent storage."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import nibabel as nib
import numpy as np

# Task 5 exercises the sibling implementation before its reviewed Git revision can
# be published and resolved by uv.  The final dependency pin below names that exact
# revision; this source path keeps the acceptance test on the same implementation.
UPSTREAM_SRC = (
    Path(__file__).resolve().parents[4]
    / "network_fw2bids"
    / ".worktrees"
    / "integrated-defacing"
    / "src"
)
if str(UPSTREAM_SRC) not in sys.path:
    sys.path.insert(0, str(UPSTREAM_SRC))

from network_fw2bids.conversion import DicomConverter
from network_fw2bids.defacing import DefaceConfig
from network_fw2bids.planning import ArchivePlan
from network_fmri.config import (
    BehaviorSource,
    ContainerConfig,
    SlurmConfig,
    VerifiedContainerConfig,
    WorkflowConfig,
    WorkflowPaths,
)
from network_fmri.pipeline import save_stage_result
from network_fmri.stages.assembly import assemble_dataset


class _Acquisition:
    label = "Sag_MPRAGE_T1"

    def download_file(self, name: str, destination: str) -> None:
        assert name == "scan.dicom.zip"
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr("scan.dcm", b"synthetic dicom")


class _DicomFile:
    name = "scan.dicom.zip"


class _ConversionBoundary:
    """Fake only dcm2niix and PyDeface while preserving the local file contract."""

    def __init__(self) -> None:
        self.undefaced_hash: str | None = None

    def __call__(self, command: list[str], **_kwargs: object) -> None:
        if command[0] == "dcm2niix":
            output = Path(command[command.index("-o") + 1])
            output.mkdir(parents=True, exist_ok=True)
            data = np.arange(60, dtype=np.float32).reshape((3, 4, 5))
            image = nib.Nifti1Image(data, np.diag((1.5, 1.25, 2.0, 1.0)))
            path = output / "converted.nii.gz"
            nib.save(image, path)
            self.undefaced_hash = _sha256(path)
            (output / "converted.json").write_text(json.dumps({"Modality": "MR"}))
            return
        assert command[:2] == ["apptainer", "exec"]
        root = Path(command[command.index("--bind") + 1].split(":", 1)[0])
        source = root / command[command.index("pydeface") + 1].removeprefix("/work/")
        output = root / command[command.index("--outfile") + 1].removeprefix("/work/")
        image = nib.load(source)
        data = np.asanyarray(image.dataobj).copy()
        data[0, 0, 0] = -999.0
        nib.save(nib.Nifti1Image(data, image.affine, image.header), output)


class _DataLadBoundary:
    """Fake DataLad while running the real local assembly subprocess."""

    def __init__(self) -> None:
        self.head = "0" * 40

    def __call__(self, command: list[str], **kwargs: object):
        command = [str(item) for item in command]
        if command[:3] == [sys.executable, "-m", "network_fw2bids._assembly"]:
            environment = dict(os.environ)
            environment["PYTHONPATH"] = os.pathsep.join(
                [str(UPSTREAM_SRC), str(Path(__file__).resolve().parents[1] / "src")]
            )
            return subprocess.run(command, env=environment, **kwargs)
        if command[:2] in (["datalad", "create"], ["datalad", "save"]):
            if command[:2] == ["datalad", "save"]:
                self.head = "1" * 40
            return subprocess.CompletedProcess(command, 0, stdout="")
        if command[:4] == ["git", "-C", command[2], "rev-parse"]:
            return subprocess.CompletedProcess(command, 0, stdout=self.head + "\n")
        raise AssertionError(f"unexpected external command: {command}")


@dataclass
class _Runtime:
    root: Path
    config: WorkflowConfig
    node_tmp: Path
    converter: _ConversionBoundary
    datalad: _DataLadBoundary

    @property
    def part(self) -> Path:
        return self.config.paths.parts_dir / "s03"

    def published_anatomy_is_defaced(self) -> bool:
        images = list(self.config.paths.bids_dir.glob("sub-s03/ses-01/anat/*_T1w.nii.gz"))
        return len(images) == 1 and json.loads(
            images[0].with_name(images[0].name[:-7] + ".json").read_text()
        ).get("Defaced") is True

    def receipts_match_all_anatomy(self) -> bool:
        receipt = json.loads(
            (self.config.paths.bids_dir / "code/network_fw2bids/defacing/sub-s03.json").read_text()
        )
        anatomy = {
            path.relative_to(self.config.paths.bids_dir).as_posix()
            for path in self.config.paths.bids_dir.glob("sub-s03/ses-01/anat/*_T?w.nii.gz")
        }
        return {item["path"] for item in receipt["images"]} == anatomy

    def persistent_tree_contains_undefaced_fixture_hash(self) -> bool:
        assert self.converter.undefaced_hash is not None
        return any(
            _sha256(path) == self.converter.undefaced_hash
            for path in self.root.rglob("*.nii.gz")
            if self.node_tmp not in path.parents
        )

    def milestone(self, stage: str) -> dict[str, object]:
        return json.loads(
            (self.config.paths.bids_dir / "code/network_fmri/milestones" / f"{stage}.json").read_text()
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _synthetic_runtime(tmp_path: Path) -> _Runtime:
    node_tmp = tmp_path / "node-tmp"
    node_tmp.mkdir()
    pydeface = tmp_path / "pydeface-2.1.0.sif"
    pydeface.write_bytes(b"pinned pydeface image")
    behavior = tmp_path / "behavior"
    behavior.mkdir()
    config = WorkflowConfig(
        paths=WorkflowPaths(
            bids_dir=tmp_path / "bids",
            parts_dir=tmp_path / "parts",
            work_dir=tmp_path / "work",
            log_dir=tmp_path / "logs",
            templateflow_dir=tmp_path / "templateflow",
            freesurfer_license=tmp_path / "license.txt",
        ),
        subjects_file=tmp_path / "subjects.txt",
        subjects=("s03",),
        flywheel_project="russpold/r01network",
        behavior=BehaviorSource(behavior, "a" * 40),
        mriqc=ContainerConfig(tmp_path / "mriqc.sif", "24.0.2"),
        fmriprep=ContainerConfig(tmp_path / "fmriprep.sif", "25.2.5"),
        pydeface=VerifiedContainerConfig(pydeface, "2.1.0", _sha256(pydeface)),
        slurm=SlurmConfig("normal", 1, 32, 720, 1),
    )
    return _Runtime(tmp_path, config, node_tmp, _ConversionBoundary(), _DataLadBoundary())


def _run_conversion_and_assembly(runtime: _Runtime) -> None:
    plan = ArchivePlan(
        acquisition=_Acquisition(),
        dicom_file=_DicomFile(),
        relative_prefix=Path("sub-s03/ses-01/anat/sub-s03_ses-01_T1w"),
        modality="anat",
    )
    DicomConverter(
        runner=runtime.converter,
        deface_config=DefaceConfig(
            runtime.config.pydeface.image,
            runtime.config.pydeface.version,
            runtime.config.pydeface.sha256,
        ),
    ).convert([plan], runtime.part, runtime.config.flywheel_project)
    result = assemble_dataset(runtime.config, runner=runtime.datalad)
    save_stage_result(runtime.config.paths.bids_dir, result, config=runtime.config, runner=runtime.datalad)


def test_persistent_pipeline_never_contains_undefaced_anatomy(tmp_path, monkeypatch):
    """Changing safe copy, receipts, or assembly must fail this privacy boundary."""

    runtime = _synthetic_runtime(tmp_path)
    monkeypatch.setenv("SLURM_TMPDIR", str(runtime.node_tmp))

    _run_conversion_and_assembly(runtime)

    assert runtime.published_anatomy_is_defaced()
    assert runtime.receipts_match_all_anatomy()
    assert not list(runtime.node_tmp.iterdir())
    assert not runtime.persistent_tree_contains_undefaced_fixture_hash()
    assert runtime.milestone("bids-assembled")["versions"]["pydeface"]["version"] == "2.1.0"
