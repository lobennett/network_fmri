"""Execute the published campaign patch against its pinned source fixtures."""

import contextlib
import csv
import importlib
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
        ["git", "apply", str(SNAPSHOT / "mechababs-local-patches.diff")],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    )
    return target


def config(name):
    return yaml.safe_load((SNAPSHOT / name).read_text())


@contextlib.contextmanager
def mechababs_package(root):
    """Import the reconstructed package, then unload it so the next case re-imports."""
    sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    try:
        yield importlib.import_module("mechababs.iterate")
    finally:
        sys.path.remove(str(root))
        for name in [n for n in sys.modules if n.split(".")[0] == "mechababs"]:
            del sys.modules[name]


def synthetic_campaign(tmp_path, pipelines, ds_id):
    """A campaign root holding the snapshot configs and one cloned study wrapper."""
    campaign = tmp_path / "campaign"
    for name in (*pipelines, "sherlock.yaml"):
        destination = campaign / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((SNAPSHOT / name).read_text())
    study = campaign / "studies" / f"study-{ds_id}"
    (study / "sourcedata").mkdir(parents=True)
    (study / "sourcedata" / "sourcedata+subjects+sessions.tsv").write_text(
        (FIXTURES / "sessions.tsv").read_text()
    )
    (study / ".gitmodules").write_text(
        f'[submodule "sourcedata/{ds_id}"]\n'
        f"\tpath = sourcedata/{ds_id}\n"
        f"\turl = https://example.invalid/{ds_id}.git\n"
    )
    return campaign


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


@pytest.mark.parametrize(
    "pipeline,processing_level,chained",
    [
        ("MRIQC-24.0.2.yaml", "session", False),
        ("fMRIPrep-25.2.5.yaml", "subject", False),
        ("XCP-D-26.0.2.yaml", "subject", True),
    ],
)
def test_scaffold_builds_babs_init_from_pipeline_level_and_cluster_throttle(
    reconstructed,
    tmp_path,
    monkeypatch,
    pipeline,
    processing_level,
    chained,
):
    pipelines = ["MRIQC-24.0.2.yaml", "fMRIPrep-25.2.5.yaml", "XCP-D-26.0.2.yaml"]
    ds_id = "ds-example"
    short = pipeline.removesuffix(".yaml")
    campaign = synthetic_campaign(tmp_path, pipelines, ds_id)
    # The ledger says session; only the pipeline YAML can raise a cell to subject.
    row = {"dataset_id": ds_id, "processing_level": "session"}
    if chained:
        row["fMRIPrep-25.2.5_babs"] = f"studies/study-{ds_id}/derivatives/fMRIPrep-25.2.5"
        row["fMRIPrep-25.2.5_babs-merged"] = "merged"
    cfg = {"venv": "venv", "cluster": "sherlock.yaml", "pipelines": pipelines}

    commands = []
    with mechababs_package(reconstructed) as iterate:
        monkeypatch.setattr(iterate, "run", lambda cmd, **kw: commands.append(list(map(str, cmd))))
        update = iterate.scaffold(campaign, cfg, row, short, pipeline, dry_run=True)

    assert update == {f"{short}_babs": f"studies/study-{ds_id}/derivatives/{short}"}
    babs_init = next(c[c.index("duct") + 1 :] for c in commands if "duct" in c)
    assert babs_init[:3] == ["babs", "init", f"studies/study-{ds_id}/derivatives/{short}"]
    assert babs_init[babs_init.index("--processing-level") + 1] == processing_level
    assert babs_init[babs_init.index("--throttle") + 1] == "8"
    if chained:
        assert "--list-sub-file" not in babs_init
    else:
        assert (
            babs_init[babs_init.index("--list-sub-file") + 1]
            == f".mechababs/inclusions/{ds_id}_{short}.csv"
        )
