import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from network_fmri.prepare import b0link, trim
from network_fmri.prepare.sidecar import SidecarError, read
from network_fmri.stages import StageError


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _session(tmp_path):
    (tmp_path / "dataset_description.json").write_text(json.dumps({"Name": "test"}))
    session = tmp_path / "sub-s01" / "ses-01"
    fmap = session / "fmap" / "sub-s01_ses-01_fieldmap.nii.gz"
    bold = session / "func" / "sub-s01_ses-01_task-test_run-1_bold.nii.gz"
    fmap.parent.mkdir(parents=True)
    bold.parent.mkdir(parents=True)
    fmap.touch()
    bold.touch()
    return fmap, bold


def _trim_input(tmp_path):
    (tmp_path / "dataset_description.json").write_text(json.dumps({"Name": "test"}))
    bold = tmp_path / "sub-s01" / "ses-01" / "func" / "sub-s01_ses-01_task-test_run-1_bold.nii.gz"
    bold.parent.mkdir(parents=True)
    bold.touch()
    return bold


def test_sidecars_are_strict_about_missing_and_malformed_json(tmp_path):
    with pytest.raises(SidecarError, match="missing"):
        read(tmp_path / "missing.json")
    malformed = tmp_path / "bad.json"
    malformed.write_text("{")
    with pytest.raises(SidecarError, match="malformed"):
        read(malformed)


def test_any_short_or_error_trim_fails_the_stage(tmp_path, monkeypatch):
    _trim_input(tmp_path)
    monkeypatch.setattr(trim, "trim_tree", lambda *args, **kwargs: {
        "trimmed": 2, "already": 1, "too_short": 1, "error": 0,
    })
    with pytest.raises(StageError, match="trim failed"):
        trim.trim_dataset(tmp_path, jobs=1)


def test_trim_dataset_records_successful_and_idempotent_counts(tmp_path, monkeypatch):
    _trim_input(tmp_path)
    monkeypatch.setattr(trim, "trim_tree", lambda *args, **kwargs: {
        "trimmed": 2, "already": 1, "too_short": 0, "error": 0,
    })
    result = trim.trim_dataset(tmp_path, jobs=2)
    assert result.name == "dummy-volumes-trimmed"
    assert result.details == {"trimmed": 2, "already": 1, "too_short": 0, "error": 0}


def test_trim_rejects_missing_or_empty_bids_input(tmp_path):
    with pytest.raises(StageError, match="dataset description"):
        trim.trim_dataset(tmp_path)

    (tmp_path / "dataset_description.json").write_text(json.dumps({"Name": "test"}))
    with pytest.raises(StageError, match="no subject sessions"):
        trim.trim_dataset(tmp_path)


def test_trim_rolls_back_nifti_when_sidecar_publication_fails(tmp_path, monkeypatch):
    bold = _trim_input(tmp_path)
    sidecar = bold.with_name(bold.name.replace(".nii.gz", ".json"))
    original_nifti = b"original-nifti"
    bold.write_bytes(original_nifti)
    _write(sidecar, {"RepetitionTime": 1.49})

    class FakeImage:
        shape = (2, 2, 2, 10)

        @property
        def slicer(self):
            return self

        def __getitem__(self, item):
            return self

    image = FakeImage()
    fake_nib = SimpleNamespace(
        load=lambda _: image,
        save=lambda _, path: Path(path).write_bytes(b"trimmed-nifti"),
    )
    monkeypatch.setitem(sys.modules, "nibabel", fake_nib)
    replace = os.replace

    def fail_sidecar_publish(source, destination):
        if Path(destination) == sidecar and str(source).endswith(".trim.json"):
            raise OSError("sidecar filesystem error")
        return replace(source, destination)

    monkeypatch.setattr(trim.os, "replace", fail_sidecar_publish)

    assert trim.trim_one(bold) == "error"
    assert bold.read_bytes() == original_nifti
    assert read(sidecar) == {"RepetitionTime": 1.49}
    assert not list(bold.parent.glob(".*.trim.*"))


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
    assert first.name == "b0-fieldmaps-linked"
    assert first.details["fmap"] == 2
    assert first.details["bold"] == 1
    assert second.details["fmap"] == 0
    assert second.details["bold"] == 0
    assert read(fmap.with_name(fmap.name.replace(".nii.gz", ".json")))["B0FieldIdentifier"] == identifier
    assert read(magnitude.with_name(magnitude.name.replace(".nii.gz", ".json")))["B0FieldIdentifier"] == identifier
    assert read(bold.with_name(bold.name.replace(".nii.gz", ".json")))["B0FieldSource"] == identifier


def test_b0_link_rejects_missing_or_empty_bids_input(tmp_path):
    with pytest.raises(StageError, match="dataset description"):
        b0link.link_b0(tmp_path)

    (tmp_path / "dataset_description.json").write_text(json.dumps({"Name": "test"}))
    with pytest.raises(StageError, match="no subject sessions"):
        b0link.link_b0(tmp_path)
