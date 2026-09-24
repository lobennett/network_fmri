import json


def test_conversion_and_defacing_join_on_file_content(tmp_path):
    from network_fmri.records.native_lineage import collect_native_lineage
    relative = "sub-s03/anat/sub-s03_T1w.nii.gz"
    conversion = tmp_path / "code/network_fw2bids/conversion/sub-s03.json"
    conversion.parent.mkdir(parents=True)
    conversion.write_text(json.dumps({"schema_version": 1, "subject": "s03", "archives": [{
        "acquisition_id": "fw-acq", "archive_sha256": "a" * 64, "dcm2niix_version": "1.0",
        "outputs": [{"path": relative, "sha256": "b" * 64}]}]}))
    defacing = tmp_path / "code/network_fw2bids/defacing/sub-s03.json"
    defacing.parent.mkdir(parents=True)
    defacing.write_text(json.dumps({"schema_version": 1, "subject": "s03", "status": "success",
        "software": {}, "images": [{"path": relative, "input_sha256": "b" * 64, "output_sha256": "c" * 64}]}))
    receipts = collect_native_lineage(tmp_path, "raw-id")
    links = [link for receipt in receipts for link in receipt["links"]]
    converted = next(link for link in links if link["relation"] == "conversion")
    defaced = next(link for link in links if link["relation"] == "defacing")
    assert converted["output"] == defaced["input"]
    assert converted["input"] != defaced["output"]


def test_behavior_links_to_events_without_inferring_missing_history(tmp_path):
    from network_fmri.records.native_lineage import collect_native_lineage
    path = tmp_path / "sourcedata/events_qc/sub-s03/ses-01/sub-s03_ses-01_task-test_run-1_desc-truncation.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"Provenance": {"SchemaVersion": 1,
        "Behavior": {"path": "sourcedata/behavioral/scan.csv", "sha256": "a" * 64},
        "Events": {"path": "sub-s03/ses-01/func/scan_events.tsv", "sha256": "b" * 64},
        "BOLDInputs": [], "TimingSidecars": []}}))
    receipts = collect_native_lineage(tmp_path, "raw-id")
    assert len(receipts) == 1
    assert receipts[0]["links"][0]["relation"] == "events"
    path.write_text("{}")
    assert collect_native_lineage(tmp_path, "raw-id") == ()
