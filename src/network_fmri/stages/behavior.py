"""Ingest the reviewed canonical behavioral source without runtime remapping."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from network_fmri.config import WorkflowConfig
from network_fmri.models import Runner, StageResult
from network_fmri.stages import StageError


def ingest_behavior(
    config: WorkflowConfig,
    runner: Runner = subprocess.run,
) -> StageResult:
    """Copy the exact canonical commit and audit it against the assembled BIDS tree."""

    source = config.behavior.source
    require_clean_canonical_source(source, config.behavior.commit, runner)
    destination = config.paths.bids_dir / "sourcedata" / "behavioral"
    with staged_committed_content(source, destination, config.behavior.commit, runner) as staged:
        # Audit the bytes that will be published.  A failed audit leaves no
        # behavioral destination behind, so the same stage can be retried.
        _run_checked(
            [
                "network-events",
                "audit",
                "--bids-dir",
                str(config.paths.bids_dir),
                "--behavioral-dir",
                str(staged),
            ],
            runner,
        )
        _publish_no_replace(staged, destination)
    return StageResult(
        "behavioral-sourcedata-ingested",
        (destination,),
        {
            "behavior_source": str(source),
            "behavior_commit": config.behavior.commit,
        },
    )


def require_git_head(source: Path, expected: str, runner: Runner = subprocess.run) -> str:
    """Return the checked-out full Git commit, refusing any other source revision."""

    try:
        result = runner(
            ["git", "-C", str(source), "rev-parse", "--verify", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError(f"could not verify canonical behavior commit at {source}") from error
    actual = str(getattr(result, "stdout", "")).strip()
    if actual != expected:
        raise StageError(
            f"canonical behavior commit mismatch: expected {expected}, found {actual or 'none'}"
        )
    return actual


def require_clean_canonical_source(
    source: Path, expected: str, runner: Runner = subprocess.run
) -> None:
    """Ensure the worktree can only expose bytes from the pinned canonical commit."""

    require_git_head(source, expected, runner)
    try:
        result = runner(
            [
                "git",
                "-C",
                str(source),
                "status",
                "--porcelain=v1",
                "--ignored=matching",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError(f"could not inspect canonical behavioral source at {source}") from error
    status = str(getattr(result, "stdout", "")).strip()
    if status:
        raise StageError(
            "canonical behavioral source has tracked, untracked, or ignored changes: " + status
        )


@contextmanager
def staged_committed_content(
    source: Path,
    destination: Path,
    commit: str,
    runner: Runner = subprocess.run,
) -> Iterator[Path]:
    """Materialize only committed source paths into a disposable staged directory.

    The canonical source is a Git/DataLad dataset.  Its repository metadata is
    provenance for that source, rather than raw BIDS sourcedata, so it is not
    copied.  The committed path list prevents ignored or untracked files from
    entering BIDS sourcedata.  ``copy2`` follows annex symlinks and therefore
    copies their actual content rather than a dangling link.
    """

    source = Path(source)
    destination = Path(destination)
    if not source.is_dir() or source.is_symlink():
        raise StageError(f"canonical behavioral source is missing or unsafe: {source}")
    if destination.exists() or destination.is_symlink():
        raise StageError(f"behavioral destination already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_root: Path | None = None
    try:
        temporary_root = Path(
            tempfile.mkdtemp(prefix=".behavioral-ingest-", dir=destination.parent)
        )
        staged = temporary_root / destination.name
        _materialize_committed_content(source, staged, commit, runner)
        yield staged
    except (OSError, shutil.Error) as error:
        raise StageError(f"could not copy canonical behavioral source to {destination}") from error
    finally:
        if temporary_root is not None:
            shutil.rmtree(temporary_root, ignore_errors=True)


def copy_content(
    source: Path,
    destination: Path,
    commit: str,
    runner: Runner = subprocess.run,
) -> None:
    """Publish only a clean, pinned canonical source for an explicit caller.

    :func:`ingest_behavior` stages and audits the same committed content before
    calling the publication primitive.  This helper is intentionally just as
    strict, so a future caller cannot accidentally copy working-tree-only data.
    """

    require_clean_canonical_source(source, commit, runner)
    with staged_committed_content(source, destination, commit, runner) as staged:
        _publish_no_replace(staged, destination)


def _materialize_committed_content(source: Path, staged: Path, commit: str, runner: Runner) -> None:
    paths = _committed_paths(source, commit, runner)
    staged.mkdir()
    for relative in paths:
        _copy_committed_file(source, staged, relative)


def _committed_paths(source: Path, commit: str, runner: Runner) -> tuple[Path, ...]:
    try:
        result = runner(
            ["git", "-C", str(source), "ls-tree", "-r", "-z", "--name-only", commit, "--"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError(f"could not list canonical behavioral content at {commit}") from error
    output = getattr(result, "stdout", b"")
    if isinstance(output, str):
        names = output.encode().split(b"\0")
    else:
        names = bytes(output).split(b"\0")
    paths = tuple(Path(os.fsdecode(name)) for name in names if name)
    if not paths:
        raise StageError(f"canonical behavioral commit has no files: {commit}")
    if len(set(paths)) != len(paths) or any(_unsafe_relative_path(path) for path in paths):
        raise StageError("canonical behavioral commit contains an unsafe file path")
    return paths


def _copy_committed_file(source: Path, staged: Path, relative: Path) -> None:
    source_path = source / relative
    destination_path = staged / relative
    if not source_path.is_file() or source_path.is_symlink() and not source_path.exists():
        raise StageError(f"canonical behavioral content is unavailable: {source_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source_path, destination_path, follow_symlinks=True)
    except OSError as error:
        raise StageError(f"could not materialize canonical behavioral content: {source_path}") from error


def _unsafe_relative_path(path: Path) -> bool:
    return path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts)


def _publish_no_replace(staged: Path, destination: Path) -> None:
    """Use the conversion package's native exclusive directory publication primitive."""

    try:
        from network_fw2bids._publication import publish_directory
    except ImportError as error:
        raise StageError("network_fw2bids is required for atomic behavioral publication") from error
    try:
        publish_directory(staged, destination)
    except Exception as error:
        raise StageError(f"could not publish behavioral sourcedata at {destination}") from error


def _run_checked(command: list[str], runner: Runner) -> None:
    try:
        runner(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError(f"source stage command failed: {command[0]}") from error
