"""Flywheel acquisition label -> BIDS entities. See docs/SCAN-NOTES.md.

Functional labels are canonical ``task-<bidsTask>_bold`` plus an optional dedup
suffix. ``TASKS`` is an allowlist, so unknown names are skipped, not guessed.
"""

from __future__ import annotations

import re

CANONICAL_FUNC = re.compile(r"^task-(?P<task>[A-Za-z0-9]+)_bold(?:_\d+|_run_\d+)?$")

# QA-failed scans get this appended to their Flywheel label, so they are never curated.
# Keyed at the source because the heuristic cannot see the session (SeqInfo's
# accession_number is None), and a subject-level skip would drop the kept scan too.
QA_REJECT = re.compile(r"_qa-reject$")

TASKS = {
    # single
    "rest", "cuedTS", "spatialTS", "directedForgetting", "flanker", "goNogo",
    "nBack", "shapeMatching", "stopSignal",
    # dual
    "cuedTSWFlanker", "directedForgettingWCuedTS", "directedForgettingWFlanker",
    "flankerWShapeMatching", "nBackWShapeMatching", "nBackWSpatialTS",
    "shapeMatchingWCuedTS", "spatialTSWCuedTS", "spatialTSWShapeMatching",
    "stopSignalWDirectedForgetting", "stopSignalWFlanker",
}

NON_FUNC: dict[str, dict[str, str]] = {
    "NEW Sag_MPRAGE_T1": {"modality": "anat", "suffix": "T1w", "acq": "SagMPRAGE"},
    "T2w CUBE PROMO .8mm sag": {"modality": "anat", "suffix": "T2w", "acq": "CubePromo"},
    "DTI_pe0_g105": {"modality": "dwi", "dir": "AP", "acq": "g105"},
    "DTI_pe1_g105": {"modality": "dwi", "dir": "PA", "acq": "g105"},
    "DTI_pe1_g71": {"modality": "dwi", "dir": "PA", "acq": "g71"},
    "fmap-fieldmap": {"modality": "fmap"},
}

# n01 is the pilot subject: different naming convention (task-n-back_run-1_ssg).
SKIP_SUBJECTS = {"n01"}

SKIP_ACQUISITIONS = {
    # Localizers and shims; _N variants because a session can hold several.
    "3Plane Loc SSFSE",
    "3Plane Loc SSFSE_1",
    "GE HOS FOV28",
    "GE HOS FOV28_1",
    "GE HOS FOV28_2",
    "GE HOS FOV28_3",
    "GE HOS FOV28_4",
    "HO Shim",
    # Scanner-derived, not source data.
    "Processed Images",
    "Processed Images_1",
    # Single-band reference — not used downstream.
    "run-1_sbref",
    # Second fieldmap in a session (s76 x3, s1486 x1); fmap template hardcodes run-1.
    "fmap-fieldmap_1",
    # Superseded by "NEW Sag_MPRAGE_T1"; its NIfTI is 4D (PROMO motion-nav), so not
    # a valid _T1w (validator: T1W_FILE_WITH_TOO_MANY_DIMENSIONS).
    "T1w MPRAGE PROMO",
}


def map_acquisition(label: str) -> dict[str, str] | None:
    """Acquisition label -> BIDS entities, or ``None`` to leave the series alone."""
    if label in SKIP_ACQUISITIONS or QA_REJECT.search(label):
        return None
    m = CANONICAL_FUNC.match(label)
    if m and m["task"] in TASKS:
        return {"modality": "func", "task": m["task"]}
    return NON_FUNC.get(label)
