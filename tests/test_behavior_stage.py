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
from network_fmri.stages.behavior import copy_content, ingest_behavior


class GitRunner:
    def __init__(self, head: str) -> None:
        self.head = head
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        command = [str(value) for value in args]
        self.calls.append(command)
        if command[:2] == ["git", "-C"]:
            return SimpleNamespace(stdout=self.head + "\n")
        return SimpleNamespace(stdout="")


def configuration(tmp_path: Path, source: Path) -> WorkflowConfig:
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
        behavior=BehaviorSource(source, "a" * 40),
        mriqc=ContainerConfig(tmp_path / "mriqc.sif", "24.0.2"),
        fmriprep=ContainerConfig(tmp_path / "fmriprep.sif", "25.2.5"),
        slurm=SlurmConfig("normal", 8, 32, 720, 4),
    )


def canonical_source(tmp_path: Path) -> Path:
    source = tmp_path / "canonical-behavior"
    file = source / "sub-s1" / "ses-01" / "beh" / "sub-s1_ses-01_task-test_run-1_beh.csv"
    file.parent.mkdir(parents=True)
    file.write_text("onset\n1\n")
    (source / "behavioral_exceptions.tsv").write_text("subject\tsession\ttask\trun\treason\n")
    (source / ".git").mkdir()
    (source / ".git" / "config").write_text("do not copy\n")
    return source


def test_ingest_refuses_wrong_canonical_commit(tmp_path):
    source = canonical_source(tmp_path)
    runner = GitRunner("f" * 40)

    with pytest.raises(StageError, match="canonical behavior commit"):
        ingest_behavior(configuration(tmp_path, source), runner)

    assert not (tmp_path / "bids" / "sourcedata" / "behavioral").exists()


def test_ingest_dereferences_content_audits_and_records_source_commit(tmp_path):
    source = canonical_source(tmp_path)
    original = source / "sub-s1" / "ses-01" / "beh" / "sub-s1_ses-01_task-test_run-1_beh.csv"
    external = tmp_path / "annex-content.csv"
    external.write_text(original.read_text())
    original.unlink()
    original.symlink_to(external)
    config = configuration(tmp_path, source)
    runner = GitRunner("a" * 40)

    result = ingest_behavior(config, runner)

    destination = config.paths.bids_dir / "sourcedata" / "behavioral"
    copied = destination / original.relative_to(source)
    assert copied.read_text() == "onset\n1\n"
    assert not copied.is_symlink()
    assert not (destination / ".git").exists()
    assert runner.calls[-1] == [
        "network-events",
        "audit",
        "--bids-dir",
        str(config.paths.bids_dir),
        "--behavioral-dir",
        str(destination),
    ]
    assert result.details == {
        "behavior_source": str(source),
        "behavior_commit": "a" * 40,
    }


def test_copy_refuses_to_replace_existing_behavioral_content(tmp_path):
    source = canonical_source(tmp_path)
    destination = tmp_path / "bids" / "sourcedata" / "behavioral"
    destination.mkdir(parents=True)
    (destination / "kept.txt").write_text("keep\n")

    with pytest.raises(StageError, match="already exists"):
        copy_content(source, destination)

    assert (destination / "kept.txt").read_text() == "keep\n"
