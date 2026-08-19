"""Read and atomically update a BIDS JSON sidecar.

Shared by trim, b0link and sidecars so the temp-file-plus-rename exists once. Writes are
a pure function of the input, so re-running any of those stages is byte-identical.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


def path_for(nifti: Path) -> Path:
    """The sidecar beside a NIfTI: ``..._bold.nii.gz`` -> ``..._bold.json``."""
    return nifti.with_name(nifti.name.replace(".nii.gz", ".json"))


def read(path: Path) -> dict:
    """Parse a sidecar. A missing or truncated file reads as empty rather than raising."""
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("malformed sidecar, treating as empty: %s (%s)", path.name, e)
        return {}


def update(path: Path, **fields) -> bool:
    """Set ``fields`` on a sidecar. False if the file is absent or already correct."""
    if not path.is_file():
        log.warning("expected sidecar is missing: %s", path)
        return False
    data = read(path)
    if all(data.get(k) == v for k, v in fields.items()):
        return False
    data.update(fields)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
    return True
