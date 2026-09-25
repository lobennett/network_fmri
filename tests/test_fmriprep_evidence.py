"""Report extraction detects incomplete/misaligned outputs without reading voxel data."""

import gzip
import io
import json
import zipfile

import nibabel as nib
import numpy as np
import pytest


def bold(volumes):
    image = nib.Nifti1Image(np.zeros((2, 2, 2, volumes), dtype="float32"), np.eye(4))
    image.header.set_zooms((1, 1, 1, 1.49))
    image.header.set_xyzt_units("mm", "sec")
    return gzip.compress(image.to_bytes())


def archive(path, count=5, confounds=5, unsafe=False):
    prefix = "fMRIPrep/sub-s03/ses-01/func/sub-s03_ses-01_task-rest_run-1"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(prefix + "_space-T1w_desc-preproc_bold.nii.gz", bold(count))
        z.writestr(
            prefix + "_desc-confounds_timeseries.tsv",
            "framewise_displacement\n" + "0\n" * confounds,
        )
        z.writestr(
            "fMRIPrep/sub-s03.html", '<html><img src="sub-s03/figures/plot.svg"></html>'
        )
        z.writestr("fMRIPrep/sub-s03/figures/plot.svg", "<svg/>")
        if unsafe:
            z.writestr("fMRIPrep/../escape.html", "bad")


def test_checks_lengths_and_extracts_reports_without_copying_bold(tmp_path):
    from network_fmri.fmriprep_evidence import inspect_archive

    source = tmp_path / "output.zip"
    archive(source)
    expected = {"sub-s03_ses-01_task-rest_run-1": {"volumes": 5, "tr": 1.49}}
    result = inspect_archive(
        source, "s03", expected, tmp_path / "review", require_cifti=False
    )
    assert result["issues"] == []
    assert result["runs"][0]["confound_rows"] == 5
    assert result["runs"][0]["outputs"][0]["volumes"] == 5
    assert (tmp_path / "review/sub-s03.html").exists()
    assert (tmp_path / "review/sub-s03/figures/plot.svg").exists()
    assert not list((tmp_path / "review").rglob("*.nii.gz"))


def test_mismatches_and_missing_runs_are_explicit(tmp_path):
    from network_fmri.fmriprep_evidence import inspect_archive

    source = tmp_path / "output.zip"
    archive(source, count=4, confounds=3)
    expected = {
        f"sub-s03_ses-01_task-rest_run-{run}": {"volumes": 5, "tr": 1.49}
        for run in (1, 2)
    }
    result = inspect_archive(
        source, "s03", expected, tmp_path / "review", require_cifti=True
    )
    assert any("BOLD volume count" in x for x in result["issues"])
    assert any("confound rows" in x for x in result["issues"])
    assert any("CIFTI" in x for x in result["issues"])
    assert any("run-2" in x for x in result["issues"])


def test_unsafe_zip_does_not_write_any_reports(tmp_path):
    from network_fmri.fmriprep_evidence import inspect_archive

    source = tmp_path / "output.zip"
    archive(source, unsafe=True)
    with pytest.raises(ValueError, match="unsafe"):
        inspect_archive(source, "s03", {}, tmp_path / "review")
    assert not (tmp_path / "review").exists()


def test_cifti_axis_length_and_tr_are_checked(tmp_path):
    from network_fmri.fmriprep_evidence import inspect_archive

    path = tmp_path / "output.zip"
    archive(path)
    axes = (
        nib.cifti2.SeriesAxis(0, 1.49, 5),
        nib.cifti2.BrainModelAxis.from_mask(np.ones(3, dtype=bool), name="CortexLeft"),
    )
    image = nib.Cifti2Image(
        np.zeros((5, 3), dtype="float32"),
        header=nib.cifti2.Cifti2Header.from_axes(axes),
    )
    with zipfile.ZipFile(path, "a") as z:
        z.writestr(
            "fMRIPrep/sub-s03/ses-01/func/sub-s03_ses-01_task-rest_run-1_space-fsLR_den-91k_bold.dtseries.nii",
            image.to_bytes(),
        )
    result = inspect_archive(
        path,
        "s03",
        {"sub-s03_ses-01_task-rest_run-1": {"volumes": 5, "tr": 1.49}},
        tmp_path / "review",
    )
    assert result["issues"] == []
    assert {f["kind"] for f in result["runs"][0]["outputs"]} == {"BOLD", "CIFTI"}


def test_restart_preserves_reports_and_rejects_tampering(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tests.test_mriqc_evidence import Commands, git, init, save, register_source
    from network_fmri import fmriprep_evidence as evidence

    study = tmp_path / "study"
    init(study, "study")
    raw = study / "sourcedata/raw"
    init(raw, "raw")
    file = raw / "sub-s03/ses-01/func/sub-s03_ses-01_task-rest_run-1_echo-2_bold.nii.gz"
    file.parent.mkdir(parents=True)
    file.write_bytes(bold(5))
    file.with_name(file.name.replace(".nii.gz", ".json")).write_text(
        json.dumps({"RepetitionTime": 1.49, "NumberOfVolumesDiscardedByUser": 7})
    )
    save(raw)
    source = study / "derivatives/fMRIPrep-25.2.5+full+pilot"
    init(source, "fmri")
    git(
        source,
        "-c",
        "protocol.file.allow=always",
        "clone",
        "-q",
        str(raw),
        "sourcedata/raw",
    )
    archive(source / "sub-s03_output.zip")
    save(source)
    save(study)
    config = SimpleNamespace(
        subjects=("s03",), mechababs=SimpleNamespace(study_dir=study, raw_slot="raw")
    )
    register_source(config, source)
    stage = SimpleNamespace(
        stage="fmriprep", state="complete", project=source.relative_to(study).as_posix()
    )
    monkeypatch.setattr(
        evidence,
        "ProcessingManager",
        lambda *a, **kw: SimpleNamespace(plan=lambda: [stage]),
    )
    runner = Commands()
    output = evidence.prepare_fmriprep_review(config, runner=runner)
    report = output / "sub-s03.html"
    original = report.read_bytes()
    receipt = json.loads((output / evidence.RECEIPT).read_text())
    assert receipt["status"] == "issues"  # Missing CIFTI must not be marked complete.
    assert evidence.prepare_fmriprep_review(config, runner=runner) == output
    assert report.read_bytes() == original
    report.write_text("modified")
    with pytest.raises(RuntimeError):
        evidence.prepare_fmriprep_review(config, runner=runner)


def test_raw_echo_download_and_direction_distinguished_runs(tmp_path):
    from network_fmri.fmriprep_evidence import expected_runs

    raw = tmp_path / "raw"
    folder = raw / "sub-s03/ses-01/func"
    folder.mkdir(parents=True)
    objects = tmp_path / "objects"
    objects.mkdir()
    for direction in ("AP", "PA"):
        path = (
            folder
            / f"sub-s03_ses-01_task-rest_dir-{direction}_run-1_echo-2_bold.nii.gz"
        )
        path.symlink_to(objects / f"{direction}.nii.gz")
        path.with_name(path.name.replace(".nii.gz", ".json")).write_text(
            json.dumps({"RepetitionTime": 1.49})
        )

    def download(command, **kwargs):
        assert command[:4] == ("datalad", "get", "-d", str(raw))
        for name in command[4:]:
            Path(name).resolve().write_bytes(bold(5))

    from pathlib import Path

    results = expected_runs(raw, "s03", runner=download)
    assert set(results) == {
        "sub-s03_ses-01_task-rest_dir-AP_run-1",
        "sub-s03_ses-01_task-rest_dir-PA_run-1",
    }
