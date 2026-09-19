"""Contracts for direct MRIQC execution and consolidation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from network_fmri.config import (
    BehaviorSource,
    ContainerConfig,
    SlurmConfig,
    WorkflowConfig,
    WorkflowPaths,
)
from network_fmri.qa.mriqc import mriqc_group_command, mriqc_participant_command, verify_mriqc
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
        behavior=BehaviorSource(tmp_path / "behavior", "a" * 40),
        mriqc=ContainerConfig(tmp_path / "mriqc.sif", "24.0.2"),
        fmriprep=ContainerConfig(tmp_path / "fmriprep.sif", "25.2.5"),
        slurm=SlurmConfig("normal", 8, 32, 720, 4),
    )


def option(command: tuple[str, ...], name: str) -> str:
    return command[command.index(name) + 1]


def test_participant_command_uses_approved_motion_contract_and_safe_binds(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    monkeypatch.setenv("SLURM_TMPDIR", str(tmp_path / "node-tmp"))

    command = mriqc_participant_command(config, "s3")

    assert command[:4] == ("apptainer", "exec", "--cleanenv", "--containall")
    assert command[-1] == "--no-datalad-get"
    assert option(command, "--fd_thres") == "0.5"
    assert "--no-sub" in command
    assert "/data" in command and "/out" in command and "participant" in command
    assert f"{config.paths.bids_dir}:/data:ro" in command
    assert f"{config.paths.templateflow_dir}:/templateflow:ro" in command
    assert f"{tmp_path / 'node-tmp'}:/tmp" in command


def test_group_command_uses_the_same_isolated_container_contract(tmp_path):
    command = mriqc_group_command(configuration(tmp_path))

    assert command[-5:] == ("/data", "/out", "group", "--no-sub", "--no-datalad-get")
    assert "--participant-label" not in command


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _complete_mriqc(config: WorkflowConfig) -> Path:
    bids = config.paths.bids_dir
    root = bids / "derivatives" / "mriqc"
    _write(root / "dataset_description.json", json.dumps({"DatasetType": "derivative"}))
    _write(root / "group_bold.html")
    _write(root / "group_bold.tsv")
    for subject in config.subjects:
        relative = Path(f"sub-{subject}/ses-01/func/sub-{subject}_ses-01_task-rest_run-1_bold")
        _write(bids / relative.with_suffix(".nii.gz"))
        _write(root / relative.with_suffix(".json"), "{}")
        _write(root / relative.with_suffix(".html"))
    return root


def test_verification_requires_complete_iqms_reports_group_outputs_and_no_crashes(tmp_path):
    config = configuration(tmp_path)
    root = _complete_mriqc(config)

    result = verify_mriqc(config)
    assert result.name == "mriqc-complete"
    assert result.outputs == (root, root / "dataset_description.json")
    assert result.details["subjects"] == 46

    (root / "sub-s2/ses-01/func/sub-s2_ses-01_task-rest_run-1_bold.json").unlink()
    with pytest.raises(StageError, match="missing IQMs"):
        verify_mriqc(config)


def test_verification_rejects_crash_evidence(tmp_path):
    config = configuration(tmp_path)
    root = _complete_mriqc(config)
    _write(config.paths.work_dir / "mriqc" / "s3" / "crash-123.txt")

    with pytest.raises(StageError, match="crash"):
        verify_mriqc(config)
