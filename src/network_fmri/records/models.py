"""Stable, content-free records for the dashboard index."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entity:
    namespace: str
    subject: str | None = None
    session: str | None = None
    datatype: str | None = None
    task: str | None = None
    run: str | None = None
    acquisition: str | None = None
    echo: str | None = None
    suffix: str | None = None

    @property
    def key(self) -> str:
        values = (
            self.namespace, self.subject, self.session, self.datatype, self.task,
            self.run, self.acquisition, self.echo, self.suffix,
        )
        return "|".join(value or "" for value in values)


@dataclass(frozen=True)
class StageAttempt:
    stage: str
    scope: str
    attempt: int
    state: str
    log_path: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    input_commit: str | None = None
    output_commit: str | None = None
    result_branch: str | None = None
    job_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class Finding:
    entity_key: str | None
    finding_type: str
    severity: str
    evidence_path: str
    evidence_json: str


@dataclass(frozen=True)
class Decision:
    entity_key: str | None
    scope: str
    decision: str
    reviewer: str | None = None
    reason: str | None = None
    reviewed_at: str | None = None


@dataclass(frozen=True)
class Artifact:
    stage: str
    path: str
    entity_key: str | None = None
    kind: str | None = None
    commit: str | None = None
