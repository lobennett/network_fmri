"""Provenance plumbing: provision git-annex, create datasets, record runs.

``text2git`` keeps JSON sidecars in git and NIfTIs in the annex. git-annex is
installed by ``datalad-installer`` on first use — pip cannot ship the binary, and
Sherlock's module (8.x) is below the >= 10.20230126 datalad requires.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

INSTALL_METHOD = "datalad/git-annex:release"


def git_annex_dir() -> Path:
    """Where the provisioned git-annex lives."""
    env = os.environ.get("NETWORK_FMRI_GIT_ANNEX")
    if env:
        return Path(env)
    return Path(os.environ.get("SCRATCH", Path.home())) / "git-annex"


def ensure_git_annex(root: Path) -> Path:
    """Bin directory holding a datalad-compatible git-annex, installing if absent.

    Installs to a per-process directory and renames into place, so concurrent array
    tasks cannot interleave writes into a shared tree.
    """
    bindir = root / "usr" / "bin"
    if (bindir / "git-annex").is_file():
        return bindir

    import certifi

    staging = root.with_name(f"{root.name}.{os.getpid()}")
    print(f"installing git-annex into {staging}", flush=True)
    # uv's CPython ships no CA path, so datalad-installer's urllib calls fail TLS.
    env = dict(os.environ, SSL_CERT_FILE=certifi.where())
    rc = subprocess.run(
        [
            str(Path(sys.executable).parent / "datalad-installer"),
            "git-annex", "-m", INSTALL_METHOD, "--install-dir", str(staging),
        ],
        env=env,
    ).returncode
    if rc != 0 or not (staging / "usr" / "bin" / "git-annex").is_file():
        raise SystemExit(f"could not install git-annex (rc={rc})")
    try:
        staging.rename(root)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)  # another task won the race
    if not (bindir / "git-annex").is_file():
        raise SystemExit(f"git-annex missing at {bindir} after install")
    return bindir


def code_version() -> str:
    """Short commit of this package's repo, for run records."""
    repo = Path(__file__).resolve().parents[2]
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


def subject_commit(dataset_path: Path) -> str:
    """Short commit of a per-subject dataset, for the merge record."""
    out = subprocess.run(["git", "-C", str(dataset_path), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


def datalad_env() -> dict:
    """Environment with a provisioned git-annex on PATH."""
    bindir = ensure_git_annex(git_annex_dir())
    return dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")


def datalad(args: list[str], env: dict, cwd: Path | None = None) -> None:
    exe = str(Path(sys.executable).parent / "datalad")
    rc = subprocess.run([exe, *args], env=env, cwd=cwd).returncode
    if rc != 0:
        raise SystemExit(f"datalad {args[0]} failed (rc={rc})")


def ensure_dataset(path: Path, env: dict) -> None:
    """``datalad create`` unless ``path`` is already a dataset."""
    if (path / ".datalad").is_dir():
        return
    path.mkdir(parents=True, exist_ok=True)
    datalad(["create", "--force", "-c", "text2git", str(path)], env)


def run_recorded(dataset: Path, cmd: list[str], message: str, outputs: list[str],
                 env: dict) -> None:
    """``datalad run`` the command inside ``dataset``, recording it in the history."""
    args = ["run", "-d", str(dataset), "-m", message]
    for out in outputs:
        args += ["--output", out]
    datalad([*args, "--", *cmd], env, cwd=dataset)


if __name__ == "__main__":
    raise SystemExit("network_fmri.provenance provides plumbing; use the network_fmri CLI")
