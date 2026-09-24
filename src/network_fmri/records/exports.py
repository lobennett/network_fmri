"""Deterministic query exports for dashboards and operator review."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path

TABLES = frozenset({"entities", "stage_attempts", "findings", "decisions", "artifacts",
                   "artifact_versions", "artifact_observations", "processing_attempts", "lineage_links"})


def export_table(database: Path, table: str, format: str) -> str:
    if table not in TABLES:
        raise ValueError("unsupported record table")
    if format not in {"tsv", "json"}:
        raise ValueError("export format must be tsv or json")
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    values = [dict(row) for row in rows]
    if format == "json":
        return json.dumps(values, indent=2, sort_keys=True) + "\n"
    stream = io.StringIO()
    fields = tuple(values[0]) if values else _columns(database, table)
    writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(values)
    return stream.getvalue()


def _columns(database: Path, table: str) -> tuple[str, ...]:
    with sqlite3.connect(database) as connection:
        return tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
