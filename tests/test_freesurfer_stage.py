"""Contracts for the FreeSurfer surface review gate."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from network_fmri.qa.freesurfer import generate_surface_review, validate_surface_review
from network_fmri.qa.freesurfer import REQUIRED_OUTPUTS
from network_fmri.qa.freesurfer import surface_fingerprints
from network_fmri.stages import StageError


def configuration(tmp_path: Path, subjects: tuple[str, ...] = ("s1", "s2")):
    return SimpleNamespace(
        subjects=subjects,
        mechababs=SimpleNamespace(study_dir=tmp_path / "study"),
        paths=SimpleNamespace(bids_dir=tmp_path / "raw"),
    )


def surface_zip(path: Path, subject: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for relative in REQUIRED_OUTPUTS:
            archive.writestr(f"freesurfer/sub-{subject}/{relative}", "result")


def test_same_content_has_same_fingerprint_zipped_or_unpacked(tmp_path):
    config = configuration(tmp_path, ("s1",))
    zipped = tmp_path / "zipped"
    unpacked = tmp_path / "unpacked"
    surface_zip(zipped / "sub-s1_anat.zip", "s1")
    for relative in REQUIRED_OUTPUTS:
        path = unpacked / "subjects/sub-s1" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("result")
    assert surface_fingerprints(config, zipped) == surface_fingerprints(config, unpacked)


def test_fingerprint_includes_non_primary_reconstruction_files(tmp_path):
    config = configuration(tmp_path, ("s1",))
    root = tmp_path / "surfaces"
    for relative in REQUIRED_OUTPUTS:
        path = root / "sub-s1" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("result")
    first = surface_fingerprints(config, root)
    (root / "sub-s1/mri/aseg.mgz").write_text("changed segmentation")
    assert surface_fingerprints(config, root) != first


def test_generate_preserves_existing_review(tmp_path):
    config = configuration(tmp_path, ("s1",))
    derivative = tmp_path / "anat"
    surface_zip(derivative / "sub-s1_anat.zip", "s1")
    manifest = generate_surface_review(config, derivative).outputs[0]
    original = manifest.read_bytes()
    with pytest.raises(StageError, match="exists"):
        generate_surface_review(config, derivative)
    assert manifest.read_bytes() == original


def test_review_is_regenerated_from_external_anatomical_derivative(tmp_path):
    config = configuration(tmp_path)
    derivative = tmp_path / "campaign/derivatives/fMRIPrep-25.2.5+anat"
    for subject in config.subjects:
        artifact = derivative / f"sub-{subject}_fMRIPrep-25.2.5+anat.zip"
        surface_zip(artifact, subject)

    result = generate_surface_review(config, derivative)
    manifest, metadata = result.outputs

    assert manifest.parent == config.mechababs.study_dir / "code/network_fmri"
    rows = manifest.read_text().splitlines()
    assert str(derivative / "sub-s1_fMRIPrep-25.2.5+anat.zip") in rows[1]
    header = rows[0].split("\t")
    values = rows[1].split("\t")
    assert values[header.index("status")] == "complete"
    assert values[header.index("approved")] == "no"
    assert len(values[header.index("surface_fingerprint")]) == 64
    assert json.loads(metadata.read_text())["surface_root"] == str(derivative.resolve())


def test_review_marks_missing_subject_artifact_incomplete(tmp_path):
    config = configuration(tmp_path)
    derivative = tmp_path / "anat"
    derivative.mkdir()
    surface_zip(derivative / "sub-s1_anat.zip", "s1")

    result = generate_surface_review(config, derivative)
    rows = result.outputs[0].read_text().splitlines()

    assert rows[2].split("\t")[2] == "missing"
    with pytest.raises(StageError, match="not approved"):
        validate_surface_review(config)


def test_surface_review_requires_explicit_approval_for_every_subject(tmp_path):
    config = configuration(tmp_path)
    derivative = tmp_path / "anat"
    derivative.mkdir()
    for subject in config.subjects:
        surface_zip(derivative / f"sub-{subject}_anat.zip", subject)
    manifest = generate_surface_review(config, derivative).outputs[0]

    lines = manifest.read_text().splitlines()
    header = lines[0].split("\t")
    output = [lines[0]]
    for line in lines[1:]:
        values = line.split("\t")
        values[header.index("approved")] = "yes"
        values[header.index("reviewer")] = "LB"
        values[header.index("reviewed_at")] = "2026-09-23T12:00:00Z"
        output.append("\t".join(values))
    manifest.write_text("\n".join(output) + "\n")

    result = validate_surface_review(config)
    assert result.name == "surface-review-approved"
    assert result.details["subjects"] == 2

    surface_zip(derivative / "sub-s1_anat.zip", "s1")
    with zipfile.ZipFile(derivative / "sub-s1_anat.zip", "a") as archive:
        archive.writestr("freesurfer/sub-s1/mri/aseg.mgz", "new segmentation")
    with pytest.raises(StageError, match="changed"):
        validate_surface_review(config)


def test_surface_review_rejects_non_object_metadata(tmp_path):
    config = configuration(tmp_path, ("s1",))
    derivative = tmp_path / "anat"
    derivative.mkdir()
    surface_zip(derivative / "sub-s1_anat.zip", "s1")
    result = generate_surface_review(config, derivative)
    result.outputs[1].write_text("[]\n")

    with pytest.raises(StageError, match="missing or malformed"):
        validate_surface_review(config)


def test_legacy_approved_review_can_be_validated_for_migration(tmp_path):
    config = configuration(tmp_path, ("s1",))
    manifest = tmp_path / "legacy/surface_review.tsv"
    manifest.parent.mkdir()
    manifest.write_text(
        "subject\tsurface_dir\tstatus\tapproved\treviewer\treviewed_at\tnotes\n"
        "sub-s1\t/old/sub-s1\tcomplete\tyes\tLB\t2026-09-23T12:00:00Z\tgood\n"
    )
    manifest.with_suffix(".meta.json").write_text('{"subjects":["sub-s1"]}\n')

    result = validate_surface_review(config, manifest, allow_legacy=True)

    assert result.name == "surface-review-approved"
