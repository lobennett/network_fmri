"""Atomic curation ground truth for Flywheel->BIDS of the r01network project.

The acquisition->BIDS map below is copied byte-identically from the legacy
bidsify/config.py (the map that produced the canonical Oak BIDS). The
network_fmri heuristic keys on the Flywheel *acquisition label* (surfaced via
our fw-heudiconv fork's SeqInfo.acquisition_label) and resolves it through
map_acquisition() here -- so BIDS naming matches Oak by construction.

Everything study-specific lives here (single source of truth), read from
config/curation_config.json (`flywheel` block + cohort `samples` rosters).
"""
import json
from pathlib import Path

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "curation_config.json"


def _config():
    with open(_CONFIG) as f:
        return json.load(f)


def roster(cohort):
    """Cohort subject list from curation_config.json `samples` (dict or list)."""
    s = _config()["samples"][cohort]
    return sorted(s) if isinstance(s, dict) else list(s)


# ---------------------------------------------------------------------------
# Acquisition-label -> BIDS entity map (VERBATIM from bidsify/config.py).
# ---------------------------------------------------------------------------
ACQUISITION_MAP = {
    # --- Single tasks (rest) ---
    "task-rest_bold": {"modality": "func", "task": "rest"},
    "task-rest_bold_1": {"modality": "func", "task": "rest"},
    "task-rest_bold_run_2": {"modality": "func", "task": "rest"},
    # --- Single tasks (cuedTS) ---
    "task-cuedTS_bold": {"modality": "func", "task": "cuedTS"},
    "task-cuedTaskSwitching_bold": {"modality": "func", "task": "cuedTS"},
    "task-cuedTaskSwitching_bold_1": {"modality": "func", "task": "cuedTS"},
    # --- Single tasks (spatialTS) ---
    "task-spatialTS_bold": {"modality": "func", "task": "spatialTS"},
    "task-spatialTaskSwitching_bold": {"modality": "func", "task": "spatialTS"},
    "task-spatialTaskSwitching_bold_1": {"modality": "func", "task": "spatialTS"},
    # --- Single tasks (directedForgetting) ---
    "task-directedForgetting_bold": {"modality": "func", "task": "directedForgetting"},
    "task-directedForgetting_bold_1": {"modality": "func", "task": "directedForgetting"},
    # --- Single tasks (flanker) ---
    "task-flanker_bold": {"modality": "func", "task": "flanker"},
    # --- Single tasks (goNogo) ---
    "task-goNogo_bold": {"modality": "func", "task": "goNogo"},
    "task-goNogo_bold_1": {"modality": "func", "task": "goNogo"},
    # --- Single tasks (nBack) ---
    "task-nBack_bold": {"modality": "func", "task": "nBack"},
    "task-nBack_bold_1": {"modality": "func", "task": "nBack"},
    # --- Single tasks (shapeMatching) ---
    "task-shapeMatching_bold": {"modality": "func", "task": "shapeMatching"},
    "task-shapeMatching_bold_1": {"modality": "func", "task": "shapeMatching"},
    "task-shapeMaching_bold": {"modality": "func", "task": "shapeMatching"},
    # --- Single tasks (stopSignal) ---
    "task-stopSignal_bold": {"modality": "func", "task": "stopSignal"},
    "task_stopSignal_bold": {"modality": "func", "task": "stopSignal"},
    # --- Dual tasks (stopSignal combinations) ---
    "stop_signal_w_directed_forgetting_bold": {
        "modality": "func",
        "task": "stopSignalWDirectedForgetting",
    },
    "stop_signal_w_directed_forgetting_bold_1": {
        "modality": "func",
        "task": "stopSignalWDirectedForgetting",
    },
    "task-stop_signal_with_directed_forgetting_bold": {
        "modality": "func",
        "task": "stopSignalWDirectedForgetting",
    },
    "task-stop_with_df_bold": {"modality": "func", "task": "stopSignalWDirectedForgetting"},
    "stop_signal_w_flanker_bold": {"modality": "func", "task": "stopSignalWFlanker"},
    "stop_w_flanker_bold": {"modality": "func", "task": "stopSignalWFlanker"},
    "task-stop_with_flanker_bold": {"modality": "func", "task": "stopSignalWFlanker"},
    # --- Dual tasks (directedForgetting combinations) ---
    "directed_forgetting_w_flanker_bold": {
        "modality": "func",
        "task": "directedForgettingWFlanker",
    },
    "directed_forgetting_w_flanker_bold_2": {
        "modality": "func",
        "task": "directedForgettingWFlanker",
    },
    "directed_forgetting_w_cuedTaskSwitching_bold": {
        "modality": "func",
        "task": "directedForgettingWCuedTS",
    },
    "cued_w_directed_forgetting_bold": {"modality": "func", "task": "directedForgettingWCuedTS"},
    # --- Dual tasks (spatialTS + cuedTS combinations) ---
    "spatialTS_w_cuedTS_bold": {"modality": "func", "task": "spatialTSWCuedTS"},
    "spatialTS_w_CuedTS_bold": {"modality": "func", "task": "spatialTSWCuedTS"},
    "spatial_w_cued_bold": {"modality": "func", "task": "spatialTSWCuedTS"},
    "cued_w_spatial_bold": {"modality": "func", "task": "spatialTSWCuedTS"},
    "cuedTS_w_spatialTS_bold": {"modality": "func", "task": "spatialTSWCuedTS"},
    "cuedTS_w_spatial_bold": {"modality": "func", "task": "spatialTSWCuedTS"},
    "spatialTaskSwitching_w_cuedTaskSwitching_bold": {
        "modality": "func",
        "task": "spatialTSWCuedTS",
    },
    "cuedTaskSwitching_w_spatialTaskSwitching_bold": {
        "modality": "func",
        "task": "spatialTSWCuedTS",
    },
    "spatialTaskSwitching_with_cuedTaskSwitching_bold": {
        "modality": "func",
        "task": "spatialTSWCuedTS",
    },
    "cued_task_switching_w_spatial_task_switching_bold": {
        "modality": "func",
        "task": "spatialTSWCuedTS",
    },
    # --- Dual tasks (flanker combinations) ---
    "flanker_w_shapeMatching_bold": {"modality": "func", "task": "flankerWShapeMatching"},
    "flanker_w_cuedTaskSwitching_bold": {"modality": "func", "task": "cuedTSWFlanker"},
    "cuedTS_w_flanker_bold": {"modality": "func", "task": "cuedTSWFlanker"},
    "cuedTS_w_flanker_bold_run_2": {"modality": "func", "task": "cuedTSWFlanker"},
    # --- Dual tasks (nBack combinations) ---
    "nBack_w_shapeMatching_bold": {"modality": "func", "task": "nBackWShapeMatching"},
    "nBack_w_spatial_bold": {"modality": "func", "task": "nBackWSpatialTS"},
    # --- Dual tasks (shapeMatching combinations) ---
    "shapeMatching_w_cued_bold": {"modality": "func", "task": "shapeMatchingWCuedTS"},
    "cuedTaskSwitching_w_shape_matching_bold": {"modality": "func", "task": "shapeMatchingWCuedTS"},
    "cued_taskSwitching_w_shape_matching_bold": {
        "modality": "func",
        "task": "shapeMatchingWCuedTS",
    },
    "shapeMatching_w_spatialTaskSwitching_bold": {
        "modality": "func",
        "task": "spatialTSWShapeMatching",
    },
    "shape_matching_w_spatial_bold": {"modality": "func", "task": "spatialTSWShapeMatching"},
    # --- Fieldmaps ---
    "fmap-fieldmap": {"modality": "fmap"},
    # --- Anatomical ---
    "T1w MPRAGE PROMO": {"modality": "anat", "suffix": "T1w", "acq": "MPRAGEPromo"},
    "NEW Sag_MPRAGE_T1": {"modality": "anat", "suffix": "T1w", "acq": "SagMPRAGE"},
    "T2w CUBE PROMO .8mm sag": {"modality": "anat", "suffix": "T2w", "acq": "CubePromo"},
    # --- Diffusion ---
    "DTI_pe0_g105": {"modality": "dwi", "dir": "AP", "acq": "g105"},
    "DTI_pe1_g105": {"modality": "dwi", "dir": "PA", "acq": "g105"},
    "DTI_pe1_g71": {"modality": "dwi", "dir": "PA", "acq": "g71"},
}

