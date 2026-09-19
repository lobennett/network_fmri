import json

import pytest

from network_fmri.prepare import b0link, trim
from network_fmri.prepare.sidecar import SidecarError, read
from network_fmri.stages import StageError


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _session(tmp_path):
    session = tmp_path / "sub-s01" / "ses-01"
    fmap = session / "fmap" / "sub-s01_ses-01_fieldmap.nii.gz"
    bold = session / "func" / "sub-s01_ses-01_task-test_run-1_bold.nii.gz"
    fmap.parent.mkdir(parents=True)
    bold.parent.mkdir(parents=True)
    fmap.touch()
    bold.touch()
    return fmap, bold


def test_sidecars_are_strict_about_missing_and_malformed_json(tmp_path):
    with pytest.raises(SidecarError, match="missing"):
        read(tmp_path / "missing.json")
    malformed = tmp_path / "bad.json"
    malformed.write_text("{")
    with pytest.raises(SidecarError, match="malformed"):
        read(malformed)


def test_any_short_or_error_trim_fails_the_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(trim, "trim_tree", lambda *args, **kwargs: {
        "trimmed": 2, "already": 1, "too_short": 1, "error": 0,
    })
    with pytest.raises(StageError, match="trim failed"):
        trim.trim_dataset(tmp_path, jobs=1)


def test_trim_dataset_records_successful_and_idempotent_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(trim, "trim_tree", lambda *args, **kwargs: {
        "trimmed": 2, "already": 1, "too_short": 0, "error": 0,
    })
    result = trim.trim_dataset(tmp_path, jobs=2)
    assert result.name == "dummy-volumes-trimmed"
    assert result.details == {"trimmed": 2, "already": 1, "too_short": 0, "error": 0}


def test_b0_link_validates_every_sidecar_before_publishing(tmp_path):
    fmap, bold = _session(tmp_path)
    _write(fmap.with_name(fmap.name.replace(".nii.gz", ".json")), {})

    with pytest.raises(StageError, match="sidecar is missing"):
        b0link.link_b0(tmp_path)

    assert read(fmap.with_name(fmap.name.replace(".nii.gz", ".json"))) == {}


def test_b0_link_is_atomic_idempotent_and_includes_magnitude(tmp_path):
    fmap, bold = _session(tmp_path)
    magnitude = fmap.with_name("sub-s01_ses-01_magnitude1.nii.gz")
    magnitude.touch()
    for image in (fmap, bold, magnitude):
        _write(image.with_name(image.name.replace(".nii.gz", ".json")), {"Existing": True})

    first = b0link.link_b0(tmp_path)
    second = b0link.link_b0(tmp_path)

    identifier = "s01_ses-01"
    assert first.details["fmap"] == 2
    assert first.details["bold"] == 1
    assert second.details["fmap"] == 0
    assert second.details["bold"] == 0
    assert read(fmap.with_name(fmap.name.replace(".nii.gz", ".json")))["B0FieldIdentifier"] == identifier
    assert read(magnitude.with_name(magnitude.name.replace(".nii.gz", ".json")))["B0FieldIdentifier"] == identifier
    assert read(bold.with_name(bold.name.replace(".nii.gz", ".json")))["B0FieldSource"] == identifier
