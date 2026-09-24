"""Standalone reconstruction must use unambiguous inputs and fresh outputs."""
import json
from pathlib import Path
import subprocess

import pytest


def anatomy(root, suffix="T1w", session="13", run=1):
    path = root / "sub-s03" / f"ses-{session}" / "anat" / f"sub-s03_ses-{session}_run-{run}_{suffix}.nii.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test anatomy")
    return path


def test_selects_t1_and_t2_across_sessions(tmp_path):
    from network_fmri.freesurfer_app import select_anatomy
    t1 = anatomy(tmp_path)
    t2 = anatomy(tmp_path, "T2w", "04")
    assert select_anatomy(tmp_path, "s03") == (t1, t2)


def test_t2_is_optional(tmp_path):
    from network_fmri.freesurfer_app import select_anatomy
    t1 = anatomy(tmp_path)
    assert select_anatomy(tmp_path, "s03") == (t1, None)


@pytest.mark.parametrize("suffix", ["T1w", "T2w"])
def test_rejects_ambiguous_anatomy(tmp_path, suffix):
    from network_fmri.freesurfer_app import select_anatomy
    anatomy(tmp_path)
    anatomy(tmp_path, suffix, "04")
    anatomy(tmp_path, suffix, "05")
    with pytest.raises(ValueError, match=suffix):
        select_anatomy(tmp_path, "s03")


def test_missing_t1_blocks_processing(tmp_path):
    from network_fmri.freesurfer_app import select_anatomy
    with pytest.raises(ValueError, match="T1w"):
        select_anatomy(tmp_path, "s03")


@pytest.mark.parametrize("subject", ["../s03", "sub-s03", "s03;exit", ""])
def test_invalid_subject_is_rejected(tmp_path, subject):
    from network_fmri.freesurfer_app import select_anatomy
    with pytest.raises(ValueError, match="subject"):
        select_anatomy(tmp_path, subject)


def test_command_uses_t2_and_never_shell_interpolation(tmp_path):
    from network_fmri.freesurfer_app import recon_command
    cmd = recon_command(Path("T1 image.nii.gz"), Path("T2.nii.gz"), "s03", tmp_path, 4)
    assert cmd == ("recon-all", "-s", "sub-s03", "-sd", str(tmp_path), "-i",
                   "T1 image.nii.gz", "-all", "-openmp", "4", "-T2", "T2.nii.gz", "-T2pial")


def test_existing_subject_is_never_resumed(tmp_path):
    from network_fmri.freesurfer_app import recon_command
    (tmp_path / "sub-s03").mkdir()
    with pytest.raises(ValueError, match="exists"):
        recon_command(Path("t1"), None, "s03", tmp_path, 4)


def test_failed_reconstruction_records_failure(tmp_path):
    from network_fmri.freesurfer_app import run_subject
    bids = tmp_path / "bids"
    t1 = anatomy(bids)
    output = tmp_path / "out"
    def runner(command, **kwargs):
        raise subprocess.CalledProcessError(9, command)
    with pytest.raises(subprocess.CalledProcessError):
        run_subject(bids, output, "s03", threads=2, build="freesurfer-8.2.0", runner=runner)
    receipt = json.loads((output / "code" / "sub-s03_reconstruction.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["exit_code"] == 9
    assert receipt["inputs"][0]["path"] == t1.relative_to(bids).as_posix()
    assert receipt["inputs"][0]["sha256"]


def test_wrong_freesurfer_version_is_rejected(tmp_path):
    from network_fmri.freesurfer_app import run_subject
    with pytest.raises(ValueError, match="8.2.0"):
        run_subject(tmp_path, tmp_path / "out", "s03", build="freesurfer-7.3.2")
