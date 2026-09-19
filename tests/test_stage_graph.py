"""Contracts for the one fixed pipeline graph."""

from pathlib import Path
from types import SimpleNamespace

import json
import pytest

import network_fmri.pipeline as pipeline
from network_fmri.config import (
    BehaviorSource, ContainerConfig, SlurmConfig, WorkflowConfig, WorkflowPaths,
)
from network_fmri.pipeline import (
    STAGE_ORDER, build_plan, initial_submission, post_approval_submission,
    save_stage_result,
)
from network_fmri.models import StageResult
from network_fmri.qa.validate import ValidationError, ValidationResult
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
    assert names.index("mriqc-curated") < names.index("bids-curated-validated")
    assert names.index("bids-curated-validated") < names.index("fmriprep-array")
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


def test_approval_milestone_binds_the_exact_manifest_and_metadata_bytes(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    manifest = config.paths.bids_dir / "code" / "network_fmri" / "scan_decisions.tsv"
    metadata = manifest.with_suffix(".meta.json")
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(b"decision\nkeep\n")
    metadata.write_bytes(b'{"approved": true}\n')
    captured = []
    monkeypatch.setattr("network_fmri.pipeline._input_datalad_commit", lambda _: "a" * 40)
    monkeypatch.setattr("network_fmri.milestones.save_milestone", lambda bids, receipt: captured.append(receipt))

    save_stage_result(
        config.paths.bids_dir,
        StageResult("scan-decisions-approved", (manifest, metadata)), config=config,
    )

    validation = captured[0].validation
    assert validation["manifest_sha256"] == pipeline._sha256(manifest.read_bytes())
    assert validation["metadata_sha256"] == pipeline._sha256(metadata.read_bytes())


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


def test_curated_validation_is_a_graph_node_without_a_second_milestone(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)
    monkeypatch.setattr(
        pipeline, "_run_stage", lambda *_: StageResult("bids-curated-validated", (tmp_path / "report.json",)),
    )
    saved = []
    monkeypatch.setattr(pipeline, "save_stage_result", lambda *args, **kwargs: saved.append(args))

    assert pipeline.stage_main(["bids-curated-validated", str(tmp_path / "workflow.toml")]) == 0
    assert saved == []


def test_curated_validation_failure_saves_its_rolled_back_diagnostics(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    report, log = tmp_path / "report.json", tmp_path / "report.log"
    report.write_text("{}")
    log.write_text("failure")
    error = ValidationError(ValidationResult("curated", report, log, 1))
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)
    monkeypatch.setattr(pipeline, "_run_stage", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    saved = []
    monkeypatch.setattr("network_fmri.milestones.save_diagnostic", lambda *args, **kwargs: saved.append(args))

    with pytest.raises(ValidationError):
        pipeline.stage_main(["mriqc-curated", str(tmp_path / "workflow.toml")])
    assert saved == [(config.paths.bids_dir, "mriqc-curated", [report, log])]


def test_resume_resubmits_a_failed_array_and_all_of_its_descendants(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    pipeline.write_record(
        pipeline.record_path(config),
        SubmissionRecord(jobs={"fw2bids-array": "123"}, status="failed", error="scheduler error"),
    )
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)
    with pytest.raises(RuntimeError, match="--resume"):
        pipeline.main(["submit", str(tmp_path / "workflow.toml")])

    selected = []
    monkeypatch.setattr(
        pipeline, "submit_plan",
        lambda jobs, **kwargs: selected.extend(job.name for job in jobs) or SubmissionRecord(),
    )
    failed = lambda *_args, **_kwargs: SimpleNamespace(stdout="FAILED\n")
    assert pipeline.main(["submit", str(tmp_path / "workflow.toml"), "--resume", "--dry-run"], runner=failed) == 0
    assert selected[0] == "fw2bids-array"


def test_status_exposes_submission_status_and_error(tmp_path, monkeypatch, capsys):
    config = configuration(tmp_path)
    pipeline.write_record(
        pipeline.record_path(config),
        SubmissionRecord(jobs={"fw2bids-array": "123"}, status="failed", error="bad output"),
    )
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)

    assert pipeline.main(["status", str(tmp_path / "workflow.toml")]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "status\tfailed", "error\tbad output", "fw2bids-array\t123",
    ]


def test_resume_does_not_treat_a_queued_decision_job_as_approval(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    pipeline.write_record(
        pipeline.record_path(config), SubmissionRecord(jobs={"scan-decisions-generated": "123"}),
    )
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)
    called = []
    monkeypatch.setattr(pipeline, "require_committed_approval", lambda _: called.append(True))
    monkeypatch.setattr(pipeline, "submit_plan", lambda *args, **kwargs: SubmissionRecord(dry_run=True))

    failed = lambda *_args, **_kwargs: SimpleNamespace(stdout="FAILED\n")
    assert pipeline.main(["submit", str(tmp_path / "workflow.toml"), "--resume", "--dry-run"], runner=failed) == 0
    assert called == []


def test_resume_starts_at_the_first_missing_milestone_after_verified_array_work(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    config.paths.parts_dir.mkdir()
    for subject in config.subjects:
        (config.paths.parts_dir / subject).mkdir()
    for stage in ("bids-assembled",):
        path = config.paths.bids_dir / "code" / "network_fmri" / "milestones" / f"{stage}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"stage": stage, "status": "success"}))
    pipeline.write_record(
        pipeline.record_path(config),
        SubmissionRecord(jobs={"fw2bids-array": "123", "bids-assembled": "124"}),
    )
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)
    selected = []

    def runner(command, **_kwargs):
        if command[0] == "sacct":
            return SimpleNamespace(stdout="COMPLETED\n")
        raise AssertionError(command)

    monkeypatch.setattr(
        pipeline, "submit_plan",
        lambda jobs, **kwargs: selected.extend(job.name for job in jobs) or SubmissionRecord(dry_run=True),
    )
    assert pipeline.main(["submit", str(tmp_path / "workflow.toml"), "--resume", "--dry-run"], runner=runner) == 0
    assert selected[0] == "behavioral-sourcedata-ingested"


def test_resume_does_not_accept_failed_curated_validator_artifacts_as_success(tmp_path):
    config = configuration(tmp_path)
    report = config.paths.bids_dir / "derivatives" / "bids-validator" / "desc-curated_validation.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}")
    report.with_suffix(".log").write_text("validator failed")
    job = next(job for job in build_plan(config) if job.name == "bids-curated-validated")
    record = SubmissionRecord(jobs={job.name: "123"})

    def failed(command, **_kwargs):
        assert command[0] == "sacct"
        return SimpleNamespace(stdout="FAILED\n")

    def completed(command, **_kwargs):
        assert command[0] == "sacct"
        return SimpleNamespace(stdout="COMPLETED\n")

    assert not pipeline._stage_completed(config, job, record, failed)
    assert pipeline._stage_completed(config, job, record, completed)


