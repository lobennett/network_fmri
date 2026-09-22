"""Read and atomically update a BIDS JSON sidecar.

Shared by trim, b0link and sidecars so the temp-file-plus-rename exists once. Writes are
a pure function of the input, so re-running any of those stages is byte-identical.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class SidecarError(ValueError):
    """A BIDS sidecar required by a preparation stage is not usable."""


def path_for(nifti: Path) -> Path:
    """The sidecar beside a NIfTI: ``..._bold.nii.gz`` -> ``..._bold.json``."""
    return nifti.with_name(nifti.name.replace(".nii.gz", ".json"))


def read(path: Path) -> dict:
    """Return a JSON-object sidecar, rejecting missing and malformed files.

    Treating a bad sidecar as an empty object can make a destructive in-place
    stage appear successful.  Preparation stages instead fail before writing
    derived metadata, leaving the defect visible for repair.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SidecarError(f"required sidecar is missing: {path}") from error
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as error:
        raise SidecarError(f"required sidecar is malformed: {path}") from error
    if not isinstance(data, dict):
        raise SidecarError(f"required sidecar is not a JSON object: {path}")
    return data


def write(path: Path, data: dict) -> None:
    """Atomically write one complete sidecar object."""
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise SidecarError(f"could not write sidecar: {path}") from error


def update(path: Path, **fields) -> bool:
    """Set ``fields`` on an existing valid sidecar and return whether it changed."""
    data = read(path)
    if all(data.get(k) == v for k, v in fields.items()):
        return False
    data.update(fields)
    write(path, data)
    return True
