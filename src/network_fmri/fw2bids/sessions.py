"""Session numbering and subject aliasing. See README.md."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

SESSION_MAP_ENV = "FWBIDS_SESSION_MAP"
FORCE_SUBJECT_ENV = "FWBIDS_FORCE_SUBJECT"

# Duplicate subject records on Flywheel -> canonical subject.
SUBJECT_ALIASES = {"s19-2": "s19", "s29-2": "s29", "s43-2": "s43", "ex26207": "s297"}

# `exclude` drops a session; `reassign_to` moves it to another participant.
# Both compensate for records we lack Flywheel admin rights to fix.
SESSION_OVERRIDES: dict[str, dict[str, dict]] = {
    # Labeled under s03 but acquired from s10.
    "s03": {"22752": {"reassign_to": "s10"}},
    # Fmap-only test session: single-echo, no usable functional or behavioral data.
    "s29": {"22424": {"exclude": True}},
}


# Flywheel split a single scanner visit into two sessions, leaving the fieldmap
# stranded in a container of its own ~1.5 min before the first BOLD run. Moving the
# acquisition at source is refused ("403 Can't create ad hoc when lab edition is
# off"), so the stray session is curated under its twin's number instead: the
# fieldmap lands with the runs it belongs to and the empty container stops consuming
# a session number. {canonical: {stray_accession: twin_accession}}
SESSION_MERGES = {
    "s1258": {"unknown_2": "28338"},
    "s1391": {"unknown": "28270"},
    "s1445": {"unknown_5": "28037"},
}


def relevant_labels(canonical: str) -> set[str]:
    """Subject labels worth querying — avoids ``.sessions()`` on all ~60 subjects."""
    return (
        {canonical}
        | {variant for variant, canon in SUBJECT_ALIASES.items() if canon == canonical}
        | {
            source
            for source, overrides in SESSION_OVERRIDES.items()
            for ovr in overrides.values()
            if ovr.get("reassign_to") == canonical
        }
    )


def collect(all_subjects: list[Any], canonical: str) -> list[dict[str, Any]]:
    """``[{fw_subject, label, timestamp, force_subject}]`` for one canonical subject.

    ``force_subject`` is set only for a reassigned session.
    """
    wanted = relevant_labels(canonical)
    records = []
    for subj in all_subjects:
        if subj.label not in wanted:
            continue
        for sess in subj.sessions():
            ovr = SESSION_OVERRIDES.get(subj.label, {}).get(sess.label, {})
            if ovr.get("exclude"):
                continue
            reassign = ovr.get("reassign_to")
            if (reassign or SUBJECT_ALIASES.get(subj.label, subj.label)) != canonical:
                continue
            records.append({
                "fw_subject": subj.label,
                "label": sess.label,
                "timestamp": sess.timestamp,
                "force_subject": canonical if reassign else None,
            })
    return records


def normalize(label: str) -> str:
    """Strip ``ses-``/``sub-`` as the engine does, so map keys match hook args."""
    return re.sub("sub-", "", re.sub("ses-", "", label))


def timeline(records: list[dict[str, Any]], merges: dict[str, str] | None = None) -> dict[str, str]:
    """``{normalized label: "NN"}`` in timestamp order, 1-indexed and zero-padded.

    Bare, not ``ses-NN``: the engine adds the prefix. Duplicate labels raise rather
    than silently collapsing two sessions into one.

    ``merges`` maps a stray accession to its twin; the stray inherits the twin's
    number instead of consuming one of its own.
    """
    merges = {normalize(k): normalize(v) for k, v in (merges or {}).items()}
    numbered: dict[str, str] = {}
    idx = 0
    for rec in sorted(records, key=lambda r: r["timestamp"]):
        key = normalize(rec["label"])
        if key in merges:
            continue                       # inherits its twin's number below
        if key in numbered:
            raise ValueError(
                f"duplicate session label {rec['label']!r} (normalizes to {key!r}); "
                "relabel one of them on Flywheel"
            )
        idx += 1
        numbered[key] = f"{idx:02d}"
    for stray, twin in merges.items():
        if twin in numbered:
            numbered[stray] = numbered[twin]
    return numbered


def jobs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One invocation per ``(fw_subject, force_subject)``; the forced subject is
    process-wide env, so it cannot apply to only some sessions."""
    grouped: dict[tuple[str, str | None], list[str]] = {}
    for rec in records:
        grouped.setdefault((rec["fw_subject"], rec["force_subject"]), []).append(rec["label"])
    return [
        {"fw_subject": subject, "force_subject": force, "sessions": sorted(labels)}
        for (subject, force), labels in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))
    ]


def load_env_map() -> dict[str, str]:
    """Map at ``$FWBIDS_SESSION_MAP``; empty if unset, so the hook passes through."""
    path = os.environ.get(SESSION_MAP_ENV)
    return json.loads(Path(path).read_text()) if path else {}
