"""Install finalized behavioral repositories in the canonical BIDS dataset."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from network_fmri.config import BehaviorSource, WorkflowConfig
from network_fmri.models import StageResult
from network_fmri.stages import StageError


Runner = Callable[..., Any]


def ingest_behavior(
    config: WorkflowConfig,
    runner: Runner = subprocess.run,
) -> StageResult:
    """Install both pinned DataLad sources and audit the in-scanner identities."""

    sources = (
        ("in_scanner", config.behavior.in_scanner),
        ("out_of_scanner", config.behavior.out_of_scanner),
    )
    for _, source in sources:
        require_clean_canonical_source(source.source, source.commit, runner)

    root = config.paths.bids_dir / "sourcedata" / "behavioral"
    destinations = tuple(root / name for name, _ in sources)
    for (_, source), destination in zip(sources, destinations, strict=True):
        _install_subdataset(config.paths.bids_dir, source, destination, runner)

    in_scanner = destinations[0]
    _run(["datalad", "get", "-d", str(in_scanner), str(in_scanner)], runner)
    _run(
        [
            "network-events", "audit",
            "--bids-dir", str(config.paths.bids_dir),
            "--behavioral-dir", str(in_scanner),
            *_pilot_subject_args(config.subjects),
        ],
        runner,
    )
    return StageResult(
        "behavioral-sourcedata-ingested",
        destinations,
        {
            "in_scanner_source": str(config.behavior.in_scanner.source),
            "in_scanner_commit": config.behavior.in_scanner.commit,
            "out_of_scanner_source": str(config.behavior.out_of_scanner.source),
            "out_of_scanner_commit": config.behavior.out_of_scanner.commit,
        },
    )


def _pilot_subject_args(subjects: tuple[str, ...]) -> list[str]:
    if len(subjects) != 1:
        return []
    return ["--subject", f"sub-{subjects[0]}"]


def require_clean_canonical_source(
    source: Path, expected: str, runner: Runner = subprocess.run, *, label: str = "behavior",
) -> None:
    """Require an unchanged repository at the configured immutable commit."""

    try:
        actual = _output(
            runner(["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError(f"could not verify canonical {label} commit at {source}") from error
    if actual != expected:
        raise StageError(
            f"canonical {label} commit mismatch at {source}: expected {expected}, found {actual or 'none'}"
        )
    try:
        status = _output(
            runner(
                ["git", "-C", str(source), "status", "--porcelain", "--ignored"],
                check=True, capture_output=True, text=True,
            )
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError(f"could not inspect canonical {label} source at {source}") from error
    if status:
        raise StageError(
            f"canonical {label} source must be clean; it has tracked, untracked, "
            f"or ignored changes: {status}"
        )


def _install_subdataset(
    bids_dir: Path, source: BehaviorSource, destination: Path, runner: Runner,
) -> None:
    if destination.exists() or destination.is_symlink():
        if not (destination / ".git").exists():
            raise StageError(f"conflicting behavioral destination: {destination}")
        actual = _repository_head(destination, runner)
        relative = destination.relative_to(bids_dir).as_posix()
        registered = _output(
            runner(
                ["git", "-C", str(bids_dir), "ls-files", "--stage", "--", relative],
                check=True, capture_output=True, text=True,
            )
        )
        if actual != source.commit or (registered and not registered.startswith(f"160000 {source.commit} ")):
            raise StageError(
                f"behavioral subdataset does not match configured commit: {destination}"
            )
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(
        ["datalad", "clone", str(source.source), str(destination)],
        runner,
    )
    if _repository_head(destination, runner) != source.commit:
        raise StageError(f"installed behavioral subdataset has the wrong commit: {destination}")


def _repository_head(repository: Path, runner: Runner) -> str:
    try:
        completed = runner(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError(f"could not verify behavioral subdataset: {repository}") from error
    return _output(completed).strip()


def _run(command: list[str], runner: Runner) -> None:
    try:
        runner(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError(f"source stage command failed: {command[0]}") from error


def _output(completed: Any) -> str:
    value = getattr(completed, "stdout", "")
    if isinstance(value, bytes):
        return value.decode()
    return str(value)
