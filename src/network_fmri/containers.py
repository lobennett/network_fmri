"""Small, explicit builders for isolated Apptainer BIDS App invocations."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from network_fmri.config import ContainerConfig


def bind(host: Path, destination: str, *, read_only: bool = False) -> str:
    """Return one Apptainer bind specification without invoking a shell."""

    suffix = ":ro" if read_only else ""
    return f"{Path(host)}:{destination}{suffix}"


def job_tmpdir(environ: Mapping[str, str] = os.environ) -> Path:
    """Use Slurm's node-local temporary directory when it is available."""

    configured = environ.get("SLURM_TMPDIR") or environ.get("TMPDIR")
    return Path(configured) if configured else Path("/tmp")


def apptainer_prefix(
    container: ContainerConfig,
    *,
    binds: Iterable[str],
    environment: Iterable[tuple[str, str]] = (),
) -> tuple[str, ...]:
    """Build the shared isolated runtime prefix for a configured image."""

    command: list[str] = ["apptainer", "exec", "--cleanenv", "--containall"]
    for spec in binds:
        command.extend(("--bind", spec))
    for key, value in environment:
        command.extend(("--env", f"{key}={value}"))
    command.append(str(container.image))
    return tuple(command)