def test_resume_refuses_to_duplicate_an_active_slurm_stage(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    pipeline.write_record(
        pipeline.record_path(config), SubmissionRecord(jobs={"fw2bids-array": "123"}),
    )
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)
    monkeypatch.setattr(pipeline, "submit_plan", lambda *_args, **_kwargs: pytest.fail("must not resubmit active work"))

    def active(command, **_kwargs):
        assert command[0] == "sacct"
        return SimpleNamespace(stdout="RUNNING\n")

    with pytest.raises(RuntimeError, match="active Slurm stages.*fw2bids-array"):
        pipeline.main(["submit", str(tmp_path / "workflow.toml"), "--resume"], runner=active)


@pytest.mark.parametrize("state", [
    "SIGNALING", "STAGE_OUT", "STOPPED", "REQUEUE_FED", "REQUEUE_HOLD", "RESV_DEL_HOLD",
    "PREEMPTED", "REVOKED", "SPECIAL_EXIT",
])
def test_resume_fails_closed_for_nonterminal_scheduler_states(tmp_path, monkeypatch, state):
    config = configuration(tmp_path)
    pipeline.write_record(
        pipeline.record_path(config), SubmissionRecord(jobs={"fw2bids-array": "123"}),
    )
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)
    monkeypatch.setattr(pipeline, "submit_plan", lambda *_args, **_kwargs: pytest.fail("must not resubmit transitional work"))

    def transitional(command, **_kwargs):
        assert command[0] == "sacct"
        return SimpleNamespace(stdout=state + "\n")

    with pytest.raises(RuntimeError, match="active Slurm stages.*fw2bids-array"):
        pipeline.main(["submit", str(tmp_path / "workflow.toml"), "--resume"], runner=transitional)