SKIP_ACQUISITIONS = {
    "3Plane Loc SSFSE",
    "GE HOS FOV28",
    "GE HOS FOV28_1",
    "GE HOS FOV28_2",
}


def map_acquisition(label):
    if label in SKIP_ACQUISITIONS:
        return None
    return ACQUISITION_MAP.get(label)




# ---------------------------------------------------------------------------
# Curation special cases -- SINGLE SOURCE OF TRUTH is curation_config.json's
# `flywheel` block (subject_aliases, session_overrides, skip_subjects), the same
# table the legacy bidsify used. Session *renumbering* (ses-01..) is NOT here --
# it is derived dynamically by chronological timestamp order in session_map.py,
# which also makes the s03/25210 "rescue T1w -> ses-13" fall out for free.
# ---------------------------------------------------------------------------
def _flywheel_config():
    return _config()["flywheel"]


SUBJECT_ALIASES = _flywheel_config()["subject_aliases"]
SKIP_SUBJECTS = set(_flywheel_config().get("skip_subjects", []))
SESSION_OVERRIDES = _flywheel_config().get("session_overrides", {})


def reassignments():
    """{flywheel_session_label: target_bids_subject} from session_overrides."""
    return {
        ses: ovr["reassign_to"]
        for subj_ovr in SESSION_OVERRIDES.values()
        for ses, ovr in subj_ovr.items()
        if ovr.get("reassign_to")
    }


def excluded_sessions():
    """{(fw_subject, fw_session)} marked exclude in session_overrides."""
    return {
        (subj, ses)
        for subj, subj_ovr in SESSION_OVERRIDES.items()
        for ses, ovr in subj_ovr.items()
        if ovr.get("exclude")
    }


def resolve_subject(fw_subject, fw_session):
    """Flywheel (subject,session) -> BIDS subject (alias + cross-subject move).

    A ``reassign_to`` session (e.g. s03/22752 -> s10) overrides the subject.
    """
    reassign = reassignments().get(fw_session)
    if reassign:
        return reassign
    return SUBJECT_ALIASES.get(fw_subject, fw_subject)
