import json
import subprocess
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
        "inputs": {}, "outputs": {}, "versions": {}, "jobs": {}, "validation": {},
    }))
    write(raw / "code/network_fw2bids/defacing/sub-s01.json", json.dumps({
        "schema_version": 1, "subject": "s01", "status": "success",
        "software": {"name": "PyDeface", "version": "2.0.2", "container": "pydeface.sif", "sha256": "b" * 64},
        "images": [{"path": "sub-s01/anat/sub-s01_T1w.nii.gz", "input_sha256": "c" * 64,
                    "output_sha256": "d" * 64, "shape": [2, 2, 2], "zooms": [1, 1, 1],
                    "affine_sha256": "e" * 64}],
    }))
    write(raw / "derivatives/bids-validator/report.json", json.dumps({"issues": {"errors": []}}))
    write(study / "derivatives/MRIQC/sub-s01/func/sub-s01_task-rest_bold.json", json.dumps({
        "fd_mean": 0.21, "dvars_std": 1.2,
    }))
    write(study / "code/network_fmri/scan_decisions.tsv",
          "subject\tsession\ttask\trun\tdecision\treviewer\treason_detail\n"
          "sub-s01\tses-01\trest\t1\tkeep\tLB\thigh motion\n")
    write(raw / "code/network_fmri/analysis_exclusions.tsv",
          "subject\tsession\ttask\trun\tanalysis_scope\treason_code\treason_detail\treviewer\treviewed_at\n"
          "sub-s01\tses-01\trest\t1\ttask_first_level\tincomplete_event_timing\tTiming incomplete\tLB\t2026-09-23T19:11:03Z\n")
    write(study / "code/network_fmri/milestones/scan-decisions-approved.json", json.dumps({
        "stage": "scan-decisions-approved", "status": "success", "inputs": {}, "outputs": {},
        "versions": {}, "jobs": {}, "validation": {},
    }))
    write(study / "code/network_fmri/surface_review.tsv",
          "subject\tsurface_dir\tstatus\tapproved\treviewer\treviewed_at\tnotes\n"
          "sub-s01\tderivatives/anat/sub-s01.zip\tcomplete\tyes\tLB\t2026-09-23T12:00:00Z\tgood\n")
    write(raw / "sourcedata/events_qc/sub-s01/ses-01/sub-s01_ses-01_task-rest_run-1_desc-truncation.json",
          json.dumps({"NTestTrialsExpected": 100, "NTestTrialsRetained": 40,
                      "FractionTestTrialsDropped": 0.6, "ScanDurationSeconds": 120.0,
                      "NScanTestTrialsDropped": 2, "FractionScanTestTrialsDropped": 0.05}))
    write(raw / "sourcedata/events_qc/conversion_errors.tsv",
          "subject\tsession\ttask\trun\tsource_path\texception_class\tmessage\n"
          "sub-s01\tses-01\trest\t1\t/private/behavior.json\tTimingEvidenceError\tnonmonotonic onsets\n")
    return study


def test_collects_durable_evidence_without_data_content(tmp_path):
    records = collect_study(fixture_study(tmp_path), runner=GitRunner())
    review = next(item for item in records.findings if item.finding_type == "scan-review")
    assert review.entity_key == next(item.entity_key for item in records.decisions if item.scope == "preprocessing")

    assert any(item.stage == "bids-precuration-validated" for item in records.stage_attempts)
    assert any(item.finding_type == "mriqc" and "0.21" in item.evidence_json for item in records.findings)
    truncation = next(item for item in records.findings if item.finding_type == "behavior-truncation")
    assert json.loads(truncation.evidence_json) == {
        "NTestTrialsExpected": 100, "NTestTrialsRetained": 40,
        "FractionTestTrialsDropped": 0.6, "ScanDurationSeconds": 120.0,
        "NScanTestTrialsDropped": 2, "FractionScanTestTrialsDropped": 0.05,
    }
    assert {item.scope for item in records.decisions} == {"preprocessing", "task_first_level", "surface"}
    assert truncation.severity == "metric"
    exclusion = next(item for item in records.decisions if item.scope == "task_first_level")
    assert truncation.entity_key == exclusion.entity_key
    assert exclusion.decision == "exclude" and exclusion.reason == "Timing incomplete"
    assert any(item.stage == "scan-decisions-approved" for item in records.stage_attempts)
    assert any(item.stage == "defacing" and item.scope == "sub-s01" and item.state == "success"
               for item in records.stage_attempts)
    error = next(item for item in records.findings if item.finding_type == "event-error")
    assert error.evidence_json == "{}"
    assert "private/behavior" not in repr(records) and "nonmonotonic onsets" not in repr(records)
    assert records.dataset_id == "dataset-id"
    assert records.study_commit == "a" * 40
    assert all("nonmonotonic onsets" not in repr(item) for item in records.artifacts)


