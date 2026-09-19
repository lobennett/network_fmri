"""Synthetic acceptance coverage for the public submission boundary."""

from __future__ import annotations

from pathlib import Path

import network_fmri.pipeline as pipeline
from network_fmri.config import (
    BehaviorSource, ContainerConfig, SlurmConfig, WorkflowConfig, WorkflowPaths,
)
from network_fmri.models import StageResult
from network_fmri.slurm import SubmissionRecord


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


def test_synthetic_pipeline_stops_for_review_then_resumes(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config_path = tmp_path / "workflow.toml"
    config_path.write_text("synthetic")
    submissions: list[tuple[str, ...]] = []
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)

    def submit(jobs, *, existing_jobs=(), on_update=None, **_):
        names = tuple(job.name for job in jobs)
        submissions.append(names)
        record = SubmissionRecord(
            jobs={**dict(existing_jobs), **{name: str(index) for index, name in enumerate(names, 1)}},
            commands={name: job.command for name, job in zip(names, jobs)},
        )
        if on_update:
            on_update(record)
        return record

    monkeypatch.setattr(pipeline, "submit_plan", submit)
    assert pipeline.main(["submit", str(config_path)]) == 0
    assert submissions[0][-1] == "scan-decisions-generated"
    assert "scan-decisions-approved" not in submissions[0]

    monkeypatch.setattr(pipeline, "require_committed_approval", lambda _: None)
    assert pipeline.main(["submit", str(config_path), "--resume"]) == 0
    assert submissions[1][0] == "mriqc-curated"
    assert submissions[1][-1] == "fmriprep-complete"


def test_synthetic_graph_records_only_serial_milestones(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config_path = tmp_path / "workflow.toml"
    config_path.write_text("synthetic")
    saved: list[str] = []
    monkeypatch.setattr(pipeline.WorkflowConfig, "load", lambda _: config)
    monkeypatch.setattr(
        pipeline, "_run_stage",
        lambda _config, stage, _index: StageResult(stage, (tmp_path / stage,)),
    )
    monkeypatch.setattr(
        pipeline, "save_stage_result",
        lambda _bids, result, **_kwargs: saved.append(result.name),
    )

    arrays = {"fw2bids-array", "mriqc-array", "fmriprep-array"}
    for stage in pipeline.STAGE_ORDER:
        arguments = [stage, str(config_path)]
        if stage in arrays:
            arguments.extend(("--array-index", "0"))
        assert pipeline.stage_main(arguments) == 0

    assert saved == [
        stage for stage in pipeline.STAGE_ORDER
        if stage not in arrays | {"bids-curated-validated"}
    ]


def test_source_contains_no_datalad_run():
    source = "\n".join(path.read_text() for path in Path("src").rglob("*.py"))
    assert "datalad run" not in source.lower()
