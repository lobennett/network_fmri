"""Link each session's field-map metadata to its functional acquisitions."""

from __future__ import annotations

import json
import os
from pathlib import Path

from network_fmri.models import StageResult
from network_fmri.prepare.sidecar import SidecarError, path_for, read, write
from network_fmri.stages import StageError


def link_b0(bids_dir: Path) -> StageResult:
    """Set B0 links atomically and idempotently across a BIDS dataset."""
    bids_dir = Path(bids_dir)
    try:
        _require_b0_input(bids_dir)
        summary = link_tree(bids_dir)
    except (OSError, SidecarError, ValueError, json.JSONDecodeError) as error:
        raise StageError(f"B0 linking failed: {error}") from error
    return StageResult("b0-fieldmaps-linked", (bids_dir,), summary)


def _require_b0_input(bids_dir: Path) -> None:
    """Reject missing, non-BIDS, and empty inputs before metadata mutation."""
    description = bids_dir / "dataset_description.json"
    if not bids_dir.is_dir() or bids_dir.is_symlink():
        raise ValueError(f"BIDS directory is missing or unsafe: {bids_dir}")
    try:
        metadata = json.loads(description.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"BIDS dataset description is missing or malformed: {description}") from error
    if not isinstance(metadata, dict):
        raise ValueError(f"BIDS dataset description is not an object: {description}")
    if not any(path.is_dir() for path in bids_dir.glob("sub-*/ses-*")):
        raise ValueError(f"BIDS directory has no subject sessions: {bids_dir}")
    if not any(bids_dir.glob("sub-*/ses-*/func/*_bold.nii.gz")):
        raise ValueError(f"BIDS directory has no BOLD scans for B0 linkage: {bids_dir}")


def link_tree(bids_dir: Path) -> dict[str, int]:
    """Stamp B0 metadata after validating every affected sidecar.

    The complete update is planned before any file changes.  If a later atomic
    write fails, originals written earlier in this pass are restored.  A second
    successful invocation therefore has no changes to make.
    """
    plans: dict[Path, dict[str, object]] = {}
    summary = {"sessions": 0, "bold": 0, "fmap": 0, "no_fmap": 0, "orphan_fmap": 0}

    for session in sorted(bids_dir.glob("sub-*/ses-*")):
        if not session.is_dir():
            continue
        fieldmaps = sorted(session.glob("fmap/*_fieldmap.nii.gz"))
        bolds = sorted(session.glob("func/*_bold.nii.gz"))

        if len(fieldmaps) > 1:
            raise ValueError(f"{session}: {len(fieldmaps)} field maps, expected exactly one")
        if not fieldmaps:
            if bolds:
                summary["no_fmap"] += 1
            continue
        if not bolds:
            summary["orphan_fmap"] += 1
            continue

        identifier = f"{session.parent.name.removeprefix('sub-')}_{session.name}"
        fieldmap = fieldmaps[0]
        # Magnitude images are linked when exported; fieldmap-only exports are
        # valid and still receive the required identifier.
        for nifti in (fieldmap, *sorted(session.glob("fmap/*_magnitude*.nii.gz"))):
            _plan(plans, path_for(nifti), B0FieldIdentifier=identifier)
        for nii in bolds:
            _plan(plans, path_for(nii), B0FieldSource=identifier)
        summary["sessions"] += 1

    changed = _publish_plans(plans)
    for fields in changed.values():
        if "B0FieldSource" in fields:
            summary["bold"] += 1
        else:
            summary["fmap"] += 1
    return summary


def _plan(plans: dict[Path, dict[str, object]], path: Path, **fields: object) -> None:
    """Validate a sidecar and add fields to its complete planned contents."""
    data = plans.get(path)
    if data is None:
        data = read(path).copy()
        plans[path] = data
    data.update(fields)


def _publish_plans(plans: dict[Path, dict[str, object]]) -> dict[Path, dict[str, object]]:
    """Publish planned sidecars, restoring originals when one write fails."""
    originals = {path: path.read_bytes() for path in plans}
    original_data = {path: read(path) for path in plans}
    changed = {
        path: {key: value for key, value in data.items() if original_data[path].get(key) != value}
        for path, data in plans.items()
    }
    changed = {path: fields for path, fields in changed.items() if fields}
    written: list[Path] = []
    try:
        for path in sorted(changed):
            write(path, plans[path])
            written.append(path)
    except Exception:
        for path in reversed(written):
            _restore(path, originals[path])
        raise
    return changed


def _restore(path: Path, content: bytes) -> None:
    """Restore one original sidecar through a same-directory atomic rename."""
    temporary = path.with_name(path.name + ".restore.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
