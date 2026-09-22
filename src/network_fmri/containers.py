"""Small, explicit builders for isolated Apptainer BIDS App invocations."""

from __future__ import annotations

import os
import json
import re
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from network_fmri.config import ContainerConfig
from network_fmri.models import Runner

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_UNSAFE_BIND = re.compile(r"[:,\x00\r\n]")


def bind(host: Path, destination: str, *, read_only: bool = False) -> str:
    """Return one Apptainer bind specification without invoking a shell."""

    host = Path(host)
    if not host.is_absolute() or not destination.startswith("/"):
        raise ValueError("Apptainer bind paths must be absolute")
    if _UNSAFE_BIND.search(str(host)) or _UNSAFE_BIND.search(destination):
        raise ValueError("Apptainer bind paths cannot contain colon, comma, NUL, or newline")
    suffix = ":ro" if read_only else ""
    return f"{host}:{destination}{suffix}"


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


def prepare_bids_app_paths(output_root: Path, work_root: Path) -> None:
    """Create writable bind sources before Apptainer resolves them on a compute node."""

    Path(output_root).mkdir(parents=True, exist_ok=True)
    Path(work_root).mkdir(parents=True, exist_ok=True)


def receipt_path(output_root: Path, application: str, subject: str) -> Path:
    """Return the private run receipt location inside an application derivative."""

    return Path(output_root) / "code" / "network_fmri" / "run-receipts" / application / f"sub-{subject}.json"


def group_receipt_path(output_root: Path, application: str) -> Path:
    """Return the receipt path for one dependent group application invocation."""

    return Path(output_root) / "code" / "network_fmri" / "run-receipts" / application / "group.json"


def subject_receipt(
    *,
    subject: str,
    input_datalad_commit: str,
    container: ContainerConfig,
    invocation: Iterable[str],
) -> dict[str, object]:
    """Build the exact evidence a consolidator needs to trust a worker output."""

    if not _COMMIT.fullmatch(input_datalad_commit):
        raise ValueError("input DataLad commit must be a 40-character lowercase SHA")
    return {
        "schema_version": 1,
        "status": "success",
        "subject": subject,
        "input_datalad_commit": input_datalad_commit,
        "container": {"image": str(container.image), "version": container.version},
        "invocation": list(invocation),
    }


def write_subject_receipt(path: Path, receipt: dict[str, object]) -> None:
    """Atomically publish one worker receipt after its application exits successfully."""

    destination = Path(path)
    encoded = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=f".{destination.name}.", delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def current_datalad_commit(bids_dir: Path, runner: Runner) -> str:
    """Read the current committed raw-BIDS state without recording a command."""

    try:
        result = runner(
            ["git", "-C", str(bids_dir), "rev-parse", "--verify", "HEAD"],
            check=True, capture_output=True, text=True,
        )
    except Exception as error:
        raise ValueError(f"cannot read current DataLad commit for {bids_dir}") from error
    commit = str(getattr(result, "stdout", "")).strip()
    if not _COMMIT.fullmatch(commit):
        raise ValueError(f"DataLad did not return a full commit SHA for {bids_dir}")
    return commit


def verify_subject_receipt(
    path: Path,
    *,
    subject: str,
    input_datalad_commit: str,
    container: ContainerConfig,
    invocation: Iterable[str],
) -> None:
    """Reject output that does not prove it belongs to this exact application run."""

    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"missing or malformed run receipt: {path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"run receipt is not an object: {path}")
    expected = subject_receipt(
        subject=subject,
        input_datalad_commit=input_datalad_commit,
        container=container,
        invocation=invocation,
    )
    for key in ("schema_version", "status", "subject", "input_datalad_commit", "container"):
        if value.get(key) != expected[key]:
            raise ValueError(f"stale or mismatched run receipt ({key}): {path}")
    recorded = value.get("invocation")
    if not isinstance(recorded, list) or not all(isinstance(item, str) for item in recorded):
        raise ValueError(f"run receipt has no valid invocation: {path}")
    if _stable_invocation(recorded) != _stable_invocation(expected["invocation"]):
        raise ValueError(f"stale or mismatched run receipt (invocation): {path}")


def _stable_invocation(invocation: Iterable[object]) -> tuple[str, ...]:
    """Ignore the node-local /tmp bind, which legitimately changes per Slurm job."""

    values = list(invocation)
    result: list[str] = []
    index = 0
    while index < len(values):
        value = values[index]
        if value == "--bind" and index + 1 < len(values) and str(values[index + 1]).endswith(":/tmp"):
            index += 2
            continue
        result.append(str(value))
        index += 1
    return tuple(result)
