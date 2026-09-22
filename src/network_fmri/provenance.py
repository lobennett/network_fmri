"""Local code and git-annex provenance helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

INSTALL_METHOD = "datalad/git-annex:release"


def git_annex_dir() -> Path:
    """Return the directory containing the provisioned git-annex installation."""

    configured = os.environ.get("NETWORK_FMRI_GIT_ANNEX")
    if configured:
        return Path(configured)
    return Path(os.environ.get("SCRATCH", Path.home())) / "git-annex"


def activate_git_annex(root: Path | None = None) -> Path:
    """Put the provisioned git-annex and its compatible Git on ``PATH``."""

    directory = ensure_git_annex(root or git_annex_dir())
    os.environ["PATH"] = os.pathsep.join((str(directory), os.environ.get("PATH", "")))
    return directory


def ensure_git_annex(root: Path) -> Path:
    """Provision git-annex and return its directory with a compatible Git."""

    bindir = root / "usr" / "bin"
    bundled = root / "usr" / "lib" / "git-annex.linux"
    if (bindir / "git-annex").is_file() and (bundled / "git").is_file():
        return bundled

    import certifi

    staging = root.with_name(f"{root.name}.{os.getpid()}")
    environment = dict(os.environ, SSL_CERT_FILE=certifi.where())
    result = subprocess.run(
        [
            str(Path(sys.executable).parent / "datalad-installer"),
            "git-annex",
            "-m",
            INSTALL_METHOD,
            "--install-dir",
            str(staging),
        ],
        env=environment,
        check=False,
    )
    if result.returncode != 0 or not (staging / "usr" / "bin" / "git-annex").is_file():
        raise SystemExit(f"could not install git-annex (rc={result.returncode})")
    try:
        staging.rename(root)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
    if not (bindir / "git-annex").is_file() or not (bundled / "git").is_file():
        raise SystemExit(f"complete git-annex bundle missing at {root} after install")
    return bundled


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
    "activate_git_annex",
    "code_is_dirty",
    "code_revision",
    "code_version",
    "ensure_git_annex",
    "git_annex_dir",
]
