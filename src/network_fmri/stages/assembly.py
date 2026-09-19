"""Convert isolated Flywheel subjects and atomically assemble their BIDS dataset."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from network_fmri.config import WorkflowConfig
from network_fmri.models import Runner, StageResult
from network_fmri.stages import StageError


def convert_subject(
    config: WorkflowConfig,
    subject: str,
    runner: Runner = subprocess.run,
) -> StageResult:
    """Download and convert one roster subject into its isolated part directory.

    ``network-fw2bids`` reads ``FLYWHEEL_API_TOKEN`` directly from the process
    environment.  The token is deliberately absent from the command and all
    returned provenance details.
    """

    _require_roster_subject(config, subject)
    output = config.paths.parts_dir / subject
    command = [
        "network-fw2bids",
        "--project",
        config.flywheel_project,
        "--subject",
        subject,
        "--execute",
        "--output",
        str(output),
    ]
    _reject_token_in_command(command)
    _run_checked(command, runner)
    return StageResult(
        "subject-converted",
        (output,),
        {"subject": subject, "flywheel_project": config.flywheel_project},
    )


def assemble_dataset(
    config: WorkflowConfig,
    runner: Runner = subprocess.run,
) -> StageResult:
    """Validate the configured roster and ask ``network_fw2bids`` to publish one BIDS root.

    The upstream assembly module validates each subject export and stages the
    combined tree before its exclusive publication.  This wrapper owns the
    workflow-level roster check, so a partial array result can never be passed
    to the finalizer.  A one-subject roster is allowed only after the CLI has
    derived it from the reviewed 46-subject configuration for a pilot.
    """

    _require_execution_roster(config.subjects)
    _require_complete_part_roster(config.paths.parts_dir, config.subjects)
    destination = config.paths.bids_dir
    if destination.exists() or destination.is_symlink():
        raise StageError(f"BIDS destination already exists: {destination}")
    # The configured roster is parsed at workflow startup.  Give the child an
    # immutable snapshot instead of reopening the operator-editable source file
    # after this wrapper has verified the parts.
    manifest = _write_roster_manifest(config.subjects)
    try:
        command = [
            sys.executable,
            "-m",
            "network_fw2bids._assembly",
            "--subjects",
            str(manifest),
            "--parts",
            str(config.paths.parts_dir),
            "--output",
            str(destination),
        ]
        _run_checked(command, runner)
    finally:
        manifest.unlink(missing_ok=True)
    return StageResult(
        "bids-assembled",
        (destination,),
        {"subjects": list(config.subjects), "subject_count": len(config.subjects)},
    )


def _require_roster_subject(config: WorkflowConfig, subject: str) -> None:
    if subject not in config.subjects:
        raise StageError(f"subject is not in the configured 46-subject roster: {subject}")


def _require_execution_roster(subjects: Sequence[str]) -> None:
    """Accept the full reviewed roster or the explicit one-subject pilot roster."""

    if len(subjects) not in {1, 46} or len(set(subjects)) != len(subjects):
        raise StageError("configured roster must contain exactly 46 unique subjects or one selected pilot subject")


def _write_roster_manifest(subjects: Sequence[str]) -> Path:
    """Write a durable, immutable-in-practice child manifest for one assembly call."""

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="network-fw2bids-subjects-",
        suffix=".txt",
        delete=False,
    ) as handle:
        handle.write("\n".join(subjects) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)


def _require_complete_part_roster(parts_dir: Path, subjects: Sequence[str]) -> None:
    """Require exactly one safe directory for each configured roster subject."""

    if not parts_dir.is_dir() or parts_dir.is_symlink():
        raise StageError(f"parts directory is missing or unsafe: {parts_dir}")
    actual = {entry.name for entry in parts_dir.iterdir()}
    expected = set(subjects)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise StageError("parts do not cover the configured roster: " + "; ".join(details))
    unsafe = sorted(
        entry.name
        for entry in parts_dir.iterdir()
        if not entry.is_dir() or entry.is_symlink()
    )
    if unsafe:
        raise StageError("subject parts must be real directories: " + ", ".join(unsafe))


def _run_checked(command: list[str], runner: Runner) -> None:
    try:
        runner(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError(f"source stage command failed: {command[0]}") from error


def _reject_token_in_command(command: list[str]) -> None:
    token = os.environ.get("FLYWHEEL_API_TOKEN")
    rendered = " ".join(command)
    if "FLYWHEEL_API_TOKEN" in rendered or (token and token in rendered):
        raise StageError("Flywheel credentials must remain in the environment")
