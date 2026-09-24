"""Build the disposable SQLite dashboard index atomically."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from network_fmri.records.collect import RecordSet

SCHEMA_VERSION = 1


def build_database(output: Path, study: Path, records: RecordSet) -> Path:
    output = Path(output).resolve()
    study = Path(study).resolve()
    if output == study or output.is_relative_to(study):
        raise ValueError("dashboard cache must be outside the study and its subdatasets")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with sqlite3.connect(temporary) as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.executescript(Path(__file__).with_name("schema.sql").read_text())
            _insert(db, records)
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("dashboard index has invalid foreign keys")
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("dashboard index failed integrity check")
            db.commit()
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return output


def _insert(db: sqlite3.Connection, records: RecordSet) -> None:
    metadata = {
        "schema_version": str(SCHEMA_VERSION),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "study_id": records.dataset_id,
        "study_commit": records.study_commit,
    }
    db.executemany("INSERT INTO metadata VALUES (?, ?)", sorted(metadata.items()))
    db.executemany(
        "INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(item.key, item.namespace, item.subject, item.session, item.datatype, item.task,
          item.run, item.acquisition, item.echo, item.suffix) for item in records.entities],
    )
    db.executemany(
        "INSERT INTO stage_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(item.stage, item.scope, item.attempt, item.state, item.log_path, item.started_at,
          item.finished_at, item.input_commit, item.output_commit, item.result_branch,
          item.job_id, item.error) for item in records.stage_attempts],
    )
    db.executemany(
        "INSERT INTO findings(entity_key,finding_type,severity,evidence_path,evidence_json) VALUES (?,?,?,?,?)",
        [(item.entity_key, item.finding_type, item.severity, item.evidence_path,
          item.evidence_json) for item in records.findings],
    )
    db.executemany(
        "INSERT INTO decisions(entity_key,scope,decision,reviewer,reason,reviewed_at) VALUES (?,?,?,?,?,?)",
        [(item.entity_key, item.scope, item.decision, item.reviewer, item.reason,
          item.reviewed_at) for item in records.decisions],
    )
    db.executemany(
        "INSERT INTO artifacts(stage,path,entity_key,kind,commit_hash) VALUES (?,?,?,?,?)",
        [(item.stage, item.path, item.entity_key, item.kind, item.commit)
         for item in records.artifacts],
    )
