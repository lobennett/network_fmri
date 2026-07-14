"""Tests for network_fmri.curation — the atomic Flywheel->BIDS ground truth."""

from network_fmri import curation as c


def test_roster_from_config():
    assert c.roster("discovery") == ["s03", "s10", "s19", "s29", "s43"]
    assert len(c.roster("validation")) == 41


def test_map_acquisition_func_base_and_dual_and_variants():
    # clean base label
    assert c.map_acquisition("task-goNogo_bold") == {"modality": "func", "task": "goNogo"}
    # shapeMatching scanner typo variant
    assert c.map_acquisition("task-shapeMaching_bold")["task"] == "shapeMatching"
    # snake_case dual labels
    assert c.map_acquisition("stop_signal_w_flanker_bold")["task"] == "stopSignalWFlanker"
    assert (
        c.map_acquisition("directed_forgetting_w_flanker_bold")["task"]
        == "directedForgettingWFlanker"
    )
    # rerun suffix
    assert c.map_acquisition("task-goNogo_bold_1")["task"] == "goNogo"


def test_map_acquisition_anat_dwi_fmap():
    t1 = c.map_acquisition("NEW Sag_MPRAGE_T1")
    assert t1["modality"] == "anat" and t1["suffix"] == "T1w" and t1["acq"] == "SagMPRAGE"
    t2 = c.map_acquisition("T2w CUBE PROMO .8mm sag")
    assert t2["modality"] == "anat" and t2["suffix"] == "T2w"
    dwi = c.map_acquisition("DTI_pe0_g105")
    assert dwi["modality"] == "dwi" and dwi["dir"] == "AP" and dwi["acq"] == "g105"
    assert c.map_acquisition("fmap-fieldmap")["modality"] == "fmap"


def test_map_acquisition_skips_localizers_and_unknown():
    assert c.map_acquisition("3Plane Loc SSFSE") is None  # localizer skip
    assert c.map_acquisition("GE HOS FOV28") is None
    assert c.map_acquisition("some_totally_unknown_series") is None


def test_resolve_subject_alias_and_cross_subject_move():
    assert c.resolve_subject("s19-2", "99999") == "s19"  # alias
    assert c.resolve_subject("s03", "50000") == "s03"  # passthrough
    # session 22752 physically under s03 but belongs to s10
    assert c.resolve_subject("s03", "22752") == "s10"


def test_reassignments_and_excluded_from_config():
    # session_overrides is the single source of truth for cross-subject moves
    assert c.reassignments() == {"22752": "s10"}
    assert ("s29", "22424") in c.excluded_sessions()


def test_alias_targets_are_real_subjects():
    allsub = set(c.roster("discovery")) | set(c.roster("validation")) | set(c.roster("excluded"))
    for tgt in c.SUBJECT_ALIASES.values():
        assert tgt in allsub, f"alias target {tgt} not in any roster"
