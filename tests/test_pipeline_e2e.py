"""Synthetic acceptance coverage for submission, approval, and resume."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import network_fmri.pipeline as pipeline
from network_fmri.config import (
    BehaviorSource, ContainerConfig, SlurmConfig, WorkflowConfig, WorkflowPaths,
)
from network_fmri.milestones import receipt_path
from network_fmri.stages.decisions import generate_decisions, validate_decisions


def _config(tmp_path: Path) -> WorkflowConfig:
    subjects = tuple(f"s{number}" for number in range(1, 47))
    subjects_file = tmp_path / "subjects-46.txt"
    subjects_file.write_text("\n".join(subjects) + "\n")
    return WorkflowConfig(
        paths=WorkflowPaths(
            bids_dir=tmp_path / "bids", parts_dir=tmp_path / "parts",
            work_dir=tmp_path / "work", log_dir=tmp_path / "logs",
            templateflow_dir=tmp_path / "templateflow",
            freesurfer_license=tmp_path / "license.txt",
        ),
        subjects_file=subjects_file,
        subjects=subjects,
        flywheel_project="russpold/r01network",
        behavior=BehaviorSource(tmp_path / "behavior", "a" * 40),
        mriqc=ContainerConfig(tmp_path / "mriqc.sif", "24.0.2"),
        fmriprep=ContainerConfig(tmp_path / "fmriprep.sif", "25.2.5"),
        slurm=SlurmConfig("normal", 8, 32, 720, 4),
    )


class FakeApplications:
    """A scheduler, DataLad, Git, and Network QA boundary for an in-memory pilot."""

    def __init__(self, bids_dir: Path) -> None:
        self.bids_dir = bids_dir
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
        if command[:3] == ("network-qa", "decisions", "generate"):
            self._write_generated_manifest(Path(command[command.index("--output") + 1]))
            return SimpleNamespace(stdout="")
        if command[:3] == ("network-qa", "decisions", "approve"):
            manifest = Path(command[command.index("--manifest") + 1])
            metadata = Path(command[command.index("--metadata") + 1])
            assert manifest.is_file()
            metadata.write_text(json.dumps({"approved": True}) + "\n")
            return SimpleNamespace(stdout="")
        if command[:3] == ("network-qa", "decisions", "validate"):
            metadata = Path(command[command.index("--metadata") + 1])
            assert json.loads(metadata.read_text()) == {"approved": True}
            return SimpleNamespace(stdout="")
        if command[:2] == ("datalad", "save"):
            self.milestones.append(command[command.index("-m") + 1])
            self.committed = {
                path.relative_to(self.bids_dir).as_posix(): path.read_bytes()
                for path in self.bids_dir.rglob("*") if path.is_file()
            }
            self.head = f"{len(self.milestones):040x}"
            return SimpleNamespace(stdout="")
        if command[:4] == ("git", "-C", str(self.bids_dir), "rev-parse"):
            return SimpleNamespace(stdout=self.head + "\n")
        if command[:4] == ("git", "-C", str(self.bids_dir), "show"):
            target = command[-1].split(":", 1)[1]
            return SimpleNamespace(stdout=self.committed[target])
        raise AssertionError(f"unexpected external command: {command}")

    @staticmethod
    def _write_generated_manifest(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "record_type\tsubject\tsession\tdatatype\tsuffix\ttask\tacquisition\tdirection\trun\tdecision\n"
            "acquisition\tsub-s1\tses-01\tfunc\tbold\trest\t\t\t1\tkeep\n"
        )
        path.with_suffix(".meta.json").write_text(json.dumps({"approved": False}) + "\n")


def test_synthetic_pipeline_stops_seals_approval_and_resumes(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.paths.bids_dir.mkdir()
    config_path = tmp_path / "workflow.toml"
    config_path.write_text("synthetic")
    apps = FakeApplications(config.paths.bids_dir)
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)

    assert pipeline.main(["submit", str(config_path)], runner=apps) == 0
    initial = pipeline.read_record(pipeline.record_path(config))
    initial_submitted = [
        command[command.index("--job-name") + 1].removeprefix("network-fmri-")
        for command in apps.calls if command[0] == "sbatch"
    ]
    assert initial_submitted == list(pipeline.STAGE_ORDER[:12])
    assert "scan-decisions-approved" not in initial.jobs

    generated = generate_decisions(config.paths.bids_dir, apps)
    pipeline.save_stage_result(config.paths.bids_dir, generated, config=config, runner=apps)
    approved = validate_decisions(config.paths.bids_dir, apps)
    pipeline.save_stage_result(config.paths.bids_dir, approved, config=config, runner=apps)

    receipt = json.loads(
        apps.committed[receipt_path(config.paths.bids_dir, approved.name).relative_to(config.paths.bids_dir).as_posix()]
    )
    assert apps.milestones == ["scan-decisions-generated", "scan-decisions-approved"]
    assert receipt["validation"] == {
        "manifest_sha256": pipeline._sha256(approved.outputs[0].read_bytes()),
        "metadata_sha256": pipeline._sha256(approved.outputs[1].read_bytes()),
    }

    assert pipeline.main(["submit", str(config_path), "--resume"], runner=apps) == 0
    resumed = pipeline.read_record(pipeline.record_path(config))
    assert set(resumed.jobs) == set(pipeline.STAGE_ORDER) - {"scan-decisions-approved"}
    all_submitted = [
        command[command.index("--job-name") + 1].removeprefix("network-fmri-")
        for command in apps.calls if command[0] == "sbatch"
    ]
    assert all_submitted == list(pipeline.STAGE_ORDER[:12]) + list(pipeline.STAGE_ORDER[13:])
    assert any(command[:3] == ("network-qa", "decisions", "validate") for command in apps.calls)
    assert any(command[:4] == ("git", "-C", str(config.paths.bids_dir), "show") for command in apps.calls)


def test_pilot_subject_is_derived_from_the_reviewed_full_roster(tmp_path):
    config = _config(tmp_path)

    pilot = pipeline.pilot_config(config, "s7")

    assert pilot.subjects == ("s7",)
    assert all(job.subject_count == 1 for job in pipeline.build_plan(pilot))


def test_pilot_cli_selects_one_subject_from_a_full_config(tmp_path, monkeypatch):
    config = _config(tmp_path)
    selected = []
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)
    monkeypatch.setattr(pipeline, "_print_plan", lambda plan: selected.extend(plan))

    assert pipeline.main(["plan", str(tmp_path / "workflow.toml"), "--pilot-subject", "s7"]) == 0

    assert selected and all(job.subject_count == 1 for job in selected)


def test_source_contains_no_datalad_run():
    source = "\n".join(path.read_text() for path in Path("src").rglob("*.py"))
    assert "datalad run" not in source.lower()
