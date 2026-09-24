"""Normalize refreshed MechaBABS/BABS status without discarding attempts."""

from __future__ import annotations

from collections import defaultdict
import re

from network_fmri.processing import ProcessingManager
from network_fmri.records.models import StageAttempt


def collect_attempts(manager: ProcessingManager) -> tuple[StageAttempt, ...]:
    status = manager.status()
    stage_by_application = {stage.application: stage for stage in status.stages}
    attempts: list[StageAttempt] = []
    numbers: dict[tuple[str, str], int] = defaultdict(int)
    applications_with_jobs: set[str] = set()
    for job in status.jobs:
        application = job.get("pipeline", "")
        stage = stage_by_application.get(application)
        name = stage.stage if stage else application
        scope = _scope(job)
        key = (name, scope)
        numbers[key] += 1
        applications_with_jobs.add(application)
        attempts.append(StageAttempt(
            stage=name,
            scope=scope,
            attempt=numbers[key],
            state=(job.get("state") or job.get("status") or "unknown").lower(),
            log_path=job.get("log_path") or job.get("log") or None,
            started_at=job.get("started_at") or job.get("submitted_at") or None,
            finished_at=job.get("finished_at") or job.get("ended_at") or None,
            input_commit=job.get("input_commit") or None,
            output_commit=job.get("output_commit") or None,
            result_branch=job.get("result_branch") or job.get("branch") or None,
            job_id=job.get("job_id") or None,
            error=_safe_error(job.get("error") or job.get("reason") or ""),
        ))
    for stage in status.stages:
        if stage.application not in applications_with_jobs:
            attempts.append(StageAttempt(stage.stage, "dataset", 1, stage.state))
    return tuple(attempts)


def _scope(job: dict[str, str]) -> str:
    subject = (job.get("subject") or job.get("participant") or "dataset").removeprefix("sub-")
    session = (job.get("session") or "").removeprefix("ses-")
    return f"sub-{subject}" + (f"/ses-{session}" if session else "") if subject != "dataset" else subject


def _safe_error(value: str) -> str | None:
    if not value:
        return None
    value = re.sub(r"(?:/[^\s:]+)+", "[path]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+", "[email]", value)
    value = re.sub(
        r"(?i)\b(?:token|password|secret|api[_-]?key)\s*[=:]\s*\S+",
        "[credential]", value,
    )
    return value[:500]
