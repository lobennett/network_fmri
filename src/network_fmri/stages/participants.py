"""Publish canonical, deidentified participant metadata into the BIDS root."""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import tempfile
from pathlib import Path

from network_fmri.config import WorkflowConfig
from network_fmri.models import Runner, StageResult
from network_fmri.stages import StageError
from network_fmri.stages.behavior import require_clean_canonical_source


def ingest_participants(
    config: WorkflowConfig, runner: Runner = subprocess.run,
) -> StageResult:
    """Validate one pinned BIDS-ready source and publish its participant files."""

    source_config = config.participants
    source = Path(source_config.source)
    bids_dir = Path(config.paths.bids_dir)
    if source.is_symlink() or not source.is_dir():
        raise StageError(f"canonical participants source is missing or unsafe: {source}")
    require_clean_canonical_source(
        source, source_config.commit, runner, label="participants",
    )
    rows, columns = _read_tsv(source / "participants.tsv")
    metadata = _read_metadata(source / "participants.json", columns)
    full_roster = _read_roster(Path(config.subjects_file))
    by_subject = _validate_roster(rows, full_roster)
    selected = tuple(f"sub-{subject}" for subject in config.subjects)
    if not set(selected) <= set(by_subject):
        raise StageError("configured execution subjects are absent from participant metadata")
    output_rows = [by_subject[subject] for subject in selected]
    tsv_bytes = _encode_tsv(columns, output_rows)
    json_bytes = (json.dumps(metadata, indent=2) + "\n").encode()
    tsv = bids_dir / "participants.tsv"
    sidecar = bids_dir / "participants.json"
    _publish_pair(tsv, tsv_bytes, sidecar, json_bytes)
    return StageResult(
        "participants-ingested",
        (tsv, sidecar),
        {
            "source": str(source),
            "commit": source_config.commit,
            "participants": len(output_rows),
        },
    )


def _read_tsv(path: Path) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    try:
        if path.is_symlink() or not path.is_file():
            raise StageError(f"canonical participant table is missing or unsafe: {path}")
        reader = csv.DictReader(io.StringIO(path.read_text()), delimiter="\t")
        columns = tuple(reader.fieldnames or ())
        rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as error:
        raise StageError(f"could not read canonical participant table: {path}") from error
    if not columns or columns[0] != "participant_id" or len(set(columns)) != len(columns):
        raise StageError("participants.tsv must begin with unique participant_id columns")
    if not rows or any(None in row or any(value is None for value in row.values()) for row in rows):
        raise StageError("participants.tsv contains malformed rows")
    return rows, columns


def _read_metadata(path: Path, columns: tuple[str, ...]) -> dict[str, object]:
    try:
        if path.is_symlink() or not path.is_file():
            raise StageError(f"participant metadata is missing or unsafe: {path}")
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StageError(f"could not read participant metadata: {path}") from error
    if not isinstance(value, dict):
        raise StageError("participants.json must contain a JSON object")
    missing = []
    for column in columns[1:]:
        description = value.get(column)
        if not isinstance(description, dict) or not isinstance(description.get("Description"), str):
            missing.append(column)
        elif not description["Description"].strip():
            missing.append(column)
    if missing:
        raise StageError("participants.json does not describe column(s): " + ", ".join(missing))
    return value


def _read_roster(path: Path) -> tuple[str, ...]:
    try:
        roster = tuple(line.strip() for line in path.read_text().splitlines() if line.strip())
    except OSError as error:
        raise StageError(f"could not read configured participant roster: {path}") from error
    if len(roster) != 46 or len(set(roster)) != 46:
        raise StageError("configured participant roster must contain 46 unique subjects")
    return roster


def _validate_roster(
    rows: list[dict[str, str]], roster: tuple[str, ...],
) -> dict[str, dict[str, str]]:
    by_subject: dict[str, dict[str, str]] = {}
    for row in rows:
        subject = row["participant_id"]
        if not subject.startswith("sub-") or subject in by_subject:
            raise StageError("participants.tsv contains invalid or duplicate participant_id values")
        by_subject[subject] = row
    expected = {f"sub-{subject}" for subject in roster}
    if set(by_subject) != expected:
        missing = sorted(expected - set(by_subject))
        extra = sorted(set(by_subject) - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("extra " + ", ".join(extra))
        raise StageError("participant roster mismatch: " + "; ".join(details))
    return by_subject


def _encode_tsv(columns: tuple[str, ...], rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def _publish_pair(tsv: Path, tsv_bytes: bytes, sidecar: Path, json_bytes: bytes) -> None:
    existing = (tsv.exists() or tsv.is_symlink(), sidecar.exists() or sidecar.is_symlink())
    if any(existing):
        if (
            existing == (True, True)
            and not tsv.is_symlink()
            and not sidecar.is_symlink()
            and tsv.is_file()
            and sidecar.is_file()
            and tsv.read_bytes() == tsv_bytes
            and sidecar.read_bytes() == json_bytes
        ):
            return
        raise StageError("BIDS root contains conflicting participant metadata")
    temporary: list[Path] = []
    published: list[Path] = []
    try:
        for destination, content in ((tsv, tsv_bytes), (sidecar, json_bytes)):
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=f".{destination.name}.", delete=False,
            ) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temporary.append(Path(handle.name))
        for source, destination in zip(temporary, (tsv, sidecar), strict=True):
            os.replace(source, destination)
            published.append(destination)
    except OSError as error:
        for path in published:
            path.unlink(missing_ok=True)
        raise StageError("could not publish participant metadata atomically") from error
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
