"""Link session-scoped B0 field maps to their BOLD runs via BIDS metadata.

Each session has exactly one Hz field map (`_fieldmap` + `_magnitude`,
`Units: Hz`). fMRIPrep/SDCFlows groups the field map, its magnitude, and the
BOLD runs it corrects by a shared ``B0FieldIdentifier``. This module stamps a
per-session identifier (``<sub-label>_<ses>``) onto the two fmap sidecars and a
matching ``B0FieldSource`` onto every BOLD echo sidecar in the session.

Sidecar writes preserve each file's native indent and append the key, so a diff
is exactly one added line per file. Writes are atomic (temp + rename) and a pure
function of the input → byte-identical across runs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class LinkSummary:
    """Counts returned by :func:`link_b0_fields` (printed + asserted by tests)."""

    sessions_linked: int = 0
    bolds_stamped: int = 0
    no_fmap: int = 0
    orphan_fmap: int = 0


def _detect_indent(text: str) -> int:
    """Leading-space width of the first indented line; default 2 if none."""
    for line in text.splitlines():
        stripped = line.lstrip(" ")
        if stripped and stripped != line:
            return len(line) - len(stripped)
    return 2


def _set_sidecar_key(sidecar: Path, key: str, value: str) -> bool:
    """Set ``sidecar[key] = value`` (append), preserving native indent.

    No-op returning ``False`` if the key already equals ``value``. Overwrites a
    differing value. Atomic temp-file + rename; trailing newline. Returns
    ``True`` when the file was written.
    """
    text = sidecar.read_text()
    data = json.loads(text)
    if data.get(key) == value:
        return False
    indent = _detect_indent(text)
    data[key] = value
    tmp = sidecar.with_name(sidecar.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=indent, ensure_ascii=False) + "\n")
    tmp.rename(sidecar)
    return True