def test_malformed_source_names_the_source_and_stops(tmp_path):
    study = fixture_study(tmp_path)
    bad = study / "code/network_fmri/scan_decisions.tsv"
    bad.write_text("subject\tdecision\n\"unterminated")

    with pytest.raises(CollectionError, match="scan decisions"):
        collect_study(study, runner=GitRunner())


def test_timing_evidence_does_not_make_an_exclusion(tmp_path):
    study = fixture_study(tmp_path)
    (study / "sourcedata/raw/code/network_fmri/analysis_exclusions.tsv").unlink()
    records = collect_study(study, runner=GitRunner())
    assert not any(item.scope == "task_first_level" for item in records.decisions)
    assert any(item.finding_type == "behavior-truncation" and item.severity == "metric"
               for item in records.findings)


def test_wrapper_exclusions_take_precedence_over_raw(tmp_path):
    study = fixture_study(tmp_path)
    write(study / "code/network_fmri/analysis_exclusions.tsv",
          "subject\tanalysis_scope\treason_code\nsub-s02\ttask_first_level\ttiming\n")
    records = collect_study(study, runner=GitRunner())
    excluded = [item for item in records.decisions if item.scope == "task_first_level"]
    assert len(excluded) == 1
    assert excluded[0].entity_key.startswith("raw|s02|")


def test_reads_dataset_ids_from_tracked_datalad_configs(tmp_path):
    study = tmp_path / "study"
    raw = study / "sourcedata/raw"
    for root, dataset_id in ((study, "study-id"), (raw, "raw-id")):
        root.mkdir(parents=True, exist_ok=True)
        subprocess.run(("git", "init", str(root)), check=True, capture_output=True)
        write(root / ".datalad/config", f"[datalad \"dataset\"]\n\tid = {dataset_id}\n")
        subprocess.run(("git", "add", ".datalad/config"), cwd=root, check=True)
        subprocess.run(("git", "-c", "user.name=Test", "-c", "user.email=test@example.org",
                        "commit", "-m", "Record dataset identity"),
                       cwd=root, check=True, capture_output=True)
    records = collect_study(study)
    assert records.dataset_id == "study-id"
    assert next(item for item in records.artifacts if item.stage == "raw").kind == "dataset:raw-id"


def test_babs_raw_input_sidecars_are_not_mriqc_metrics(tmp_path):
    study = fixture_study(tmp_path)
    write(study / "derivatives/MRIQC/sourcedata/raw/sub-s01/func/sub-s01_task-rest_bold.json",
          json.dumps({"RepetitionTime": 1.49, "TaskName": "rest"}))
    write(study / "derivatives/MRIQC/sourcedata/raw/derivatives/mriqc/sub-s01_task-rest_bold.json",
          json.dumps({"fd_mean": 99.0}))
    records = collect_study(study, runner=GitRunner())
    assert len([row for row in records.findings if row.finding_type == "mriqc"]) == 1


def test_flywheel_selection_keeps_current_inventory_distinct_from_conversion(tmp_path):
    study = fixture_study(tmp_path)
    write(study/'code/network_fw2bids/selection/sub-s01.json', json.dumps({
        'schema_version':1,'subject':'s01','snapshot_kind':'current_inventory','captured_at':'2026-09-24T00:00:00Z',
        'project':'russpold/r01network','acquisitions':[
            {'session':'ses-01','acquisition_id':'rejected','label':'T1w_qa-reject','decision':'skipped','reason':'qa-reject','bids_prefix':None},
            {'session':'ses-01','acquisition_id':'included','label':'task-rest_bold','decision':'selected','reason':'mapped_to_bids','bids_prefix':'sub-s01/ses-01/func/sub-s01_ses-01_task-rest_run-1_bold'}]}))
    records=collect_study(study, runner=GitRunner())
    findings=[f for f in records.findings if f.finding_type=='flywheel-acquisition']
    assert len(findings)==2
    assert {json.loads(f.evidence_json)['decision'] for f in findings}=={'skipped','selected'}
    assert all(json.loads(f.evidence_json)['snapshot_kind']=='current_inventory' for f in findings)
    assert not any(a.stage=='conversion' for a in records.stage_attempts)
