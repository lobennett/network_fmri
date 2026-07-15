"""Tests for network_fmri.trim — 7 dummy-volume BOLD trimming (ported verbatim
from the neuro_workflow monolith's scripts/trim_bold.py)."""

import json

import nibabel as nib
import numpy as np

from network_fmri.trim import N_DUMMY, trim_bold_directory


def _make_bold(tmp_path, sub="s01", ses="01", n_vols=17, task="x", run=1):
    func_dir = tmp_path / f"sub-{sub}" / f"ses-{ses}" / "func"
    func_dir.mkdir(parents=True, exist_ok=True)
    stem = f"sub-{sub}_ses-{ses}_task-{task}_run-{run}_bold"
    nifti_path = func_dir / f"{stem}.nii.gz"
    json_path = func_dir / f"{stem}.json"

    img = nib.Nifti1Image(np.zeros((4, 4, 4, n_vols), dtype="float32"), np.eye(4))
    nib.save(img, str(nifti_path))
    json_path.write_text(json.dumps({"RepetitionTime": 1.0}, indent=2) + "\n")
    return nifti_path, json_path


def test_trims_dummy_volumes(tmp_path):
    nifti_path, json_path = _make_bold(tmp_path, n_vols=17)

    summary = trim_bold_directory(tmp_path)

    assert summary == {
        "trimmed": 1,
        "skipped_already_trimmed": 0,
        "skipped_too_short": 0,
        "errors": 0,
    }

    reloaded = nib.load(str(nifti_path))
    assert reloaded.shape[3] == 17 - N_DUMMY

    sidecar = json.loads(json_path.read_text())
    assert sidecar["NumberOfVolumesDiscardedByUser"] == N_DUMMY


def test_idempotent(tmp_path):
    nifti_path, _ = _make_bold(tmp_path, n_vols=17)

    trim_bold_directory(tmp_path)
    second = trim_bold_directory(tmp_path)

    assert second == {
        "trimmed": 0,
        "skipped_already_trimmed": 1,
        "skipped_too_short": 0,
        "errors": 0,
    }

    reloaded = nib.load(str(nifti_path))
    assert reloaded.shape[3] == 10


def test_skips_too_short(tmp_path):
    nifti_path, json_path = _make_bold(tmp_path, n_vols=5)

    summary = trim_bold_directory(tmp_path)

    assert summary == {
        "trimmed": 0,
        "skipped_already_trimmed": 0,
        "skipped_too_short": 1,
        "errors": 0,
    }

    reloaded = nib.load(str(nifti_path))
    assert reloaded.shape[3] == 5
    sidecar = json.loads(json_path.read_text())
    assert "NumberOfVolumesDiscardedByUser" not in sidecar


def test_subjects_filter(tmp_path):
    s01_nifti, _ = _make_bold(tmp_path, sub="s01", n_vols=17)
    s02_nifti, _ = _make_bold(tmp_path, sub="s02", n_vols=17)

    summary = trim_bold_directory(tmp_path, subjects=["s01"])

    assert summary["trimmed"] == 1
    assert nib.load(str(s01_nifti)).shape[3] == 10
    assert nib.load(str(s02_nifti)).shape[3] == 17


def _make_multi_bold(tmp_path, n_files=4):
    paths = []
    for i in range(n_files):
        ses = "01" if i % 2 == 0 else "02"
        nifti_path, json_path = _make_bold(
            tmp_path, sub="s01", ses=ses, n_vols=17, task=f"t{i}", run=1
        )
        paths.append((nifti_path, json_path))
    return paths


def test_parallel_jobs_trims_all(tmp_path):
    paths = _make_multi_bold(tmp_path, n_files=4)

    summary = trim_bold_directory(tmp_path, jobs=2)

    assert summary == {
        "trimmed": 4,
        "skipped_already_trimmed": 0,
        "skipped_too_short": 0,
        "errors": 0,
    }
    for nifti_path, json_path in paths:
        reloaded = nib.load(str(nifti_path))
        assert reloaded.shape[3] == 10
        sidecar = json.loads(json_path.read_text())
        assert sidecar["NumberOfVolumesDiscardedByUser"] == N_DUMMY


def test_parallel_matches_serial(tmp_path_factory):
    serial_dir = tmp_path_factory.mktemp("serial")
    parallel_dir = tmp_path_factory.mktemp("parallel")

    serial_paths = _make_multi_bold(serial_dir, n_files=5)
    parallel_paths = _make_multi_bold(parallel_dir, n_files=5)

    serial_summary = trim_bold_directory(serial_dir, jobs=1)
    parallel_summary = trim_bold_directory(parallel_dir, jobs=3)

    assert serial_summary == parallel_summary

    for (serial_nifti, _), (parallel_nifti, _) in zip(serial_paths, parallel_paths):
        assert nib.load(str(serial_nifti)).shape == nib.load(str(parallel_nifti)).shape
