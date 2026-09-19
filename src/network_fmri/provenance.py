"""Provenance metadata and explicit milestone-save compatibility exports."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

from network_fmri.milestones import MilestoneReceipt, save_diagnostic, save_milestone

INSTALL_METHOD = "datalad/git-annex:release"


def git_annex_dir() -> Path:
    """Return the directory containing the provisioned git-annex installation."""

    configured = os.environ.get("NETWORK_FMRI_GIT_ANNEX")
    if configured:
        return Path(configured)
    return Path(os.environ.get("SCRATCH", Path.home())) / "git-annex"


def ensure_git_annex(root: Path) -> Path:
    """Provision a DataLad-compatible git-annex without interleaved installations."""

    bindir = root / "usr" / "bin"
    if (bindir / "git-annex").is_file():
        return bindir

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
    if not (bindir / "git-annex").is_file():
        raise SystemExit(f"git-annex missing at {bindir} after install")
    return bindir


def datalad_env() -> dict[str, str]:
    """Return an environment whose PATH contains the provisioned git-annex."""

    bindir = ensure_git_annex(git_annex_dir())
    return dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")


def datalad(args: list[str], env: dict[str, str], cwd: Path | None = None) -> None:
    """Execute one direct DataLad command for a legacy caller."""

    executable = str(Path(sys.executable).parent / "datalad")
    result = subprocess.run([executable, *args], env=env, cwd=cwd, check=False)
    if result.returncode != 0:
        raise SystemExit(f"datalad {args[0]} failed (rc={result.returncode})")


def ensure_dataset(path: Path, env: dict[str, str]) -> None:
    """Create ``path`` as a DataLad dataset if the legacy caller has not done so."""

    if (path / ".datalad").is_dir():
        return
    path.mkdir(parents=True, exist_ok=True)
    datalad(["create", "--force", "-c", "text2git", str(path)], env)


def run_recorded(
    dataset: Path, cmd: list[str], message: str, outputs: list[str], env: dict[str, str]
) -> None:
    """Run a legacy stage directly, then save its declared products explicitly."""

    result = subprocess.run(cmd, env=env, cwd=dataset, check=False)
    if result.returncode != 0:
        raise SystemExit(f"stage command failed (rc={result.returncode})")
    save_args = ["save", "-d", str(dataset), "-m", message]
    if outputs:
        save_args.extend(["--", *outputs])
    datalad(save_args, env, cwd=dataset)


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


def subject_commit(dataset_path: Path) -> str:
    """Return the short commit of a legacy per-subject dataset."""

    result = subprocess.run(
        ["git", "-C", str(dataset_path), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or "unknown"


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
    "datalad",
    "datalad_env",
    "ensure_dataset",
    "ensure_git_annex",
    "git_annex_dir",
    "run_recorded",
    "save_diagnostic",
    "save_milestone",
    "subject_commit",
]
