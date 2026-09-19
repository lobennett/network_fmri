"""Synthetic end-to-end coverage for the approval-gated pilot pipeline."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import network_fmri.pipeline as pipeline
from network_fmri.config import (
    BehaviorSource, ContainerConfig, SlurmConfig, WorkflowConfig, WorkflowPaths,
)
from network_fmri.milestones import receipt_path
from network_fmri.qa.fmriprep import (
    _native_echo_preprocessed_bold_path, _standard_preprocessed_bold_paths,
)
from network_fmri.stages import behavior


def _config(tmp_path: Path) -> WorkflowConfig:
    subjects = tuple(f"s{number}" for number in range(1, 47))
    subjects_file = tmp_path / "subjects-46.txt"
    subjects_file.write_text("\n".join(subjects) + "\n")
    behavior = tmp_path / "behavior"
    behavior.mkdir()
    (behavior / "raw.csv").write_text("trial\n1\n")
    return WorkflowConfig(
        paths=WorkflowPaths(
            bids_dir=tmp_path / "bids", parts_dir=tmp_path / "parts",
            work_dir=tmp_path / "work", log_dir=tmp_path / "logs",
            templateflow_dir=tmp_path / "templateflow",
            freesurfer_license=tmp_path / "license.txt",
        ),
        subjects_file=subjects_file, subjects=subjects, flywheel_project="russpold/r01network",
        behavior=BehaviorSource(behavior, "a" * 40),
        mriqc=ContainerConfig(tmp_path / "mriqc.sif", "24.0.2"),
        fmriprep=ContainerConfig(tmp_path / "fmriprep.sif", "25.2.5"),
        # Keep the real trimming implementation in-process for this acceptance test.
        slurm=SlurmConfig("normal", 1, 32, 720, 1),
    )


class _SyntheticImage:
    def __init__(self, volumes: int) -> None:
        self.volumes = volumes
        self.shape = (2, 2, 2, volumes)

    @property
    def slicer(self):
        return self

    def __getitem__(self, key):
        return _SyntheticImage(self.volumes - int(key[3].start or 0))


class _SyntheticNibabel:
    @staticmethod
    def load(path: str) -> _SyntheticImage:
        return _SyntheticImage(int(Path(path).read_text().split("=", 1)[1]))

    @staticmethod
    def save(image: _SyntheticImage, path: str) -> None:
        Path(path).write_text(f"volumes={image.volumes}")


class FakeApplications:
    """Simulate process boundaries while real stage code changes a tiny BIDS tree."""

    def __init__(self, config: WorkflowConfig) -> None:
        self.config = config
        self.bids_dir = config.paths.bids_dir
        self.calls: list[tuple[str, ...]] = []
        self.milestones: list[str] = []
        self.committed: dict[str, bytes] = {}
        self.head = "0" * 40
        self.next_job = 1

    def __call__(self, command, **_kwargs):
        command = tuple(map(str, command))
        self.calls.append(command)
        if command[0] == "sbatch":
            job = str(self.next_job)
            self.next_job += 1
            return SimpleNamespace(stdout=f"{job};sherlock\n")
        if command[0] == "network-fw2bids":
            Path(command[command.index("--output") + 1]).mkdir(parents=True)
            return SimpleNamespace(stdout="")
        if command[:3] == (sys.executable, "-m", "network_fw2bids._assembly"):
            self._assemble(Path(command[command.index("--output") + 1]))
            return SimpleNamespace(stdout="")
        if command[:2] == ("network-events", "audit"):
            return SimpleNamespace(stdout="")
        if command[:2] == ("network-events", "create"):
            evidence = self.bids_dir / "sourcedata" / "events_qc"
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / "conversion_errors.tsv").write_text("scan\treason\n")
            return SimpleNamespace(stdout="")
        if command[0] == "nf-global-signal":
            Path(command[command.index("--out-tsv") + 1]).write_text("scan\tmean\nrun\t0\n")
            Path(command[command.index("--out-pdf") + 1]).write_bytes(b"%PDF")
            return SimpleNamespace(stdout="")
        if command[0] == "bids-validator":
            Path(command[command.index("--outfile") + 1]).write_text("{}\n")
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[:3] == ("network-qa", "decisions", "generate"):
            self._write_generated_manifest(Path(command[command.index("--output") + 1]))
            return SimpleNamespace(stdout="")
        if command[:3] == ("network-qa", "decisions", "approve"):
            manifest = Path(command[command.index("--manifest") + 1])
            manifest.with_suffix(".meta.json").write_text(json.dumps({"approved": True}) + "\n")
            return SimpleNamespace(stdout="")
        if command[:3] == ("network-qa", "decisions", "validate"):
            metadata = Path(command[command.index("--metadata") + 1])
            assert json.loads(metadata.read_text()) == {"approved": True}
            return SimpleNamespace(stdout="")
        if command[0] == "apptainer":
            self._run_container(command)
            return SimpleNamespace(stdout="")
        if command[:2] == ("datalad", "save"):
            self.milestones.append(command[command.index("-m") + 1])
            self.committed = {
                path.relative_to(self.bids_dir).as_posix(): path.read_bytes()
                for path in self.bids_dir.rglob("*") if path.is_file()
            }
            self.head = f"{len(self.milestones):040x}"
            return SimpleNamespace(stdout="")
        if command[:3] == ("git", "-C", str(self.config.behavior.source)):
            return self._behavior_git(command)
        if command[:4] == ("git", "-C", str(self.bids_dir), "rev-parse"):
            return SimpleNamespace(stdout=self.head + "\n")
        if command[:4] == ("git", "-C", str(self.bids_dir), "show"):
            return SimpleNamespace(stdout=self.committed[command[-1].split(":", 1)[1]])
        raise AssertionError(f"unexpected external command: {command}")

    def _behavior_git(self, command: tuple[str, ...]):
        if command[3:5] == ("rev-parse", "--verify"):
            return SimpleNamespace(stdout=self.config.behavior.commit + "\n")
        if command[3] == "status":
            return SimpleNamespace(stdout="")
        if command[3] == "ls-tree":
            return SimpleNamespace(stdout=b"raw.csv\0")
        raise AssertionError(f"unexpected behavioral Git command: {command}")

    def _assemble(self, destination: Path) -> None:
        session = destination / "sub-s7" / "ses-01"
        (session / "func").mkdir(parents=True)
        (session / "anat").mkdir()
        (session / "fmap").mkdir()
        (destination / "dataset_description.json").write_text(
            json.dumps({"Name": "synthetic", "BIDSVersion": "1.10.0"})
        )
        bold = session / "func" / "sub-s7_ses-01_task-rest_run-1_bold.nii.gz"
        bold.write_text("volumes=60")
        bold.with_name(bold.name.removesuffix(".nii.gz") + ".json").write_text(
            json.dumps({"RepetitionTime": 1.0, "NumVolumes": 60})
        )
        for suffix in ("T1w", "T2w"):
            (session / "anat" / f"sub-s7_ses-01_{suffix}.nii.gz").write_bytes(b"nii")
        fieldmap = session / "fmap" / "sub-s7_ses-01_fieldmap.nii.gz"
        fieldmap.write_bytes(b"nii")
        fieldmap.with_name(fieldmap.name.removesuffix(".nii.gz") + ".json").write_text("{}")

    def _run_container(self, command: tuple[str, ...]) -> None:
        subject = command[command.index("--participant-label") + 1] if "--participant-label" in command else None
        if str(self.config.mriqc.image) in command:
            if subject:
                self._write_mriqc_subject(subject)
            else:
                self._write_mriqc_group()
            return
        if str(self.config.fmriprep.image) in command and subject:
            self._write_fmriprep_subject(subject)
            return
        raise AssertionError(f"unexpected container command: {command}")

    def _write_mriqc_subject(self, subject: str) -> None:
        root = self.bids_dir / "derivatives" / "mriqc"
        root.mkdir(parents=True, exist_ok=True)
        (root / "dataset_description.json").write_text(json.dumps({"DatasetType": "derivative"}))
        for raw in (self.bids_dir / f"sub-{subject}").glob("ses-*/*/*.nii*"):
            suffix = raw.name.removesuffix(".nii.gz").rsplit("_", 1)[-1]
            if suffix not in {"bold", "T1w", "T2w"}:
                continue
            stem = raw.name.removesuffix(".nii.gz")
            target = root / raw.relative_to(self.bids_dir).with_name(stem + ".json")
            target.parent.mkdir(parents=True, exist_ok=True)
            value = {"provenance": {"settings": {"fd_thres": 0.5}}} if suffix == "bold" else {}
            target.write_text(json.dumps(value))
            target.with_suffix(".html").write_text("report")

    def _write_mriqc_group(self) -> None:
        root = self.bids_dir / "derivatives" / "mriqc"
        for suffix in ("bold", "T1w", "T2w"):
            (root / f"group_{suffix}.html").write_text("report")
            (root / f"group_{suffix}.tsv").write_text("metric\n0\n")

    def _write_fmriprep_subject(self, subject: str) -> None:
        root = self.bids_dir / "derivatives" / "fmriprep"
        root.mkdir(parents=True, exist_ok=True)
        (root / "dataset_description.json").write_text(json.dumps({"DatasetType": "derivative"}))
        (root / f"sub-{subject}.html").write_text("report")
        anat = root / f"sub-{subject}" / "anat"
        anat.mkdir(parents=True, exist_ok=True)
        (anat / f"sub-{subject}_desc-preproc_T1w.nii.gz").write_bytes(b"nii")
        for raw in (self.bids_dir / f"sub-{subject}").glob("ses-*/func/*_bold.nii*"):
            for output in _standard_preprocessed_bold_paths(root, self.bids_dir, raw):
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"nii")
            if "_echo-" in raw.name:
                output = _native_echo_preprocessed_bold_path(root, self.bids_dir, raw)
                output.write_bytes(b"nii")

    @staticmethod
    def _write_generated_manifest(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "record_type\tsubject\tsession\tdatatype\tsuffix\ttask\tacquisition\tdirection\trun\tdecision\n"
            "acquisition\tsub-s7\tses-01\tfunc\tbold\trest\t\t\t1\tkeep\n"
        )
        path.with_suffix(".meta.json").write_text(json.dumps({"approved": False}) + "\n")


def _stage(name: str, config_path: Path, apps: FakeApplications) -> None:
    args = [name, str(config_path), "--pilot-subject", "s7"]
    if name in {"fw2bids-array", "mriqc-array", "fmriprep-array"}:
        args.extend(("--array-index", "0"))
    assert pipeline.stage_main(args, runner=apps) == 0


def test_synthetic_pilot_executes_assembly_through_fmriprep(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config_path = tmp_path / "workflow.toml"
    config_path.write_text("synthetic")
    apps = FakeApplications(config)
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)
    monkeypatch.setitem(sys.modules, "nibabel", _SyntheticNibabel)
    # The upstream publisher is an integration boundary; use the same exclusive
    # directory move that the tiny synthetic tree needs without importing it.
    monkeypatch.setattr(behavior, "_publish_no_replace", lambda staged, destination: staged.replace(destination))

    assert pipeline.main(["submit", str(config_path), "--pilot-subject", "s7"], runner=apps) == 0
    initial = pipeline.read_record(pipeline.record_path(config))
    assert initial.pilot_subject == "s7"
    assert all("--pilot-subject s7" in " ".join(command) for command in initial.commands.values())

    for name in pipeline.STAGE_ORDER[:12]:
        _stage(name, config_path, apps)
    _stage("scan-decisions-approved", config_path, apps)

    receipt = json.loads(
        apps.committed[receipt_path(config.paths.bids_dir, "scan-decisions-approved").relative_to(config.paths.bids_dir).as_posix()]
    )
    manifest = config.paths.bids_dir / "code" / "network_fmri" / "scan_decisions.tsv"
    assert receipt["validation"]["manifest_sha256"] == pipeline._sha256(manifest.read_bytes())

    assert pipeline.main(
        ["submit", str(config_path), "--pilot-subject", "s7", "--resume"], runner=apps,
    ) == 0
    for name in pipeline.STAGE_ORDER[13:]:
        _stage(name, config_path, apps)

    assert apps.milestones == [
        "bids-assembled", "behavioral-sourcedata-ingested", "gs-pretrim",
        "dummy-volumes-trimmed", "bids-events-generated", "gs-posttrim",
        "b0-fieldmaps-linked", "bids-precuration-validated", "mriqc-complete",
        "scan-decisions-generated", "scan-decisions-approved", "mriqc-curated",
        "fmriprep-complete",
    ]
    bold_json = config.paths.bids_dir / "sub-s7" / "ses-01" / "func" / "sub-s7_ses-01_task-rest_run-1_bold.json"
    assert json.loads(bold_json.read_text())["NumberOfVolumesDiscardedByUser"] == 7
    assert (config.paths.bids_dir / "derivatives" / "fmriprep" / "sub-s7.html").is_file()


def test_pilot_selection_is_carried_to_workers_and_resume_is_bound_to_it(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config_path = tmp_path / "workflow.toml"
    config_path.write_text("synthetic")
    apps = FakeApplications(config)
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)

    pilot = pipeline.pilot_config(config, "s7")
    plan = pipeline.build_plan(pilot, config_path=config_path)
    assert all(job.subject_count == 1 for job in plan)
    assert all(job.command[-2:] == ("--pilot-subject", "s7") for job in plan)
    with pytest.raises(ValueError, match="matching one-subject roster"):
        pipeline.build_plan(config, config_path=config_path, pilot_subject="s7")

    assert pipeline.main(["submit", str(config_path), "--pilot-subject", "s7"], runner=apps) == 0
    saved = pipeline.read_record(pipeline.record_path(config))
    assert saved.pilot_subject == "s7"
    with pytest.raises(RuntimeError, match="different pilot selection"):
        pipeline.main(["submit", str(config_path), "--resume"], runner=apps)
    with pytest.raises(RuntimeError, match="different pilot selection"):
        pipeline.main(["submit", str(config_path), "--pilot-subject", "s8", "--resume"], runner=apps)


def test_source_contains_no_datalad_run():
    source = "\n".join(path.read_text() for path in Path("src").rglob("*.py"))
    assert "datalad run" not in source.lower()
