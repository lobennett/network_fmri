"""Convert isolated Flywheel subjects and atomically assemble their BIDS dataset."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Sequence

from network_fmri.config import WorkflowConfig
from network_fmri.models import Runner, StageResult
from network_fmri.stages import StageError


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ANATOMICAL_SUFFIXES = ("_T1w.nii", "_T1w.nii.gz", "_T2w.nii", "_T2w.nii.gz")


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
        "--pydeface-image",
        str(config.pydeface.image),
        "--pydeface-version",
        config.pydeface.version,
        "--pydeface-sha256",
        config.pydeface.sha256,
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
    defacing = _verified_defacing_details(destination, config)
    _initialize_datalad_dataset(destination, runner)
    return StageResult(
        "bids-assembled",
        (destination,),
        {
            "subjects": list(config.subjects),
            "subject_count": len(config.subjects),
            "defacing": defacing,
        },
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


def _verified_defacing_details(destination: Path, config: WorkflowConfig) -> dict[str, object]:
    """Read the final, upstream-verified receipt inventory without publishing its contents."""

    directory = destination / "code" / "network_fw2bids" / "defacing"
    if directory.is_symlink() or not directory.is_dir():
        raise StageError(f"assembled defacing receipt directory is missing or unsafe: {directory}")
    expected = {f"sub-{subject}.json" for subject in config.subjects}
    try:
        actual = {entry.name for entry in directory.iterdir()}
    except OSError as error:
        raise StageError(f"could not inspect assembled defacing receipts: {directory}") from error
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise StageError("assembled defacing receipts do not cover the configured roster: " + "; ".join(details))

    t1w = 0
    t2w = 0
    receipts = []
    for subject in config.subjects:
        path = directory / f"sub-{subject}.json"
        image_counts = _verified_receipt_image_counts(path, subject, config)
        t1w += image_counts[0]
        t2w += image_counts[1]
        receipts.append(path.relative_to(destination).as_posix())
    return {"subjects": len(config.subjects), "T1w": t1w, "T2w": t2w, "receipts": receipts}


def _verified_receipt_image_counts(
    path: Path, subject: str, config: WorkflowConfig,
) -> tuple[int, int]:
    """Validate the public shape and pin evidence of one copied schema-v1 receipt."""

    try:
        if path.is_symlink() or not path.is_file():
            raise StageError(f"assembled defacing receipt is missing or unsafe: {path}")
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise StageError(f"could not read assembled defacing receipt: {path}") from error
    if not isinstance(value, dict) or set(value) != {"schema_version", "subject", "status", "software", "images"}:
        raise StageError(f"assembled defacing receipt has an invalid schema: {path}")
    if not isinstance(value["schema_version"], int) or isinstance(value["schema_version"], bool):
        raise StageError(f"assembled defacing receipt has an invalid schema: {path}")
    if value["schema_version"] != 1 or value["subject"] != subject or value["status"] != "success":
        raise StageError(f"assembled defacing receipt does not identify a successful subject: {path}")
    expected_software = {
        "name": "PyDeface",
        "version": config.pydeface.version,
        "container": config.pydeface.image.name,
        "sha256": config.pydeface.sha256,
    }
    if value["software"] != expected_software:
        raise StageError(f"assembled defacing receipt does not match the configured PyDeface pin: {path}")
    images = value["images"]
    if not isinstance(images, list):
        raise StageError(f"assembled defacing receipt has invalid image evidence: {path}")
    t1w = 0
    t2w = 0
    seen = set()
    for image in images:
        suffix = _validated_receipt_image(image, subject, path)
        image_path = image["path"]
        if image_path in seen:
            raise StageError(f"assembled defacing receipt has duplicate image evidence: {path}")
        seen.add(image_path)
        t1w += suffix == "T1w"
        t2w += suffix == "T2w"
    return t1w, t2w


def _validated_receipt_image(image: object, subject: str, receipt_path: Path) -> str:
    if not isinstance(image, dict) or set(image) != {
        "path", "input_sha256", "output_sha256", "shape", "zooms", "affine_sha256",
    }:
        raise StageError(f"assembled defacing receipt has invalid image evidence: {receipt_path}")
    image_path = image["path"]
    checksums = (image["input_sha256"], image["output_sha256"], image["affine_sha256"])
    shape, zooms = image["shape"], image["zooms"]
    path = PurePosixPath(image_path) if isinstance(image_path, str) else None
    if (
        path is None
        or not image_path
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != image_path
        or not image_path.startswith(f"sub-{subject}/")
        or not image_path.endswith(_ANATOMICAL_SUFFIXES)
        or not all(isinstance(checksum, str) and _SHA256.fullmatch(checksum) for checksum in checksums)
        or not isinstance(shape, list)
        or not isinstance(zooms, list)
        or not shape
        or len(shape) != len(zooms)
        or any(not isinstance(size, int) or isinstance(size, bool) or size <= 0 for size in shape)
        or any(
            not isinstance(zoom, (int, float))
            or isinstance(zoom, bool)
            or not math.isfinite(zoom)
            or zoom <= 0
            for zoom in zooms
        )
    ):
        raise StageError(f"assembled defacing receipt has invalid image evidence: {receipt_path}")
    return "T1w" if "_T1w." in image_path else "T2w"


def _run_checked(command: list[str], runner: Runner) -> None:
    try:
        runner(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError(f"source stage command failed: {command[0]}") from error


def _initialize_datalad_dataset(destination: Path, runner: Runner) -> None:
    """Turn the newly published plain BIDS tree into the dataset serial stages save."""

    try:
        runner(
            ["datalad", "create", "-c", "text2git", "--force", str(destination)],
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError(f"could not initialize DataLad dataset at {destination}") from error


def _reject_token_in_command(command: list[str]) -> None:
    token = os.environ.get("FLYWHEEL_API_TOKEN")
    rendered = " ".join(command)
    if "FLYWHEEL_API_TOKEN" in rendered or (token and token in rendered):
        raise StageError("Flywheel credentials must remain in the environment")
