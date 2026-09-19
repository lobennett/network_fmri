"""Slurm submission mechanics are deterministic and side-effect free on dry runs."""

from types import SimpleNamespace

import pytest

from network_fmri.config import SlurmConfig
from network_fmri.slurm import PlannedJob, sbatch_command, submit_plan


class Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(tuple(command))
        return SimpleNamespace(stdout=f"{100 + len(self.calls)};sherlock\n")


def config() -> SlurmConfig:
    return SlurmConfig("normal", 8, 32, 720, 3, "lab")


def test_array_submission_uses_parsable_afterok_throttle_and_task_logs(tmp_path):
    command = sbatch_command(
        PlannedJob("convert", ("network-fmri", "_stage", "convert"), array=True),
        config(), tmp_path, ("123",), subject_count=46,
    )

    assert command[:2] == ("sbatch", "--parsable")
    assert "--dependency=afterok:123" in command
    assert "--array=0-45%3" in command
    assert str(tmp_path / "convert-%A_%a.out") in command
    assert "--account" in command and "lab" in command


def test_array_index_is_expanded_by_slurm_shell_not_quoted_as_a_literal(tmp_path):
    command = sbatch_command(
        PlannedJob(
            "convert", ("network-fmri", "_stage", "convert", "workflow.toml", "--array-index", "${SLURM_ARRAY_TASK_ID}"),
            array=True,
        ),
        config(), tmp_path, subject_count=46,
    )

    wrapped = command[command.index("--wrap") + 1]
    assert "${SLURM_ARRAY_TASK_ID}" in wrapped
    assert "'${SLURM_ARRAY_TASK_ID}'" not in wrapped


def test_submit_wires_returned_ids_into_afterok_dependencies(tmp_path):
    runner = Runner()
    plan = (
        PlannedJob("one", ("one",)),
        PlannedJob("two", ("two",), dependencies=("one",)),
    )

    record = submit_plan(
        plan, config=config(), log_dir=tmp_path, subject_count=46, runner=runner,
    )

    assert record.jobs == {"one": "101", "two": "102"}
    assert "--dependency=afterok:101" in runner.calls[1]


def test_dry_run_never_invokes_sbatch_or_creates_a_job_id(tmp_path):
    runner = Runner()
    record = submit_plan(
        (PlannedJob("one", ("one",)),), dry_run=True, runner=runner,
        config=config(), log_dir=tmp_path, subject_count=46,
    )

    assert runner.calls == []
    assert record.jobs == {}
    assert record.dry_run is True


def test_dry_run_uses_a_valid_numeric_dependency_placeholder(tmp_path):
    record = submit_plan(
        (PlannedJob("one", ("one",)), PlannedJob("two", ("two",), dependencies=("one",))),
        dry_run=True, config=config(), log_dir=tmp_path, subject_count=46,
    )

    assert "--dependency=afterok:0" in record.commands["two"]


def test_submission_persists_each_accepted_job_and_failure_state(tmp_path):
    updates = []

    class FailingRunner(Runner):
        def __call__(self, command, **kwargs):
            if self.calls:
                raise OSError("scheduler unavailable")
            return super().__call__(command, **kwargs)

    with pytest.raises(RuntimeError, match="two"):
        submit_plan(
            (PlannedJob("one", ("one",)), PlannedJob("two", ("two",), dependencies=("one",))),
            config=config(), log_dir=tmp_path, subject_count=46,
            runner=FailingRunner(), on_update=updates.append,
        )

    assert updates[0].jobs == {"one": "101"}
    assert updates[-1].status == "failed"
    assert updates[-1].jobs == {"one": "101"}


def test_missing_dependency_is_rejected_before_submission(tmp_path):
    with pytest.raises(ValueError, match="missing dependency"):
        submit_plan(
            (PlannedJob("two", ("two",), dependencies=("one",)),),
            dry_run=True, config=config(), log_dir=tmp_path, subject_count=46,
        )
