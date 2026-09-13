"""Execute the published campaign patch against its pinned source fixtures."""

import csv
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs" / "campaign"
FIXTURES = Path(__file__).parent / "fixtures" / "campaign"


@pytest.fixture
def reconstructed(tmp_path):
    target = tmp_path / "mechababs"
    shutil.copytree(FIXTURES / "mechababs-base", target)
    # Give git apply its own root even when pytest's temp directory is in a checkout.
    subprocess.run(["git", "init", "-q", str(target)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "apply",
            "--include=mechababs/select.py",
            "--include=merge_config.py",
            "--include=study_meta.py",
            str(SNAPSHOT / "mechababs-local-patches.diff"),
        ],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    )
    return target


def config(name):
    return yaml.safe_load((SNAPSHOT / name).read_text())


def test_mriqc_inclusion_excludes_fieldmap_only_sessions(reconstructed, tmp_path):
    select = runpy.run_path(str(reconstructed / "mechababs" / "select.py"))
    output = tmp_path / "inclusion.csv"
    select["generate_inclusion"](
        (FIXTURES / "sessions.tsv").read_text(),
        config("MRIQC-24.0.2.yaml")["mechababs"]["selection"],
        output,
        processing_level="session",
    )
    with output.open() as stream:
        assert list(csv.DictReader(stream)) == [
            {"sub_id": "sub-anat", "ses_id": "ses-01"},
            {"sub_id": "sub-func", "ses_id": "ses-01"},
            {"sub_id": "sub-mixed", "ses_id": "ses-01"},
            {"sub_id": "sub-mixed", "ses_id": "ses-02"},
        ]


def test_subject_selection_keeps_required_counts(reconstructed, tmp_path):
    select = runpy.run_path(str(reconstructed / "mechababs" / "select.py"))
    output = tmp_path / "inclusion.csv"
    select["generate_inclusion"](
        (FIXTURES / "sessions.tsv").read_text(),
        config("XCP-D-26.0.2.yaml")["mechababs"]["selection"],
        output,
        processing_level="subject",
    )
    with output.open() as stream:
        assert list(csv.DictReader(stream)) == [{"sub_id": "sub-mixed"}]


def test_study_metadata_regenerates_from_multi_echo_bids(reconstructed, tmp_path):
    bids = tmp_path / "bids"
    files = [
        "sub-example/ses-01/anat/sub-example_ses-01_T1w.nii.gz",
        "sub-example/ses-01/anat/sub-example_ses-01_T2w.nii.gz",
        "sub-example/ses-02/func/sub-example_ses-02_task-test_echo-1_bold.nii.gz",
        "sub-example/ses-02/func/sub-example_ses-02_task-test_echo-2_bold.nii.gz",
        "sub-fmap/ses-01/fmap/sub-fmap_ses-01_epi.nii.gz",
    ]
    for name in files:
        path = bids / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    output = tmp_path / "metadata"
    output.mkdir()
    command = [
        sys.executable,
        str(reconstructed / "study_meta.py"),
        "--bids-dir",
        str(bids),
        "--out",
        str(output),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    with (output / "sourcedata+subjects.tsv").open() as stream:
        assert list(csv.DictReader(stream, delimiter="\t")) == [
            {
                "subject_id": "sub-example",
                "datatypes": "anat,func",
                "t1w_num": "1",
                "t2w_num": "1",
                "bold_num": "1",
            },
            {
                "subject_id": "sub-fmap",
                "datatypes": "fmap",
                "t1w_num": "0",
                "t2w_num": "0",
                "bold_num": "0",
            },
        ]
    (bids / files[0]).unlink()
    subprocess.run(command, check=True, capture_output=True, text=True)
    with (output / "sourcedata+subjects+sessions.tsv").open() as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert len(rows) == 3
    assert rows[0]["t1w_num"] == "0"


@pytest.mark.parametrize(
    "name,primary,runtime",
    [
        ("MRIQC-24.0.2.yaml", "BIDS", "24:00:00"),
        ("fMRIPrep-25.2.5.yaml", "BIDS", "48:00:00"),
        ("XCP-D-26.0.2.yaml", "fMRIPrep-25.2.5", "48:00:00"),
    ],
)
def test_generated_babs_config_preserves_campaign_behavior(
    reconstructed,
    name,
    primary,
    runtime,
):
    command = [
        sys.executable,
        str(reconstructed / "merge_config.py"),
        "--pipeline",
        str(SNAPSHOT / name),
        "--cluster",
        str(SNAPSHOT / "sherlock.yaml"),
        "--dataset-url",
        "/example/bids",
        "--campaign-venv",
        "/example/venv",
    ]
    if primary != "BIDS":
        command += ["--input-origin", f"{primary}=ria+file:///example/output"]
    merged = yaml.safe_load(
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    assert next(iter(merged["input_datasets"])) == primary
    assert merged["cluster_resources"]["hard_runtime_limit"] == runtime
    assert "/example/venv/bin/activate" in merged["script_preamble"]
    assert config("sherlock.yaml")["array_throttle"] == 8
    assert (
        not {"mechababs", "array_throttle", "cluster_resources_override"}
        & merged.keys()
    )
    if primary != "BIDS":
        assert (
            merged["input_datasets"][primary]["origin_url"]
            == "ria+file:///example/output"
        )
        assert merged["bids_app_args"]["--abcc-qc"] == "n"
        assert merged["bids_app_args"]["--warp-surfaces-native2std"] == ""
        assert merged["bids_app_args"]["--fs-license-file"] == "${HOME}/license.txt"
        assert merged["pre_app_commands"] == config(name)["pre_app_commands"]
    elif name.startswith("MRIQC"):
        assert merged["bids_app_args"]["--fd_thres"] == "0.5"
