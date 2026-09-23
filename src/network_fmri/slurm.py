"""Small, explicit Slurm submission primitives for the workflow graph.

This module turns an already-validated job graph into ``sbatch`` invocations.
It does not know about BIDS or DataLad, which keeps array workers without
shared-dataset privileges.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from network_fmri.config import SlurmConfig
from network_fmri.models import Runner


@dataclass(frozen=True)
class PlannedJob:
    """One fixed pipeline stage and its scheduler requirements."""

    name: str
    command: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    array: bool = False
    cpus: int = 1
    memory_gb: int = 1
    time_minutes: int = 1
    slurm: SlurmConfig | None = None
    log_dir: Path | None = None
    subject_count: int | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("planned job needs a name")
        if not self.command:
            raise ValueError(f"planned job {self.name!r} needs a command")
        if min(self.cpus, self.memory_gb, self.time_minutes) < 1:
            raise ValueError(f"planned job {self.name!r} has non-positive resources")


@dataclass(frozen=True)
class SubmissionRecord:
    """The scheduler IDs and exact invocations for one submit attempt."""

    jobs: dict[str, str] = field(default_factory=dict)
    commands: dict[str, tuple[str, ...]] = field(default_factory=dict)
    dry_run: bool = False
    status: str = "submitted"
    error: str | None = None
    pilot_subject: str | None = None


def sbatch_command(
    job: PlannedJob,
    config: SlurmConfig,
    log_dir: Path,
    dependency_job_ids: Iterable[str] = (),
    *,
    subject_count: int,
) -> tuple[str, ...]:
    """Build the exact ``sbatch`` command for one job."""

    if subject_count < 1:
        raise ValueError("subject_count must be positive")
    dependency_job_ids = tuple(dependency_job_ids)
    command = [
        "sbatch", "--parsable", "--job-name", f"network-fmri-{job.name}",
        "--partition", config.partition, "--cpus-per-task", str(job.cpus),
        "--mem", f"{job.memory_gb}G", "--time", str(job.time_minutes),
    ]
    if config.account:
        command.extend(("--account", config.account))
    if dependency_job_ids:
        command.append("--dependency=afterok:" + ":".join(dependency_job_ids))
    if job.array:
        command.append(f"--array=0-{subject_count - 1}%{config.array_concurrency}")
        log_pattern = f"{job.name}-%A_%a"
    else:
        log_pattern = f"{job.name}-%j"
    command.extend((
        "--output", str(log_dir / f"{log_pattern}.out"),
        "--error", str(log_dir / f"{log_pattern}.err"),
        "--wrap", _shell_wrap(job.command),
    ))
    return tuple(command)


def submit_plan(
    plan: Iterable[PlannedJob],
    dry_run: bool = False,
    *,
    runner: Runner = subprocess.run,
    config: SlurmConfig | None = None,
    log_dir: Path | None = None,
    subject_count: int | None = None,
    existing_jobs: dict[str, str] | None = None,
    externally_completed: Iterable[str] = (),
    on_update: Callable[[SubmissionRecord], None] | None = None,
) -> SubmissionRecord:
    """Submit dependent jobs, or return their commands without mutation.

    ``externally_completed`` represents the human-reviewed decision manifest:
    approval is an operator action, not an automatically submitted Slurm job.
    """

    plan = tuple(plan)
    if not plan:
        return SubmissionRecord(dry_run=dry_run)
    first = plan[0]
    config = config or first.slurm
    log_dir = log_dir or first.log_dir
    subject_count = subject_count or first.subject_count
    if config is None or log_dir is None or subject_count is None:
        raise ValueError("planned jobs must carry Slurm configuration, log directory, and subject count")
    complete = set(externally_completed)
    jobs = dict(existing_jobs or {})
    dependency_jobs = {name: job_id for name, job_id in jobs.items() if name not in complete}
    commands: dict[str, tuple[str, ...]] = {}
    for job in plan:
        unresolved = [
            dependency for dependency in job.dependencies
            if dependency not in dependency_jobs and dependency not in complete
        ]
        if unresolved:
            raise ValueError(
                f"cannot submit {job.name}: missing dependency job IDs for "
                + ", ".join(unresolved)
            )
        command = sbatch_command(
            job, config, log_dir,
            (dependency_jobs[name] for name in job.dependencies if name in dependency_jobs),
            subject_count=subject_count,
        )
        commands[job.name] = command
        if dry_run:
            dependency_jobs[job.name] = "0"
            continue
        try:
            completed = runner(command, check=True, capture_output=True, text=True)
            job_id = _parse_job_id(getattr(completed, "stdout", ""))
        except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
            failed = SubmissionRecord(
                jobs=jobs.copy(), commands=commands.copy(), status="failed",
                error=f"{type(error).__name__}: {error}",
            )
            if on_update:
                on_update(failed)
            raise RuntimeError(f"sbatch submission failed for {job.name}") from error
        jobs[job.name] = job_id
        dependency_jobs[job.name] = job_id
        if on_update:
            on_update(SubmissionRecord(
                jobs=jobs.copy(), commands=commands.copy(), status="submitting",
            ))
    record = SubmissionRecord(jobs=jobs, commands=commands, dry_run=dry_run)
    if on_update and not dry_run:
        on_update(record)
    return record


def _shell_wrap(command: tuple[str, ...]) -> str:
    """Quote literal argv while preserving Slurm's one required array expansion."""

    return " ".join(
        token if token == "${SLURM_ARRAY_TASK_ID}" else shlex.quote(token)
        for token in command
    )


def _parse_job_id(stdout: object) -> str:
    text = str(stdout).strip()
    value = text.splitlines()[0].split(";", 1)[0] if text else ""
    if not value or not value.isdigit():
        raise RuntimeError(f"sbatch --parsable returned no valid job ID: {value!r}")
    return value
