"""Provenance metadata and explicit milestone-save compatibility exports."""

from __future__ import annotations

import subprocess
from functools import cache
from pathlib import Path

from network_fmri.milestones import MilestoneReceipt, save_diagnostic, save_milestone


def code_version() -> str:
    """Return the short revision of this package's repository."""

    return _git_revision("--short")


@cache
def code_revision() -> str:
    """Return the full revision of this package's repository."""

    return _git_revision()


@cache
def code_is_dirty() -> bool:
    """Report whether the package worktree has uncommitted changes."""

    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip())


def _git_revision(*args: str) -> str:
    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", *args, "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or "unknown"


__all__ = [
    "MilestoneReceipt",
    "code_is_dirty",
    "code_revision",
    "code_version",
    "save_diagnostic",
    "save_milestone",
]
