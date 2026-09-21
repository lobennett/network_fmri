from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from network_fmri.config import (
    BehaviorSource,
    ContainerConfig,
    SlurmConfig,
    VerifiedContainerConfig,
    WorkflowConfig,
    WorkflowPaths,
)
from network_fmri.stages import StageError
from network_fmri.stages import behavior
from network_fmri.stages.behavior import copy_content, ingest_behavior


class GitRunner:
    def __init__(self, head: str, paths: tuple[str, ...] = ()) -> None:
        self.head = head
        self.paths = paths
        self.calls: list[list[str]] = []
        self.fail_audit = False
        self.status = ""

    def __call__(self, args, **kwargs):
        command = [str(value) for value in args]
        self.calls.append(command)
        if command[:2] == ["git", "-C"]:
            if "status" in command:
                return SimpleNamespace(stdout=self.status)
            if "ls-tree" in command:
                return SimpleNamespace(stdout=("\0".join(self.paths) + "\0").encode())
            return SimpleNamespace(stdout=self.head + "\n")
        if command[:2] == ["network-events", "audit"] and self.fail_audit:
            raise __import__("subprocess").CalledProcessError(2, command)
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
        pydeface=VerifiedContainerConfig(tmp_path / "pydeface.sif", "2.1.0", "a" * 64),
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


def tracked_paths(source: Path) -> tuple[str, ...]:
    return tuple(
        str(path.relative_to(source))
        for path in sorted(source.rglob("*"))
        if path.is_file() and ".git" not in path.parts
    )


def publish_with_rename(staged: Path, destination: Path) -> None:
    staged.rename(destination)


def test_ingest_refuses_wrong_canonical_commit(tmp_path):
    source = canonical_source(tmp_path)
    runner = GitRunner("f" * 40, tracked_paths(source))

    with pytest.raises(StageError, match="canonical behavior commit"):
        ingest_behavior(configuration(tmp_path, source), runner)

    assert not (tmp_path / "bids" / "sourcedata" / "behavioral").exists()


def test_ingest_dereferences_committed_content_audits_staged_tree_and_records_commit(tmp_path, monkeypatch):
    source = canonical_source(tmp_path)
    original = source / "sub-s1" / "ses-01" / "beh" / "sub-s1_ses-01_task-test_run-1_beh.csv"
    external = tmp_path / "annex-content.csv"
    external.write_text(original.read_text())
    original.unlink()
    original.symlink_to(external)
    config = configuration(tmp_path, source)
    runner = GitRunner("a" * 40, tracked_paths(source))
    monkeypatch.setattr(behavior, "_publish_no_replace", publish_with_rename)

    result = ingest_behavior(config, runner)

    destination = config.paths.bids_dir / "sourcedata" / "behavioral"
    copied = destination / original.relative_to(source)
    assert copied.read_text() == "onset\n1\n"
    assert not copied.is_symlink()
    assert not (destination / ".git").exists()
    audit_command = runner.calls[-1]
    assert audit_command[:4] == [
        "network-events",
        "audit",
        "--bids-dir",
        str(config.paths.bids_dir),
    ]
    staged_behavior = Path(audit_command[-1])
    assert audit_command[4:6] == ["--behavioral-dir", str(staged_behavior)]
    assert staged_behavior.name == "behavioral"
    assert staged_behavior.parent.name.startswith(".behavioral-ingest-")
    assert not staged_behavior.exists()
    assert audit_command != [
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
        copy_content(source, destination, "a" * 40, GitRunner("a" * 40, tracked_paths(source)))

    assert (destination / "kept.txt").read_text() == "keep\n"


@pytest.mark.parametrize("status", [" M sub-s1/file.csv", "?? extra.csv", "!! stale.tmp"])
def test_ingest_rejects_dirty_untracked_and_ignored_canonical_state(tmp_path, monkeypatch, status):
    source = canonical_source(tmp_path)
    runner = GitRunner("a" * 40, tracked_paths(source))
    runner.status = status + "\n"
    monkeypatch.setattr(behavior, "_publish_no_replace", publish_with_rename)
    with pytest.raises(StageError, match="tracked, untracked, or ignored"):
        ingest_behavior(configuration(tmp_path, source), runner)

    assert not (tmp_path / "bids" / "sourcedata" / "behavioral").exists()


def test_ingest_materializes_only_the_committed_file_set(tmp_path, monkeypatch):
    source = canonical_source(tmp_path)
    committed = tracked_paths(source)
    (source / "ignored-local.csv").write_text("must not copy\n")
    runner = GitRunner("a" * 40, committed)
    monkeypatch.setattr(behavior, "_publish_no_replace", publish_with_rename)

    ingest_behavior(configuration(tmp_path, source), runner)

    destination = tmp_path / "bids" / "sourcedata" / "behavioral"
    assert not (destination / "ignored-local.csv").exists()


def test_ingest_reads_the_actual_pinned_git_tree_before_dereferencing_annex_content(tmp_path, monkeypatch):
    source = tmp_path / "canonical-behavior"
    file = source / "sub-s1" / "ses-01" / "beh" / "sub-s1_ses-01_task-test_run-1_beh.csv"
    file.parent.mkdir(parents=True)
    annex_content = tmp_path / "annex-content.csv"
    annex_content.write_text("onset\n1\n")
    file.symlink_to(annex_content)
    (source / "behavioral_exceptions.tsv").write_text("subject\tsession\ttask\trun\treason\n")
    for command in (
        ["git", "-C", str(source), "init"],
        ["git", "-C", str(source), "config", "user.email", "test@example.com"],
        ["git", "-C", str(source), "config", "user.name", "Test User"],
        ["git", "-C", str(source), "add", "."],
        ["git", "-C", str(source), "commit", "-m", "canonical behavior"],
    ):
        subprocess.run(command, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    config = configuration(tmp_path, source)
    object.__setattr__(config, "behavior", BehaviorSource(source, head))
    monkeypatch.setattr(behavior, "_publish_no_replace", publish_with_rename)

    def runner(args, **kwargs):
        if args[:2] == ["network-events", "audit"]:
            return SimpleNamespace(stdout="")
        return subprocess.run(args, **kwargs)

    ingest_behavior(config, runner)

    copied = config.paths.bids_dir / "sourcedata" / "behavioral" / file.relative_to(source)
    assert copied.read_text() == "onset\n1\n"
    assert not copied.is_symlink()


def test_failed_staged_audit_publishes_nothing_and_allows_retry(tmp_path, monkeypatch):
    source = canonical_source(tmp_path)
    runner = GitRunner("a" * 40, tracked_paths(source))
    runner.fail_audit = True
    monkeypatch.setattr(behavior, "_publish_no_replace", publish_with_rename)
    config = configuration(tmp_path, source)

    with pytest.raises(StageError, match="source stage command failed"):
        ingest_behavior(config, runner)

    destination = config.paths.bids_dir / "sourcedata" / "behavioral"
    assert not destination.exists()
    assert not list(destination.parent.glob(".behavioral-ingest-*"))

    runner.fail_audit = False
    ingest_behavior(config, runner)
    assert destination.is_dir()
