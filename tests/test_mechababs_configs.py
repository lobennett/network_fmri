from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
CLUSTER = ROOT / "src/network_fmri/mechababs/clusters/sherlock.yaml"
APPS = ROOT / "src/network_fmri/mechababs/apps"


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
    config = load(APPS / "MRIQC-24.0.2.yaml")

    assert config["mechababs"]["container"] == {
        "source": "{{CONTAINER_DATASET}}",
        "name": "bids-mriqc",
    }
    assert config["zip_foldernames"] == {"MRIQC-24.0.2": "24-0-2"}
    assert config["bids_app_args"]["--n_cpus"] == "4"
    assert config["bids_app_args"]["--mem_gb"] == "16"
    assert config["bids_app_args"]["--fd_thres"] == "0.5"


def test_anatomical_config_produces_reusable_freesurfer_output():
    config = load(APPS / "fMRIPrep-25.2.5+anat.yaml")

    assert config["mechababs"]["container"]["name"] == "bids-fmriprep"
    assert config["mechababs"]["depends_on"] == "MRIQC-24.0.2"
    assert config["bids_app_args"]["--anat-only"] == ""
    assert config["bids_app_args"]["--fs-license-file"] == "{{FREESURFER_LICENSE}}"
    assert config["zip_foldernames"] == {"fMRIPrep-25.2.5+anat": "25-2-5"}


def test_full_config_consumes_anatomical_derivative_and_surfaces():
    config = load(APPS / "fMRIPrep-25.2.5+full.yaml")
    upstream = config["input_datasets"]["FreeSurfer-8.2.0"]

    assert config["mechababs"]["depends_on"] == "FreeSurfer-8.2.0"
    assert upstream["is_zipped"] is True
    assert upstream["path_in_babs"] == "sourcedata/FreeSurfer-8.2.0"
    assert upstream["required_files"] == ["*FreeSurfer-8.2.0*.zip"]
    assert "FreeSurfer-8.2.0" in config["bids_app_args"]["--fs-subjects-dir"]
    assert config["bids_app_args"]["--fs-no-resume"] == ""
    assert config["bids_app_args"]["--no-track-sessions"] == ""
    assert config["bids_app_args"]["--fs-subjects-dir"].endswith("/subjects")
    assert config["bids_app_args"]["--level"] == "full"
    assert config["bids_app_args"]["--dummy-scans"] == "0"
    assert "--no-submm-recon" in config["bids_app_args"]
    assert config["bids_app_args"]["--output-spaces"] == "MNI152NLin2009cAsym:res-2 T1w fsnative fsaverage6"
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


def test_standalone_freesurfer_config():
    config = load(APPS / "FreeSurfer-8.2.0.yaml")
    assert config["mechababs"]["container"]["name"] == "bids-freesurfer"
    assert config["mechababs"]["depends_on"] == "MRIQC-24.0.2"
    assert "--anat-only" not in config["bids_app_args"]
    assert config["zip_foldernames"] == {"FreeSurfer-8.2.0": "8-2-0"}
