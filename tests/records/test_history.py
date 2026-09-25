from types import SimpleNamespace
import json

from network_fmri.processing import ProcessingStage, ProcessingStatus


def test_history_preserves_failed_job_after_retry_and_avoids_duplicate_saves(tmp_path):
    from network_fmri.records.history import record_status, read_history
    config = SimpleNamespace(mechababs=SimpleNamespace(study_dir=tmp_path, campaign="pilot"))
    stage = ProcessingStage("mriqc", "MRIQC-24.0.2", "active")
    saves = []
    runner = lambda command, **kwargs: saves.append(command)
    first = ProcessingStatus((stage,), ({"app": stage.application, "sub_id": "s03", "job_id": "10", "state": "FAILED"},))
    second = ProcessingStatus((stage,), ({"app": stage.application, "sub_id": "s03", "job_id": "11", "state": "RUNNING"},))
    record_status(config, first, runner=runner)
    record_status(config, first, runner=runner)
    record_status(config, second, runner=runner)
    attempts, lineage = read_history(tmp_path)
    assert [(row.job_id, row.state) for row in attempts] == [("10", "failed"), ("11", "running")]
    assert len(saves) == 3  # unchanged save is a DataLad no-op, and retries interrupted saves
    assert all(command[:2] == ("datalad", "save") for command in saves)
    assert len(lineage["attempts"]) == 2
    assert lineage["attempts"][0]["observations"][0]["observed_at"]


def test_history_records_transitions_for_same_job(tmp_path):
    from network_fmri.records.history import record_status, read_history
    config = SimpleNamespace(mechababs=SimpleNamespace(study_dir=tmp_path, campaign="pilot"))
    stage = ProcessingStage("anatomical", "FreeSurfer-8.2.0", "active")
    for state in ("PENDING", "RUNNING", "COMPLETED"):
        status = ProcessingStatus((stage,), ({"app": stage.application, "sub_id": "s03", "job_id": "10", "state": state},))
        record_status(config, status, runner=lambda *args, **kwargs: None)
    attempts, lineage = read_history(tmp_path)
    assert len(attempts) == 1
    assert attempts[0].stage == "freesurfer"
    assert attempts[0].state == "completed"
    assert lineage["attempts"][0]["application"] == "FreeSurfer-8.2.0"
    assert [o["state"] for o in lineage["attempts"][0]["observations"]] == ["pending", "running", "completed"]

    # Older history used the generic pipeline stage even for standalone FS8.
    path = tmp_path / "code/network_fmri/processing-history/pilot.json"
    saved = json.loads(path.read_text())
    for item in saved["attempts"].values():
        item["latest"]["stage"] = "anatomical"
    path.write_text(json.dumps(saved))
    assert read_history(tmp_path)[0][0].stage == "freesurfer"


def test_correction_campaign_preserves_original_completed_attempt(tmp_path):
    from network_fmri.records.history import record_status, read_history
    config = SimpleNamespace(mechababs=SimpleNamespace(study_dir=tmp_path, campaign="pilot"))
    for campaign in ("pilot", "pilot-edit1"):
        stage = ProcessingStage("anatomical", "FreeSurfer-8.2.0", "complete",
                                f"derivatives/FreeSurfer-8.2.0+{campaign}")
        record_status(config, ProcessingStatus((stage,), ()), runner=lambda *a, **k: None)
    _, lineage = read_history(tmp_path)
    assert {item["campaign"] for item in lineage["attempts"]} == {"pilot", "pilot-edit1"}
