from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
CLUSTER = ROOT / "config/mechababs/clusters/sherlock.yaml"
APPS = ROOT / "config/mechababs/apps"


def load(path: Path) -> dict:
    value = yaml.safe_load(path.read_text())
    assert isinstance(value, dict)
    return value


def test_sherlock_profile_uses_campaign_tools_and_node_local_work():
    config = load(CLUSTER)
    preamble = config["script_preamble"]

    assert "{{MECHABABS_VENV}}/bin/activate" in preamble
    assert 'export PYTHONNOUSERSITE=1' in preamble
    assert 'export JOB_TMP=' in preamble
    assert "SLURM_ARRAY_JOB_ID" in preamble
    assert "trap 'rm -rf" in preamble
    assert config["job_compute_space"] == "/scratch/users/${USER}"


def test_mriqc_config_pins_version_image_and_resources():
    config = load(APPS / "mriqc-24.0.2.yaml")

    assert config["mechababs"]["container"] == {
        "source": "{{CONTAINER_DATASET}}",
        "name": "bids-mriqc",
    }
    assert config["zip_foldernames"] == {"mriqc": "24-0-2"}
    assert config["bids_app_args"]["--n_cpus"] == "4"
    assert config["bids_app_args"]["--mem_gb"] == "16"


def test_anatomical_config_produces_reusable_freesurfer_output():
    config = load(APPS / "fmriprep-25.2.5-anatomical.yaml")

    assert config["mechababs"]["container"]["name"] == "bids-fmriprep"
    assert config["bids_app_args"]["--anat-only"] == ""
    assert config["bids_app_args"]["--fs-license-file"] == "{{FREESURFER_LICENSE}}"
    assert config["zip_foldernames"] == {"fMRIPrep-25.2.5+anat": "25-2-5"}


def test_full_config_consumes_anatomical_derivative_and_surfaces():
    config = load(APPS / "fmriprep-25.2.5-full.yaml")
    upstream = config["input_datasets"]["fMRIPrep-25.2.5+anat"]

    assert upstream["is_zipped"] is True
    assert upstream["path_in_babs"] == "sourcedata/fMRIPrep-25.2.5+anat"
    assert upstream["required_files"] == ["*fMRIPrep-25.2.5+anat*.zip"]
    assert "fMRIPrep-25.2.5+anat" in config["bids_app_args"]["--fs-subjects-dir"]
    assert config["bids_app_args"]["--level"] == "full"
    assert config["zip_foldernames"] == {"fMRIPrep-25.2.5+full": "25-2-5"}


def test_all_apps_use_study_layout_and_isolated_container_runtime():
    for path in sorted(APPS.glob("*.yaml")):
        config = load(path)
        assert config["analysis_path"] == "."
        assert config["input_ria_path"] == ".babs/input_ria"
        assert config["output_ria_path"] == ".babs/output_ria"
        assert config["all_results_in_one_zip"] is True
        assert "--containall" in config["singularity_args"]
        assert '-B $JOB_TMP:/tmp' in config["singularity_args"]
