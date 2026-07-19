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


def _identifier(sub_dir: Path, ses_dir: Path) -> str:
    """``<sub-label>_<ses>`` — subject dir sans ``sub-`` prefix + session dir.

    e.g. ``sub-s1035`` / ``ses-01`` -> ``s1035_ses-01``.
    """
    sub_label = sub_dir.name[len("sub-"):] if sub_dir.name.startswith("sub-") else sub_dir.name
    return f"{sub_label}_{ses_dir.name}"


def link_b0_fields(cohort_dir: Path) -> LinkSummary:
    """Stamp session-scoped B0Field* metadata across a staged BIDS cohort tree.

    For each ``sub-*/ses-*`` with both a field map and ≥1 BOLD: stamp
    ``B0FieldIdentifier`` on the ``_fieldmap`` + ``_magnitude`` sidecars and
    ``B0FieldSource`` (same value) on every ``_bold`` sidecar. Sessions with BOLD
    but no field map are counted (``no_fmap``) and skipped; a field map with no
    BOLD is counted (``orphan_fmap``) and skipped. Raises ``ValueError`` if a
    session has more than one field map (asserted-never).
    """
    cohort_dir = Path(cohort_dir)
    summary = LinkSummary()

    for sub_dir in sorted(cohort_dir.glob("sub-*")):
        if not sub_dir.is_dir():
            continue
        for ses_dir in sorted(sub_dir.glob("ses-*")):
            if not ses_dir.is_dir():
                continue
            fmaps = sorted((ses_dir / "fmap").glob("*_fieldmap.nii.gz"))
            bold_niftis = sorted((ses_dir / "func").glob("*_bold.nii.gz"))

            if len(fmaps) > 1:
                raise ValueError(
                    f"{ses_dir}: multiple field maps {[f.name for f in fmaps]} "
                    "— expected exactly one per session"
                )
            if not fmaps:
                if bold_niftis:
                    summary.no_fmap += 1
                    log.warning("%s: BOLD present but no field map — no SDC", ses_dir)
                continue
            if not bold_niftis:
                summary.orphan_fmap += 1
                log.info("%s: field map present but no BOLD — skipped", ses_dir)
                continue

            ident = _identifier(sub_dir, ses_dir)
            fieldmap = fmaps[0]
            magnitude = fieldmap.with_name(
                fieldmap.name.replace("_fieldmap.nii.gz", "_magnitude.nii.gz")
            )
            for nii in (fieldmap, magnitude):
                sidecar = nii.with_name(nii.name.replace(".nii.gz", ".json"))
                if sidecar.exists():
                    _set_sidecar_key(sidecar, "B0FieldIdentifier", ident)
                else:
                    log.warning("%s: expected sidecar %s is missing — not stamped", ses_dir, sidecar.name)
            for nii in bold_niftis:
                sidecar = nii.with_name(nii.name.replace(".nii.gz", ".json"))
                if not sidecar.exists():
                    log.warning("%s: expected sidecar %s is missing — not stamped", ses_dir, sidecar.name)
                    continue
                if _set_sidecar_key(sidecar, "B0FieldSource", ident):
                    summary.bolds_stamped += 1
            summary.sessions_linked += 1

    return summary
