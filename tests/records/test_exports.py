import json
from pathlib import Path

from network_fmri.records.database import build_database
from network_fmri.records.exports import export_table
from tests.records.test_database import records


def test_tsv_and_json_exports_are_deterministic(tmp_path):
    database = build_database(tmp_path / "index.sqlite", tmp_path / "study", records())
    tsv = export_table(database, "entities", "tsv")
    json_text = export_table(database, "entities", "json")

    assert tsv.startswith("entity_key\tnamespace\tsubject")
    assert tsv == export_table(database, "entities", "tsv")
    assert json.loads(json_text)[0]["subject"] == "s01"


def test_export_restricts_tables_and_formats(tmp_path):
    database = build_database(tmp_path / "index.sqlite", tmp_path / "study", records())
    for table, format in (("metadata", "json"), ("entities", "xml")):
        try:
            export_table(database, table, format)
        except ValueError:
            pass
        else:
            raise AssertionError("unsupported export was accepted")
