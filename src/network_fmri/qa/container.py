"""Resolve and run Apptainer/Singularity containers, caching pulled images.

Lets a step name a container URI instead of expecting the host to be configured.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def cache_dir(override: str | Path | None = None) -> Path:
    """Where pulled ``.sif`` files live."""
    if override:
        return Path(override)
    env = os.environ.get("NETWORK_FMRI_CONTAINERS")
    if env:
        return Path(env)
    return Path(os.environ.get("SCRATCH", Path.home())) / "containers"


def sif_name(uri: str) -> str:
    """``docker://bids/validator:3.0.1`` -> ``bids-validator_3.0.1.sif``."""
    body = re.sub(r"^[a-z0-9]+://", "", uri)
    repo, _, tag = body.partition(":")
    stem = repo.replace("/", "-")
    return f"{stem}_{tag or 'latest'}.sif"


def _runtime() -> str:
    for exe in ("apptainer", "singularity"):
        if shutil.which(exe):
            return exe
    raise SystemExit("neither apptainer nor singularity is on PATH")


def resolve(uri: str, image: str | Path | None = None, cache: str | Path | None = None) -> Path:
    """Local ``.sif`` for ``uri``, pulling if absent. ``image`` short-circuits.

    Pulls to a temp name then renames, so concurrent tasks never read a partial image.
    """
    if image:
        sif = Path(image)
        if not sif.is_file():
            raise SystemExit(f"container not found: {sif}")
        return sif

    target = cache_dir(cache) / sif_name(uri)
    if target.is_file():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".sif.{os.getpid()}")
    print(f"pulling {uri} -> {target}", flush=True)
    rc = subprocess.run([_runtime(), "pull", "--force", str(tmp), uri]).returncode
    if rc != 0:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"failed to pull {uri} (rc={rc})")
    os.replace(tmp, target)
    return target


def default_binds() -> list[str]:
    """Paths to bind. Apptainer does not mount Lustre, so /scratch needs this."""
    return [p for p in ("/scratch", "/oak", "/home/groups") if Path(p).is_dir()]


def run(
    sif: Path,
    args: list[str],
    entry: str | None = None,
    binds: list[str] | None = None,
) -> int:
    """Run the image, returning its exit code. ``entry`` switches run -> exec."""
    cmd = [_runtime(), "exec" if entry else "run"]
    for path in default_binds() if binds is None else binds:
        cmd += ["-B", path]
    cmd.append(str(sif))
    if entry:
        cmd.append(entry)
    cmd += args
    print(" ".join(cmd), flush=True)
    return subprocess.run(cmd).returncode


if __name__ == "__main__":  # `python -m network_fmri.container <uri> [args...]`
    sys.exit(run(resolve(sys.argv[1]), sys.argv[2:]))
