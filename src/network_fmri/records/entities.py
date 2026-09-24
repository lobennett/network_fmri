"""Parse deterministic BIDS identities without reading data content."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from network_fmri.records.models import Entity

_ENTITIES = {
    "sub": "subject", "ses": "session", "task": "task", "run": "run",
    "acq": "acquisition", "echo": "echo",
}
_DATATYPES = {"anat", "func", "fmap", "dwi", "perf", "eeg", "meg", "beh"}


def entity_from_path(path: Path | str) -> Entity:
    """Return the BIDS identity encoded by a relative raw or derivative path."""

    relative = PurePosixPath(str(path).replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("record paths must be normalized and relative")
    parts = relative.parts
    namespace = parts[1] if len(parts) > 1 and parts[0] == "derivatives" else "raw"
    datatype = next((part for part in reversed(parts[:-1]) if part in _DATATYPES), None)
    name = parts[-1]
    stem = re.sub(r"\.(?:nii\.gz|[A-Za-z0-9]+)$", "", name)
    tokens = stem.split("_")
    values: dict[str, str] = {}
    suffix = None
    for token in tokens:
        if "-" in token:
            label, value = token.split("-", 1)
            field = _ENTITIES.get(label)
            if field and value:
                values[field] = str(int(value)) if field == "run" and value.isdigit() else value
        elif token:
            suffix = token
    return Entity(namespace=namespace, datatype=datatype, suffix=suffix, **values)
