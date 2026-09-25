"""Normalize current MechaBABS jobs; historical retries remain in BABS provenance."""

from __future__ import annotations

from collections import defaultdict

from network_fmri.processing import ProcessingManager
from network_fmri.records.models import StageAttempt


def stage_name(stage):
    """Distinguish standalone FreeSurfer from the earlier fMRIPrep anatomy app."""
    if stage.stage == "anatomical" and stage.application.startswith("FreeSurfer-8."):
        return "freesurfer"
    return stage.stage


def collect_attempts(manager: ProcessingManager) -> tuple[StageAttempt, ...]:
    status = manager.status()
    stage_by_application = {stage.application: stage for stage in status.stages}
    attempts: list[StageAttempt] = []
    numbers: dict[tuple[str, str], int] = defaultdict(int)
    applications_with_jobs: set[str] = set()
    for job in status.jobs:
        application = job.get("app", "")
        stage = stage_by_application.get(application)
        name = stage_name(stage) if stage else application
        scope = _scope(job)
        key = (name, scope)
        numbers[key] += 1
        applications_with_jobs.add(application)
        attempts.append(StageAttempt(
            stage=name,
            scope=scope,
            attempt=numbers[key],
            state="failed" if job.get("failed") == "true" else (job.get("state") or "unknown").lower(),
            log_path=job.get("logs") or None,
            job_id=job.get("job_id") or None,
        ))
    for stage in status.stages:
        if stage.application not in applications_with_jobs:
            attempts.append(StageAttempt(stage_name(stage), "dataset", 1, stage.state))
    return tuple(attempts)


def _scope(job: dict[str, str]) -> str:
    subject = (job.get("sub_id") or "dataset").removeprefix("sub-")
    session = (job.get("ses_id") or "").removeprefix("ses-")
    return f"sub-{subject}" + (f"/ses-{session}" if session else "") if subject != "dataset" else subject
