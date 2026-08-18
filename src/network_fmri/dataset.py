"""``network_fmri datalad`` — version a merged BIDS tree as a DataLad dataset.

Idempotent. ``text2git`` keeps JSON sidecars in git and NIfTIs in the annex.

git-annex is provisioned by ``datalad-installer`` on first use: pip cannot ship the
binary, and Sherlock's ``system/git-annex`` module (8.x) is below the >= 10.20230126
that datalad requires.
"""

from __future__ import annotations

import argparse
import os
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
    """Bin directory holding a datalad-compatible git-annex, installing if absent."""
    bindir = root / "usr" / "bin"
    if (bindir / "git-annex").is_file():
        return bindir

    import certifi

    print(f"installing git-annex into {root}", flush=True)
    # uv's CPython ships no CA path, so datalad-installer's urllib calls fail TLS.
    env = dict(os.environ, SSL_CERT_FILE=certifi.where())
    rc = subprocess.run(
        [
            str(Path(sys.executable).parent / "datalad-installer"),
            "git-annex", "-m", INSTALL_METHOD, "--install-dir", str(root),
        ],
        env=env,
    ).returncode
    if rc != 0 or not (bindir / "git-annex").is_file():
        raise SystemExit(f"could not install git-annex into {root} (rc={rc})")
    return bindir


def commands(tree: Path, message: str, jobs: int | None) -> list[list[str]]:
    """datalad invocations needed; create is skipped if already a dataset."""
    cmds = []
    if not (tree / ".datalad").is_dir():
        cmds.append(["create", "--force", "-c", "text2git", str(tree)])
    save = ["save", "-d", str(tree), "-m", message]
    if jobs:
        save += ["-J", str(jobs)]
    cmds.append(save)
    return cmds


def get_parser() -> argparse.ArgumentParser:
    from network_fmri.cli import DEFAULT_STAGING
    from network_fmri.cohorts import COHORTS

    p = argparse.ArgumentParser(prog="network_fmri datalad")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--message", "-m", default=None)
    p.add_argument("--jobs", "-J", type=int, default=None,
                   help="parallel git-annex workers; use several at these sizes")
    p.add_argument("--git-annex-dir", default=None,
                   help=f"default: {git_annex_dir()}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    tree = Path(args.staging) / args.cohort / "bids"
    if not tree.is_dir():
        raise SystemExit(f"no merged tree at {tree} (run `network_fmri merge` first)")

    bindir = ensure_git_annex(Path(args.git_annex_dir or git_annex_dir()))
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    datalad = str(Path(sys.executable).parent / "datalad")
    message = args.message or f"network_fmri: import {args.cohort} BIDS from Flywheel"

    for cmd in commands(tree, message, args.jobs):
        rc = subprocess.run([datalad, *cmd], env=env).returncode
        if rc != 0:
            raise SystemExit(f"datalad {cmd[0]} failed (rc={rc})")
    print(f"[{args.cohort}] {tree} is a DataLad dataset", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
