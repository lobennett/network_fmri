"""Atomic milestone receipts and explicit DataLad saves for one BIDS dataset."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from network_fmri.models import Runner

_STAGE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class MilestoneReceipt:
    """Evidence that one fully validated pipeline stage was saved."""

    stage: str
    status: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    versions: dict[str, Any]
    jobs: dict[str, Any]
    validation: dict[str, Any]


def receipt_path(bids_dir: Path, stage: str) -> Path:
    """Return the receipt location for a validated stage."""

    _validate_stage(stage)
    return Path(bids_dir) / "code" / "network_fmri" / "milestones" / f"{stage}.json"


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Publish JSON with an atomic replacement in its destination directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary_name = temporary.name
            json.dump(payload, temporary, default=_json_default, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def save_milestone(
    bids_dir: Path,
    receipt: MilestoneReceipt,
    runner: Runner = subprocess.run,
) -> str:
    """Write one successful receipt, save it, and return the resulting Git head."""

    _validate_stage(receipt.stage)
    if receipt.status != "success":
        raise ValueError("milestone receipts must have status 'success'")
    payload = asdict(receipt)
    _reject_configured_token({"bids_dir": str(bids_dir), "receipt": payload})
    write_json_atomic(receipt_path(bids_dir, receipt.stage), payload)
    runner(
        ["datalad", "save", "-d", str(bids_dir), "-m", receipt.stage],
        check=True,
    )
    return git_head(bids_dir, runner)


def save_diagnostic(
    bids_dir: Path,
    stage: str,
    paths: list[Path],
    runner: Runner = subprocess.run,
) -> str:
    """Save failure diagnostics without publishing a successful-stage receipt."""

    _validate_stage(stage)
    if not paths:
        raise ValueError("diagnostic save requires at least one path")
    path_strings = [str(path) for path in paths]
    _reject_configured_token(
        {"bids_dir": str(bids_dir), "stage": stage, "paths": path_strings}
    )
    runner(
        [
            "datalad",
            "save",
            "-d",
            str(bids_dir),
            "-m",
            f"{stage}-failed-diagnostics",
            *path_strings,
        ],
        check=True,
    )
    return git_head(bids_dir, runner)


def git_head(bids_dir: Path, runner: Runner = subprocess.run) -> str:
    """Ask Git to verify and return the current commit of ``bids_dir``."""

    result = runner(
        ["git", "-C", str(bids_dir), "rev-parse", "--verify", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    head = result.stdout.strip()
    if not head:
        raise ValueError("git did not return a verified HEAD")
    return head


def _validate_stage(stage: str) -> None:
    if not _STAGE.fullmatch(stage):
        raise ValueError("stage must use lowercase letters, digits, and hyphens")


def _reject_configured_token(payload: dict[str, Any]) -> None:
    token = os.environ.get("FLYWHEEL_API_TOKEN")
    if token and token in json.dumps(payload, default=_json_default):
        raise ValueError("receipt must not contain a configured credential")


def _json_default(value: object) -> str:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot serialize receipt value of type {type(value).__name__}")
