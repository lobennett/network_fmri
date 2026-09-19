"""Ingest the reviewed canonical behavioral source without runtime remapping."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from network_fmri.config import WorkflowConfig
from network_fmri.models import Runner, StageResult
from network_fmri.stages import StageError


def ingest_behavior(
    config: WorkflowConfig,
    runner: Runner = subprocess.run,
) -> StageResult:
    """Copy the exact canonical commit and audit it against the assembled BIDS tree."""

    source = config.behavior.source
    require_git_head(source, config.behavior.commit, runner)
    destination = config.paths.bids_dir / "sourcedata" / "behavioral"
    copy_content(source, destination)
    _run_checked(
        [
            "network-events",
            "audit",
            "--bids-dir",
            str(config.paths.bids_dir),
            "--behavioral-dir",
            str(destination),
        ],
        runner,
    )
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


def copy_content(source: Path, destination: Path) -> None:
    """Atomically copy canonical source content, dereferencing annex symlinks.

    The canonical source is a Git/DataLad dataset.  Its repository metadata is
    provenance for that source, rather than raw BIDS sourcedata, so it is not
    copied.  ``copytree(..., symlinks=False)`` dereferences annex links and
    raises if required content is unavailable.
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
        shutil.copytree(source, staged, symlinks=False, ignore=_ignore_repository_metadata)
        # ``rename`` is atomic because the staged directory shares the destination's
        # parent filesystem.  The explicit nonexistence check above prevents a
        # replacement of data from an earlier completed run.
        os.rename(staged, destination)
    except (OSError, shutil.Error) as error:
        raise StageError(f"could not copy canonical behavioral source to {destination}") from error
    finally:
        if temporary_root is not None:
            shutil.rmtree(temporary_root, ignore_errors=True)


def _ignore_repository_metadata(_: str, names: list[str]) -> set[str]:
    return {name for name in names if name in {".git", ".datalad"}}


def _run_checked(command: list[str], runner: Runner) -> None:
    try:
        runner(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError(f"source stage command failed: {command[0]}") from error
