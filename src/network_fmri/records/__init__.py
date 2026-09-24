"""Build a disposable dashboard index from canonical study evidence."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from network_fmri.config import WorkflowConfig
from network_fmri.processing import ProcessingManager
from network_fmri.records.collect import collect_study
from network_fmri.records.database import SCHEMA_VERSION, build_database
from network_fmri.records.mechababs import collect_attempts
from network_fmri.records.inventory import inventory_dataset, study_datasets
from network_fmri.records.models import Artifact
from network_fmri.records.history import read_history


def build_index(config: WorkflowConfig, output: Path) -> dict[str, object]:
    if config.mechababs is None:
        raise ValueError("workflow configuration is missing [mechababs]")
    records = collect_study(
        config.mechababs.study_dir, raw_slot=config.mechababs.raw_slot
    )
    datasets = list(study_datasets(config.mechababs.study_dir))
    inventories = tuple(inventory_dataset(root, identity) for identity, root in datasets)
    locations = tuple(Artifact("dataset", root.relative_to(config.mechababs.study_dir).as_posix(),
                               kind="dataset:" + identity) for identity, root in datasets)
    records = replace(records, lineage=records.lineage + inventories, artifacts=records.artifacts + locations)
    live_attempts = collect_attempts(ProcessingManager(config))
    history, history_lineage = read_history(config.mechababs.study_dir)
    # Live status supersedes the last observation of the same scheduler job.
    current_keys = {(row.stage, row.scope, row.job_id) for row in live_attempts}
    history = tuple(row for row in history if (row.stage, row.scope, row.job_id) not in current_keys)
    records = replace(
        records,
        stage_attempts=_renumber_attempts(records.stage_attempts + history + live_attempts),
        lineage=records.lineage + (history_lineage,),
    )
    database = build_database(output, config.mechababs.study_dir, records)
    tables = ("entities", "stage_attempts", "findings", "decisions", "artifacts", "artifact_versions", "lineage_links")
    with sqlite3.connect(database) as connection:
        counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in tables}
    return {
        "output": str(database), "schema_version": SCHEMA_VERSION,
        "study_commit": records.study_commit, "counts": counts,
    }


def _renumber_attempts(attempts):
    numbers = defaultdict(int)
    result = []
    for attempt in attempts:
        key = (attempt.stage, attempt.scope)
        numbers[key] += 1
        result.append(replace(attempt, attempt=numbers[key]))
    return tuple(result)
