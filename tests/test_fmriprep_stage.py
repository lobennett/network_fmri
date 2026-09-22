"""Contracts for direct fMRIPrep execution and consolidation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from network_fmri.config import (
    BehaviorSource,
    BehaviorSources,
    ParticipantsSource,
    ContainerConfig,
    SlurmConfig,
    VerifiedContainerConfig,
    WorkflowConfig,
    WorkflowPaths,
)
from network_fmri.containers import receipt_path, write_subject_receipt
from network_fmri.qa.fmriprep import (
    fmriprep_participant_command,
    fmriprep_subject_receipt,
    verify_fmriprep,
)
from network_fmri.stages import StageError


def configuration(tmp_path: Path) -> WorkflowConfig:
    subjects = tuple(f"s{number}" for number in range(1, 47))
    subjects_file = tmp_path / "subjects.txt"
    subjects_file.write_text("\n".join(subjects) + "\n")
    return WorkflowConfig(
        paths=WorkflowPaths(
            bids_dir=tmp_path / "bids",
            parts_dir=tmp_path / "parts",
            work_dir=tmp_path / "work",
            log_dir=tmp_path / "logs",
            templateflow_dir=tmp_path / "templateflow",
            freesurfer_license=tmp_path / "license.txt",
        ),
        subjects_file=subjects_file,
        subjects=subjects,
        flywheel_project="r01network",
        behavior=BehaviorSources(
            BehaviorSource(tmp_path / "behavior", "a" * 40),
            BehaviorSource(tmp_path / "out-of-scanner", "b" * 40),
        ),
        participants=ParticipantsSource(tmp_path / "demographics", "c" * 40),
        mriqc=ContainerConfig(tmp_path / "mriqc.sif", "24.0.2"),
        fmriprep=ContainerConfig(tmp_path / "fmriprep.sif", "25.2.5"),
        pydeface=VerifiedContainerConfig(tmp_path / "pydeface.sif", "2.1.0", "a" * 64),
        slurm=SlurmConfig("normal", 8, 32, 720, 4),
    )


def option(command: tuple[str, ...], name: str) -> str:
    return command[command.index(name) + 1]


def test_participant_command_preserves_the_trimmed_dataset_contract(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    monkeypatch.setenv("SLURM_TMPDIR", str(tmp_path / "node-tmp"))

    command = fmriprep_participant_command(config, "s3")

    assert command[:4] == ("apptainer", "exec", "--cleanenv", "--containall")
    assert command[command.index(str(config.fmriprep.image)) + 1] == "fmriprep"
    assert option(command, "--dummy-scans") == "0"
    assert "--no-submm-recon" in command
    assert option(command, "--random-seed") == "12345"
    assert option(command, "--output-spaces") == "MNI152NLin2009cAsym:res-2"
    assert command[command.index("--output-spaces") + 1:command.index("--cifti-output")] == (
        "MNI152NLin2009cAsym:res-2", "T1w", "fsnative", "fsaverage6",
    )
    assert option(command, "--fs-license-file") == "/license.txt"
    assert "--skip-bids-validation" in command
    assert option(command, "--omp-nthreads") == "2"
    assert f"{config.paths.freesurfer_license}:/license.txt:ro" in command
    assert f"{tmp_path / 'node-tmp'}:/tmp" in command
    assert (config.paths.bids_dir / "derivatives" / "fmriprep").is_dir()
    assert (config.paths.work_dir / "fmriprep" / "s3").is_dir()


def test_participant_command_never_oversubscribes_a_one_cpu_job(tmp_path):
    config = configuration(tmp_path)
    config = replace(config, slurm=SlurmConfig("normal", 1, 32, 720, 4))

    assert option(fmriprep_participant_command(config, "s3"), "--omp-nthreads") == "1"


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


class GitRunner:
    def __call__(self, args, **kwargs):
        assert args[:2] == ["git", "-C"]
        return type("Completed", (), {"stdout": "a" * 40 + "\n"})()


def _complete_fmriprep(config: WorkflowConfig) -> Path:
    root = config.paths.bids_dir / "derivatives" / "fmriprep"
    _write(root / "dataset_description.json", json.dumps({"DatasetType": "derivative"}))
    for subject in config.subjects:
        _write(config.paths.bids_dir / f"sub-{subject}/ses-01/anat/sub-{subject}_ses-01_T1w.nii.gz", "raw")
        _write(config.paths.bids_dir / f"sub-{subject}/ses-01/func/sub-{subject}_ses-01_task-rest_bold.nii.gz", "raw")
        _write(root / f"sub-{subject}/anat/sub-{subject}_desc-preproc_T1w.nii.gz", "preprocessed")
        _write(root / f"sub-{subject}/ses-01/func/sub-{subject}_ses-01_task-rest_space-T1w_desc-preproc_bold.nii.gz", "preprocessed")
        _write(root / f"sub-{subject}/ses-01/func/sub-{subject}_ses-01_task-rest_space-MNI152NLin2009cAsym_res-2_desc-preproc_bold.nii.gz", "preprocessed")
        _write(root / f"sub-{subject}.html")
        write_subject_receipt(
            receipt_path(root, "fmriprep", subject),
            fmriprep_subject_receipt(config, subject, "a" * 40),
        )
    return root


def test_verification_requires_each_subject_report_description_and_no_crashes(tmp_path):
    config = configuration(tmp_path)
    root = _complete_fmriprep(config)

    result = verify_fmriprep(config, GitRunner())
    assert result.name == "fmriprep-complete"
    assert result.outputs == (root, root / "dataset_description.json")
    assert result.details["subjects"] == 46

    (root / "sub-s2.html").unlink()
    with pytest.raises(StageError, match="missing subject reports"):
        verify_fmriprep(config, GitRunner())


def test_verification_rejects_crash_evidence(tmp_path):
    config = configuration(tmp_path)
    root = _complete_fmriprep(config)
    _write(config.paths.work_dir / "fmriprep" / "s3" / "crash-123.txt")

    with pytest.raises(StageError, match="crash"):
        verify_fmriprep(config, GitRunner())


def test_verification_rejects_empty_imaging_outputs_and_stale_receipts(tmp_path):
    config = configuration(tmp_path)
    root = _complete_fmriprep(config)
    bold = root / "sub-s3/ses-01/func/sub-s3_ses-01_task-rest_space-MNI152NLin2009cAsym_res-2_desc-preproc_bold.nii.gz"
    bold.write_text("")
    with pytest.raises(StageError, match="output-space BOLD"):
        verify_fmriprep(config, GitRunner())

    bold.write_text("preprocessed")
    anatomy = root / "sub-s3/anat/sub-s3_desc-preproc_T1w.nii.gz"
    anatomy.unlink()
    with pytest.raises(StageError, match="preprocessed T1w anatomy"):
        verify_fmriprep(config, GitRunner())

    anatomy.write_text("preprocessed")
    receipt = receipt_path(root, "fmriprep", "s3")
    record = json.loads(receipt.read_text())
    record["container"]["version"] = "old"
    receipt.write_text(json.dumps(record))
    with pytest.raises(StageError, match="stale or missing subject receipts"):
        verify_fmriprep(config, GitRunner())


def test_verification_groups_multi_echo_spaces_and_requires_each_native_echo(tmp_path):
    config = configuration(tmp_path)
    root = _complete_fmriprep(config)
    raw_one = config.paths.bids_dir / "sub-s3/ses-01/func/sub-s3_ses-01_task-rest_run-2_echo-1_bold.nii.gz"
    raw_two = config.paths.bids_dir / "sub-s3/ses-01/func/sub-s3_ses-01_task-rest_run-2_echo-2_bold.nii.gz"
    _write(raw_one, "raw")
    _write(raw_two, "raw")
    _write(
        root / "sub-s3/ses-01/func/sub-s3_ses-01_task-rest_run-2_space-T1w_desc-preproc_bold.nii.gz",
        "preprocessed",
    )
    mni = root / "sub-s3/ses-01/func/sub-s3_ses-01_task-rest_run-2_space-MNI152NLin2009cAsym_res-2_desc-preproc_bold.nii.gz"
    _write(mni, "preprocessed")
    _write(
        root / "sub-s3/ses-01/func/sub-s3_ses-01_task-rest_run-2_echo-1_desc-preproc_bold.nii.gz",
        "preprocessed",
    )
    echo_two = root / "sub-s3/ses-01/func/sub-s3_ses-01_task-rest_run-2_echo-2_desc-preproc_bold.nii.gz"
    _write(echo_two, "preprocessed")
    assert verify_fmriprep(config, GitRunner()).name == "fmriprep-complete"

    echo_two.unlink()
    with pytest.raises(StageError, match="native echo BOLD"):
        verify_fmriprep(config, GitRunner())
