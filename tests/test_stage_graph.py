"""Contracts for the one fixed pipeline graph."""

from pathlib import Path

from network_fmri.config import (
    BehaviorSource, ContainerConfig, SlurmConfig, WorkflowConfig, WorkflowPaths,
)
from network_fmri.pipeline import (
    STAGE_ORDER, build_plan, initial_submission, post_approval_submission,
)
from network_fmri.slurm import submit_plan


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
