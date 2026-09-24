import sqlite3
from pathlib import Path

import pytest

from network_fmri.records.collect import RecordSet
from network_fmri.records.database import SCHEMA_VERSION, build_database
from network_fmri.records.models import Artifact, Decision, Entity, Finding, StageAttempt


def records(state="complete"):
    entity = Entity("raw", "s01", "01", "func", "rest", "1", suffix="bold")
    return RecordSet(
        "study-id", "a" * 40, (entity,),
        (StageAttempt("mriqc", "sub-s01", 1, state),),
        (Finding(entity.key, "motion", "review", "evidence.json", "{}"),),
        (Decision(entity.key, "preprocessing", "keep"),),
        (Artifact("mriqc", "derivatives/mriqc/report.html", entity.key),),
    )


def test_builds_versioned_database_with_foreign_keys_and_metadata(tmp_path):
    output = tmp_path / "cache/index.sqlite"
    build_database(output, tmp_path / "study", records())

    with sqlite3.connect(output) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
        assert db.execute("SELECT value FROM metadata WHERE key='study_id'").fetchone()[0] == "study-id"
        assert db.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_key_list(findings)").fetchall()


def test_rebuild_replaces_stale_rows_atomically(tmp_path):
    output = tmp_path / "index.sqlite"
    build_database(output, tmp_path / "study", records("failed"))
    build_database(output, tmp_path / "study", records("complete"))

    with sqlite3.connect(output) as db:
        assert db.execute("SELECT state FROM stage_attempts").fetchall() == [("complete",)]


def test_failure_keeps_previous_database(tmp_path, monkeypatch):
    output = tmp_path / "index.sqlite"
    build_database(output, tmp_path / "study", records("old"))
    before = output.read_bytes()
    monkeypatch.setattr("network_fmri.records.database._insert", lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        build_database(output, tmp_path / "study", records("new"))
    assert output.read_bytes() == before


def test_rejects_cache_inside_study(tmp_path):
    study = tmp_path / "study"
    study.mkdir()
    with pytest.raises(ValueError, match="outside the study"):
        build_database(study / "index.sqlite", study, records())
