import json
import pytest


def test_artifact_identity_tracks_content_not_observation_commit():
    from network_fmri.records.lineage import artifact_id
    assert artifact_id("ds", "file.nii.gz", "sha256:a") == artifact_id("ds", "file.nii.gz", "sha256:a")
    assert artifact_id("ds", "file.nii.gz", "sha256:a") != artifact_id("ds", "file.nii.gz", "sha256:b")
    assert artifact_id("ds1", "file.nii.gz", "sha256:a") != artifact_id("ds2", "file.nii.gz", "sha256:a")


def receipt():
    from network_fmri.records.lineage import artifact_id
    artifacts = [{"id": artifact_id("ds", p, "sha256:" + p), "dataset_id": "ds", "path": p,
                  "content_id": "sha256:" + p, "commit": None, "source_ids": {},
                  "availability": "available"} for p in ("source", "echo1", "echo2", "echo3")]
    return {"schema_version": 1, "artifacts": artifacts,
            "attempts": [{"id": "convert-1", "stage": "conversion", "scope": "sub-s03", "status": "success"}],
            "links": [{"input": artifacts[0]["id"], "output": a["id"], "attempt": "convert-1",
                       "relation": "conversion"} for a in artifacts[1:]]}


def test_multi_echo_receipt_preserves_all_links(tmp_path):
    from network_fmri.records.lineage import read_receipt
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt()))
    assert len(read_receipt(path)["links"]) == 3


@pytest.mark.parametrize("change", ["dangling", "unsafe", "identity", "schema", "duplicate"])
def test_invalid_receipt_fails_without_silent_partial_lineage(tmp_path, change):
    from network_fmri.records.lineage import read_receipt
    value = receipt()
    if change == "dangling":
        value["links"][0]["input"] = "missing"
    elif change == "unsafe":
        value["artifacts"][0]["path"] = "../secret"
    elif change == "identity":
        value["artifacts"][0]["content_id"] = "different"
    elif change == "duplicate":
        value["artifacts"].append(value["artifacts"][0])
    else:
        value["schema_version"] = 999
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        read_receipt(path)
