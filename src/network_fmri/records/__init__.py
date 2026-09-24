"""Build a disposable dashboard index from canonical study evidence."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from network_fmri.config import WorkflowConfig
from network_fmri.processing import ProcessingManager
from network_fmri.records.collect import collect_study
from network_fmri.records.database import SCHEMA_VERSION, build_database
from network_fmri.records.mechababs import collect_attempts


def build_index(config: WorkflowConfig, output: Path) -> dict[str, object]:
    if config.mechababs is None:
        raise ValueError("workflow configuration is missing [mechababs]")
    records = collect_study(config.mechababs.study_dir)
    records = replace(
        records,
        stage_attempts=records.stage_attempts + collect_attempts(ProcessingManager(config)),
    )
    database = build_database(output, config.mechababs.study_dir, records)
    tables = ("entities", "stage_attempts", "findings", "decisions", "artifacts")
    with sqlite3.connect(database) as connection:
        counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in tables}
    return {
        "output": str(database), "schema_version": SCHEMA_VERSION,
        "study_commit": records.study_commit, "counts": counts,
    }
