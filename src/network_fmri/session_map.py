"""Build the Flywheel-session → BIDS-session-number map for curation.

fw-heudiconv's ``ReplaceSession`` only sees a raw Flywheel session label (an
accession number like ``22461``); it cannot renumber sessions chronologically on
its own. The canonical Oak tree numbers each subject's sessions ``ses-01,
ses-02, …`` in acquisition-timestamp order, honoring the
``curation_config["flywheel"]["session_overrides"]`` table (a session may be
excluded, or reassigned to another subject — e.g. s03/22752 → s10).

This module reproduces that timeline as a flat ``{accession: "NN"}`` map (bare,
zero-padded — ``ReplaceSession`` re-adds the ``ses-`` prefix). ``run.py`` writes
it to JSON and points ``FWBIDS_SESSION_MAP`` at it so the heuristic can renumber
during ``curate``.

Accession labels are globally unique, so per-subject maps merge into one flat
dict with no collisions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "curation_config.json"


def _flywheel_config() -> dict[str, Any]:
    return json.loads(_CONFIG.read_text())["flywheel"]


def _matching_labels(canonical: str, aliases: dict[str, str]) -> set[str]:
    """FW subject labels that resolve to this canonical subject (incl. aliases)."""
    labels = {canonical}
    for variant, canon in aliases.items():
        if canon == canonical:
            labels.add(variant)
    return labels


def collect_sessions(
    canonical: str,
    all_subjects: list[Any],
    aliases: dict[str, str],
    overrides: dict[str, dict],
) -> list[dict[str, Any]]:
    """Gather ``{label, timestamp}`` session dicts for a canonical subject.

    Faithful to the legacy ``bidsify.flywheel_query.collect_subject_sessions``:
    drops ``exclude`` sessions, drops ``reassign_to`` sessions from their source
    subject, and pulls in sessions reassigned *to* this canonical subject.
    """
    matching = _matching_labels(canonical, aliases)
    out: list[dict[str, Any]] = []

    for subj in all_subjects:
        if subj.label not in matching:
            continue
        subj_ovr = overrides.get(subj.label, {})
        for sess in subj.sessions():
            ovr = subj_ovr.get(sess.label, {})
            if ovr.get("exclude") or ovr.get("reassign_to"):
                continue
            out.append({"label": sess.label, "timestamp": sess.timestamp})

    # Sessions reassigned TO this canonical subject (from any source subject).
    subjects_by_label = {s.label: s for s in all_subjects}
    for src_label, src_ovr in overrides.items():
        for ses_label, ovr in src_ovr.items():
            if ovr.get("reassign_to") != canonical:
                continue
            src = subjects_by_label.get(src_label)
            if src is None:
                continue
            for sess in src.sessions():
                if sess.label == ses_label:
                    out.append({"label": sess.label, "timestamp": sess.timestamp})
                    break
    return out


def timeline(sessions: list[dict[str, Any]]) -> dict[str, str]:
    """Sort sessions by timestamp → ``{accession: "NN"}`` (1-indexed, zero-pad)."""
    ordered = sorted(sessions, key=lambda s: s["timestamp"])
    return {s["label"]: f"{idx:02d}" for idx, s in enumerate(ordered, start=1)}


def build_flat_map(
    all_subjects: list[Any],
    canonical_subjects: list[str],
    aliases: dict[str, str] | None = None,
    overrides: dict[str, dict] | None = None,
) -> dict[str, str]:
    """Merge per-subject timelines into one flat ``{accession: "NN"}`` map."""
    cfg = _flywheel_config()
    aliases = cfg["subject_aliases"] if aliases is None else aliases
    overrides = cfg.get("session_overrides", {}) if overrides is None else overrides
    flat: dict[str, str] = {}
    for canonical in canonical_subjects:
        sessions = collect_sessions(canonical, all_subjects, aliases, overrides)
        flat.update(timeline(sessions))
    return flat


def load_env_map() -> dict[str, str]:
    """Load the JSON map pointed to by ``FWBIDS_SESSION_MAP`` (empty if unset)."""
    path = os.environ.get("FWBIDS_SESSION_MAP")
    if not path:
        return {}
    return json.loads(Path(path).read_text())


def plan_jobs(
    all_subjects: list[Any],
    canonical: str,
    aliases: dict[str, str] | None = None,
    overrides: dict[str, dict] | None = None,
) -> list[dict[str, Any]]:
    """Plan the ``curate`` invocations that build one canonical BIDS subject.

    Each job is ``{"fw_subject", "sessions", "force_subject"}``:
      * the subject's own + aliased Flywheel subjects, each with their sessions
        minus any excluded/reassigned-away (``force_subject=None`` → ReplaceSubject
        applies the alias map);
      * one job per session reassigned *in* from another Flywheel subject, curated
        under that source subject with ``force_subject=canonical`` (so run.py sets
        ``FWBIDS_FORCE_SUBJECT`` — ReplaceSubject can't see the session otherwise).
    """
    cfg = _flywheel_config()
    aliases = cfg["subject_aliases"] if aliases is None else aliases
    overrides = cfg.get("session_overrides", {}) if overrides is None else overrides

    matching = _matching_labels(canonical, aliases)
    jobs: list[dict[str, Any]] = []

    for subj in all_subjects:
        if subj.label not in matching:
            continue
        subj_ovr = overrides.get(subj.label, {})
        keep = [
            s.label
            for s in subj.sessions()
            if not (subj_ovr.get(s.label, {}).get("exclude")
                    or subj_ovr.get(s.label, {}).get("reassign_to"))
        ]
        if keep:
            jobs.append({"fw_subject": subj.label, "sessions": sorted(keep),
                         "force_subject": None})

    for src_label, src_ovr in overrides.items():
        for ses_label, ovr in src_ovr.items():
            if ovr.get("reassign_to") == canonical:
                jobs.append({"fw_subject": src_label, "sessions": [ses_label],
                             "force_subject": canonical})
    return jobs
