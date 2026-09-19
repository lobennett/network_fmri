"""Contracts for the one fixed pipeline graph."""

from pathlib import Path

import network_fmri.pipeline as pipeline
from network_fmri.config import (
    BehaviorSource, ContainerConfig, SlurmConfig, WorkflowConfig, WorkflowPaths,
)
from network_fmri.pipeline import (
    STAGE_ORDER, build_plan, initial_submission, post_approval_submission,
    save_stage_result,
)
from network_fmri.models import StageResult
from network_fmri.slurm import submit_plan
from network_fmri.slurm import SubmissionRecord


def configuration(tmp_path: Path) -> WorkflowConfig:
    subjects = tuple(f"s{number}" for number in range(1, 47))
    subjects_file = tmp_path / "subjects.txt"
    subjects_file.write_text("\n".join(subjects) + "\n")
    return WorkflowConfig(
        paths=WorkflowPaths(
            bids_dir=tmp_path / "bids", parts_dir=tmp_path / "parts",
            work_dir=tmp_path / "work", log_dir=tmp_path / "logs",
            templateflow_dir=tmp_path / "templateflow",
            freesurfer_license=tmp_path / "license.txt",
        ),
        subjects_file=subjects_file, subjects=subjects, flywheel_project="russpold/r01network",
        behavior=BehaviorSource(tmp_path / "behavior", "a" * 40),
        mriqc=ContainerConfig(tmp_path / "mriqc.sif", "24.0.2"),
        fmriprep=ContainerConfig(tmp_path / "fmriprep.sif", "25.2.5"),
        slurm=SlurmConfig("normal", 8, 32, 720, 4),
    )


def test_graph_has_the_fixed_order_and_human_gate(tmp_path):
    plan = build_plan(configuration(tmp_path))
    names = tuple(job.name for job in plan)

    assert names == STAGE_ORDER
    assert names.index("scan-decisions-approved") < names.index("mriqc-curated")
    assert names.index("mriqc-curated") < names.index("fmriprep-array")
    assert "bids-curated-validated" not in names
    assert all(
        job.dependencies == (() if index == 0 else (names[index - 1],))
        for index, job in enumerate(plan)
    )


def test_array_workers_never_call_datalad(tmp_path):
    plan = build_plan(configuration(tmp_path))

    assert all("datalad" not in " ".join(job.command).lower() for job in plan if job.array)
    assert {job.name for job in plan if job.array} == {
        "fw2bids-array", "mriqc-array", "fmriprep-array",
    }


def test_first_submission_stops_for_human_review_and_resume_starts_after_it(tmp_path):
    plan = build_plan(configuration(tmp_path))

    assert initial_submission(plan)[-1].name == "scan-decisions-generated"
    assert "scan-decisions-approved" not in {job.name for job in initial_submission(plan)}
    assert post_approval_submission(plan)[0].name == "mriqc-curated"


def test_plan_carries_the_submission_context_needed_for_a_dry_run(tmp_path):
    plan = build_plan(configuration(tmp_path))

    record = submit_plan(initial_submission(plan), dry_run=True)

    assert record.jobs == {}
    assert record.dry_run is True


def test_milestone_receipt_carries_input_package_and_container_provenance(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    captured = []
    monkeypatch.setattr("network_fmri.pipeline._input_datalad_commit", lambda _: "a" * 40)
    monkeypatch.setattr("network_fmri.milestones.save_milestone", lambda bids, receipt: captured.append((bids, receipt)))

    save_stage_result(config.paths.bids_dir, StageResult("gs-pretrim", (tmp_path / "gs",)), config=config)

    bids, receipt = captured[0]
    assert bids == config.paths.bids_dir
    assert receipt.inputs["input_datalad_commit"] == "a" * 40
    assert receipt.inputs["behavior_commit"] == "a" * 40
    assert {"network_fmri", "network_fw2bids", "network_events", "network_qa"} <= receipt.versions.keys()
    assert receipt.versions["mriqc"]["version"] == "24.0.2"
    assert receipt.versions["fmriprep"]["version"] == "25.2.5"


def test_real_submit_creates_logs_and_persists_each_callback(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    updates = []
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)

    def submit(plan, **kwargs):
        assert config.paths.log_dir.is_dir()
        update = SubmissionRecord(jobs={"fw2bids-array": "123"}, status="submitting")
        kwargs["on_update"](update)
        updates.append(update)
        return update

    monkeypatch.setattr(pipeline, "submit_plan", submit)
    assert pipeline.main(["submit", str(tmp_path / "workflow.toml")]) == 0

    assert updates
    assert pipeline.read_record(pipeline.record_path(config)).jobs == {"fw2bids-array": "123"}


def test_dry_submit_does_not_create_the_log_directory(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)

    def submit(plan, **kwargs):
        assert not config.paths.log_dir.exists()
        assert kwargs["on_update"] is None
        return SubmissionRecord(dry_run=True)

    monkeypatch.setattr(pipeline, "submit_plan", submit)
    assert pipeline.main(["submit", str(tmp_path / "workflow.toml"), "--dry-run"]) == 0
    assert not config.paths.log_dir.exists()
