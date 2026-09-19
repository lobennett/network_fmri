from pathlib import Path
from types import SimpleNamespace

import pytest

from network_fmri.config import (
    BehaviorSource,
    ContainerConfig,
    SlurmConfig,
    WorkflowConfig,
    WorkflowPaths,
)
from network_fmri.stages import StageError
from network_fmri.stages.assembly import assemble_dataset, convert_subject


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.calls.append([str(value) for value in args])
        return SimpleNamespace(stdout="")


def configuration(tmp_path: Path) -> WorkflowConfig:
    paths = WorkflowPaths(
        bids_dir=tmp_path / "bids",
        parts_dir=tmp_path / "parts",
        work_dir=tmp_path / "work",
        log_dir=tmp_path / "logs",
        templateflow_dir=tmp_path / "templateflow",
        freesurfer_license=tmp_path / "license.txt",
    )
    subjects = tuple(f"s{number}" for number in range(1, 47))
    subjects_file = tmp_path / "subjects.txt"
    subjects_file.write_text("\n".join(subjects) + "\n")
    return WorkflowConfig(
        paths=paths,
        subjects_file=subjects_file,
        subjects=subjects,
        flywheel_project="russpold/r01network",
        behavior=BehaviorSource(tmp_path / "canonical-behavior", "a" * 40),
        mriqc=ContainerConfig(tmp_path / "mriqc.sif", "24.0.2"),
        fmriprep=ContainerConfig(tmp_path / "fmriprep.sif", "25.2.5"),
        slurm=SlurmConfig("normal", 8, 32, 720, 4),
    )


def test_conversion_uses_environment_token_without_putting_it_on_command_line(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    runner = RecordingRunner()
    monkeypatch.setenv("FLYWHEEL_API_TOKEN", "private-token")

    result = convert_subject(config, "s3", runner)

    command = runner.calls[0]
    assert command[:1] == ["network-fw2bids"]
    assert "--subject" in command
    assert command[command.index("--subject") + 1] == "s3"
    assert "FLYWHEEL_API_TOKEN" not in " ".join(command)
    assert "private-token" not in " ".join(command)
    assert result.outputs == (tmp_path / "parts" / "s3",)


def test_conversion_rejects_a_subject_outside_the_exact_roster(tmp_path):
    with pytest.raises(StageError, match="46-subject roster"):
        convert_subject(configuration(tmp_path), "s999", RecordingRunner())


def test_assembly_requires_exact_part_roster_and_uses_upstream_atomic_assembler(tmp_path):
    config = configuration(tmp_path)
    config.paths.parts_dir.mkdir()
    for subject in config.subjects:
        (config.paths.parts_dir / subject).mkdir()
    runner = RecordingRunner()

    result = assemble_dataset(config, runner)

    assert result.name == "bids-assembled"
    assert result.details["subject_count"] == 46
    assert runner.calls == [
        [
            __import__("sys").executable,
            "-m",
            "network_fw2bids._assembly",
            "--subjects",
            str(config.subjects_file),
            "--parts",
            str(config.paths.parts_dir),
            "--output",
            str(config.paths.bids_dir),
        ]
    ]


def test_assembly_refuses_partial_or_extra_part_rosters(tmp_path):
    config = configuration(tmp_path)
    config.paths.parts_dir.mkdir()
    for subject in config.subjects[:-1]:
        (config.paths.parts_dir / subject).mkdir()
    (config.paths.parts_dir / "s999").mkdir()

    with pytest.raises(StageError, match="missing s46; unexpected s999"):
        assemble_dataset(config, RecordingRunner())
