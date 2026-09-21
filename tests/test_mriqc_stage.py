"""Contracts for direct MRIQC execution and consolidation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from network_fmri.config import (
    BehaviorSource,
    BehaviorSources,
    ContainerConfig,
    SlurmConfig,
    VerifiedContainerConfig,
    WorkflowConfig,
    WorkflowPaths,
)
from network_fmri.containers import (
    bind,
    group_receipt_path,
    receipt_path,
    write_subject_receipt,
)
from network_fmri.qa.mriqc import (
    mriqc_group_command,
    mriqc_group_receipt,
    mriqc_participant_command,
    mriqc_subject_receipt,
    verify_mriqc,
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
        mriqc=ContainerConfig(tmp_path / "mriqc.sif", "24.0.2"),
        fmriprep=ContainerConfig(tmp_path / "fmriprep.sif", "25.2.5"),
        pydeface=VerifiedContainerConfig(tmp_path / "pydeface.sif", "2.1.0", "a" * 64),
        slurm=SlurmConfig("normal", 8, 32, 720, 4),
    )


def option(command: tuple[str, ...], name: str) -> str:
    return command[command.index(name) + 1]


def test_participant_command_uses_approved_motion_contract_and_safe_binds(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    monkeypatch.setenv("SLURM_TMPDIR", str(tmp_path / "node-tmp"))

    command = mriqc_participant_command(config, "s3")

    assert command[:4] == ("apptainer", "exec", "--cleanenv", "--containall")
    assert command[command.index(str(config.mriqc.image)) + 1] == "mriqc"
    assert command[-1] == "--no-datalad-get"
    assert option(command, "--fd_thres") == "0.5"
    assert option(command, "--n_cpus") == "8"
    assert option(command, "--mem_gb") == "32"
    assert "--no-sub" in command
    assert "/data" in command and "/out" in command and "participant" in command
    assert f"{config.paths.bids_dir}:/data:ro" in command
    assert f"{config.paths.templateflow_dir}:/templateflow:ro" in command
    assert f"{tmp_path / 'node-tmp'}:/tmp" in command
    assert (config.paths.bids_dir / "derivatives" / "mriqc").is_dir()
    assert (config.paths.work_dir / "mriqc" / "s3").is_dir()


def test_group_command_uses_the_same_isolated_container_contract(tmp_path):
    command = mriqc_group_command(configuration(tmp_path))

    assert command[-6:] == ("mriqc", "/data", "/out", "group", "--no-sub", "--no-datalad-get")
    assert "--participant-label" not in command


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


class GitRunner:
    def __call__(self, args, **kwargs):
        assert args[:2] == ["git", "-C"]
        return type("Completed", (), {"stdout": "a" * 40 + "\n"})()


def _complete_mriqc(config: WorkflowConfig) -> Path:
    bids = config.paths.bids_dir
    root = bids / "derivatives" / "mriqc"
    _write(root / "dataset_description.json", json.dumps({"DatasetType": "derivative"}))
    _write(root / "group_bold.html")
    _write(root / "group_bold.tsv")
    _write(root / "group_T1w.html")
    _write(root / "group_T1w.tsv")
    for subject in config.subjects:
        anatomy = Path(f"sub-{subject}/ses-01/anat/sub-{subject}_ses-01_T1w")
        _write(bids / anatomy.with_suffix(".nii.gz"))
        _write(root / anatomy.with_suffix(".json"), "{}")
        _write(root / f"{anatomy.name}.html")
        relative = Path(f"sub-{subject}/ses-01/func/sub-{subject}_ses-01_task-rest_run-1_bold")
        _write(bids / relative.with_suffix(".nii.gz"))
        _write(root / relative.with_suffix(".json"), json.dumps({
            "provenance": {"settings": {"fd_thres": 0.5}},
        }))
        _write(root / f"{relative.name}.html")
        write_subject_receipt(
            receipt_path(root, "mriqc", subject),
            mriqc_subject_receipt(config, subject, "a" * 40),
        )
    write_subject_receipt(
        group_receipt_path(root, "mriqc"), mriqc_group_receipt(config, "a" * 40)
    )
    return root


def test_verification_requires_complete_iqms_reports_group_outputs_and_no_crashes(tmp_path):
    config = configuration(tmp_path)
    root = _complete_mriqc(config)

    result = verify_mriqc(config, GitRunner())
    assert result.name == "mriqc-complete"
    assert result.outputs == (root, root / "dataset_description.json")
    assert result.details["subjects"] == 46

    (root / "sub-s2/ses-01/func/sub-s2_ses-01_task-rest_run-1_bold.json").unlink()
    with pytest.raises(StageError, match="missing IQMs"):
        verify_mriqc(config, GitRunner())


def test_verification_rejects_crash_evidence(tmp_path):
    config = configuration(tmp_path)
    root = _complete_mriqc(config)
    _write(config.paths.work_dir / "mriqc" / "s3" / "crash-123.txt")

    with pytest.raises(StageError, match="crash"):
        verify_mriqc(config, GitRunner())


def test_verification_rejects_stale_receipts_and_wrong_iqm_provenance(tmp_path):
    config = configuration(tmp_path)
    root = _complete_mriqc(config)
    receipt = receipt_path(root, "mriqc", "s3")
    record = json.loads(receipt.read_text())
    record["input_datalad_commit"] = "b" * 40
    receipt.write_text(json.dumps(record))

    with pytest.raises(StageError, match="stale or missing subject receipts"):
        verify_mriqc(config, GitRunner())

    receipt.write_text(json.dumps(mriqc_subject_receipt(config, "s3", "a" * 40)))
    iqm = root / "sub-s3/ses-01/func/sub-s3_ses-01_task-rest_run-1_bold.json"
    iqm.write_text(json.dumps({"provenance": {"settings": {"fd_thres": 0.2}}}))
    with pytest.raises(StageError, match="fd_thres=0.5"):
        verify_mriqc(config, GitRunner())


def test_anatomical_iqms_need_valid_json_but_no_motion_threshold(tmp_path):
    config = configuration(tmp_path)
    root = _complete_mriqc(config)
    anatomy = root / "sub-s3/ses-01/anat/sub-s3_ses-01_T1w.json"
    anatomy.write_text("[]")
    with pytest.raises(StageError, match="IQMs are malformed"):
        verify_mriqc(config, GitRunner())

    anatomy.write_text("{}")
    assert verify_mriqc(config, GitRunner()).name == "mriqc-complete"


def test_verification_requires_a_current_group_receipt(tmp_path):
    config = configuration(tmp_path)
    root = _complete_mriqc(config)
    group_receipt_path(root, "mriqc").unlink()

    with pytest.raises(StageError, match="group receipt"):
        verify_mriqc(config, GitRunner())


@pytest.mark.parametrize("host,destination", [
    (Path("relative"), "/data"), (Path("/host:bad"), "/data"),
    (Path("/host"), "/bad,name"), (Path("/host"), "relative"),
])
def test_bind_rejects_unsafe_or_relative_paths(host, destination):
    with pytest.raises(ValueError, match="absolute|cannot contain"):
        bind(host, destination)