@pytest.mark.parametrize("sacct_output", [None, ""])
def test_resume_fails_closed_when_scheduler_state_cannot_be_verified(tmp_path, monkeypatch, sacct_output):
    config = configuration(tmp_path)
    pipeline.write_record(
        pipeline.record_path(config), SubmissionRecord(jobs={"fw2bids-array": "123"}),
    )
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)
    monkeypatch.setattr(pipeline, "submit_plan", lambda *_args, **_kwargs: pytest.fail("must not resubmit unknown work"))

    def unknown(command, **_kwargs):
        if command[0] == "sacct" and sacct_output is None:
            raise OSError("accounting unavailable")
        if command[0] == "sacct":
            return SimpleNamespace(stdout=sacct_output)
        if command[0] == "squeue":
            return SimpleNamespace(stdout="")
        raise AssertionError(command)

    with pytest.raises(pipeline.ResumeError, match="cannot verify|returned no state"):
        pipeline.main(["submit", str(tmp_path / "workflow.toml"), "--resume"], runner=unknown)


def test_resume_does_not_trust_a_queued_curation_job_without_a_milestone(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    pipeline.write_record(
        pipeline.record_path(config),
        SubmissionRecord(jobs={"scan-decisions-generated": "123", "mriqc-curated": "456"}),
    )
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)
    monkeypatch.setattr(
        pipeline, "require_committed_approval",
        lambda _: pytest.fail("queued jobs are not completion evidence"),
    )
    selected = []
    monkeypatch.setattr(
        pipeline, "submit_plan",
        lambda jobs, **kwargs: selected.extend(job.name for job in jobs) or SubmissionRecord(dry_run=True),
    )

    failed = lambda *_args, **_kwargs: SimpleNamespace(stdout="FAILED\n")
    assert pipeline.main(["submit", str(tmp_path / "workflow.toml"), "--resume", "--dry-run"], runner=failed) == 0
    assert selected[0] == "fw2bids-array"


def test_committed_approval_requires_a_receipt_present_in_head(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    manifest = config.paths.bids_dir / "code" / "network_fmri" / "scan_decisions.tsv"
    metadata = manifest.with_suffix(".meta.json")
    manifest.parent.mkdir(parents=True)
    manifest_bytes = b"decision\nkeep\n"
    metadata_bytes = b'{"approved": true}\n'
    manifest.write_bytes(manifest_bytes)
    metadata.write_bytes(metadata_bytes)
    receipt = {
        "stage": "scan-decisions-approved",
        "status": "success",
        "validation": {
            "manifest_sha256": pipeline._sha256(manifest_bytes),
            "metadata_sha256": pipeline._sha256(metadata_bytes),
        },
    }
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[0] == "network-qa":
            return SimpleNamespace(stdout="")
        target = command[-1].split(":", 1)[1]
        output = {
            "code/network_fmri/milestones/scan-decisions-approved.json": json.dumps(receipt),
            "code/network_fmri/scan_decisions.tsv": manifest_bytes,
            "code/network_fmri/scan_decisions.meta.json": metadata_bytes,
        }[target]
        return SimpleNamespace(stdout=output)

    pipeline.require_committed_approval(config, runner)
    assert calls[1][:2] == ["git", "-C"]


def test_committed_receipt_rejects_a_new_uncommitted_seal(tmp_path):
    config = configuration(tmp_path)
    manifest = config.paths.bids_dir / "code" / "network_fmri" / "scan_decisions.tsv"
    metadata = manifest.with_suffix(".meta.json")
    manifest.parent.mkdir(parents=True)
    committed_manifest = b"decision\nkeep\n"
    metadata_bytes = b'{"approved": true}\n'
    manifest.write_bytes(b"decision\ndrop\n")
    metadata.write_bytes(metadata_bytes)
    receipt = {
        "stage": "scan-decisions-approved",
        "status": "success",
        "validation": {
            "manifest_sha256": pipeline._sha256(committed_manifest),
            "metadata_sha256": pipeline._sha256(metadata_bytes),
        },
    }

    def runner(command, **kwargs):
        if command[0] == "network-qa":
            return SimpleNamespace(stdout="")
        target = command[-1].split(":", 1)[1]
        output = {
            "code/network_fmri/milestones/scan-decisions-approved.json": json.dumps(receipt),
            "code/network_fmri/scan_decisions.tsv": committed_manifest,
            "code/network_fmri/scan_decisions.meta.json": metadata_bytes,
        }[target]
        return SimpleNamespace(stdout=output)

    with pytest.raises(RuntimeError, match="working scan decisions differ"):
        pipeline.require_committed_approval(config, runner)
