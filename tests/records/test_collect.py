import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from network_fmri.records.collect import CollectionError, collect_study


class GitRunner:
    def __call__(self, command, **kwargs):
        value = "dataset-id" if "datalad.dataset.id" in command else "a" * 40
        return SimpleNamespace(stdout=value + "\n")


def write(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


def fixture_study(tmp_path: Path) -> Path:
    study = tmp_path / "study"
    raw = study / "sourcedata/raw"
    (study / ".git").mkdir(parents=True)
    (raw / ".git").mkdir(parents=True)
    write(raw / "dataset_description.json", "{}")
    write(raw / "code/network_fmri/milestones/bids-precuration-validated.json", json.dumps({
        "stage": "bids-precuration-validated", "status": "success",
    }))
    write(raw / "code/network_fw2bids/defacing/sub-s01.json", json.dumps({
        "subject": "s01", "status": "success",
    }))
    write(raw / "derivatives/bids-validator/report.json", json.dumps({"issues": {"errors": []}}))
    write(study / "derivatives/MRIQC/sub-s01/func/sub-s01_task-rest_bold.json", json.dumps({
        "fd_mean": 0.21, "dvars_std": 1.2,
    }))
    write(study / "code/network_fmri/scan_decisions.tsv",
          "subject\tsession\ttask\trun\tdecision\treviewer\treason_detail\n"
          "sub-s01\tses-01\trest\t1\tkeep\tLB\thigh motion\n")
    write(study / "code/network_fmri/analysis_exclusions.tsv",
          "subject\tsession\ttask\trun\tdecision\treason\n"
          "sub-s01\tses-01\trest\t1\texclude\ttiming\n")
    write(study / "code/network_fmri/surface_review.tsv",
          "subject\tsurface_dir\tstatus\tapproved\treviewer\treviewed_at\tnotes\n"
          "sub-s01\tderivatives/anat/sub-s01.zip\tcomplete\tyes\tLB\t2026-09-23T12:00:00Z\tgood\n")
    write(raw / "sub-s01/ses-01/func/sub-s01_ses-01_task-rest_events_desc-truncation.json",
          json.dumps({"total_trials": 100, "kept_trials": 40, "dropped_trials": 60}))
    write(raw / "sub-s01/ses-01/func/sub-s01_ses-01_task-rest_events.error.json",
          json.dumps({"error": "nonmonotonic onsets"}))
    return study


def test_collects_durable_evidence_without_data_content(tmp_path):
    records = collect_study(fixture_study(tmp_path), runner=GitRunner())

    assert any(item.stage == "bids-precuration-validated" for item in records.stage_attempts)
    assert any(item.finding_type == "mriqc" and "0.21" in item.evidence_json for item in records.findings)
    truncation = next(item for item in records.findings if item.finding_type == "behavior-truncation")
    assert json.loads(truncation.evidence_json) == {
        "dropped_trials": 60, "kept_trials": 40, "total_trials": 100,
    }
    assert {item.scope for item in records.decisions} == {"preprocessing", "first-level", "surface"}
    assert records.dataset_id == "dataset-id"
    assert records.study_commit == "a" * 40
    assert all("nonmonotonic onsets" not in repr(item) for item in records.artifacts)


def test_malformed_source_names_the_source_and_stops(tmp_path):
    study = fixture_study(tmp_path)
    bad = study / "code/network_fmri/scan_decisions.tsv"
    bad.write_text("subject\tdecision\n\"unterminated")

    with pytest.raises(CollectionError, match="scan decisions"):
        collect_study(study, runner=GitRunner())
