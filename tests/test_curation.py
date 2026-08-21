"""The curation gate: which acquisitions reach BIDS, and under what session number.

These are the decisions every later stage inherits. A change here silently changes which
scans exist and what they are called, and nothing downstream would fail.
"""

import pytest

from network_fmri.fw2bids.acquisitions import NON_FUNC, TASKS, map_acquisition
from network_fmri.fw2bids.qa_reject import MARKER, REJECTS, suffix_labels
from network_fmri.fw2bids.sessions import SUBJECT_ALIASES, normalize, timeline


class TestMapAcquisition:
    def test_canonical_func_label(self):
        assert map_acquisition("task-flanker_bold") == {"modality": "func", "task": "flanker"}

    @pytest.mark.parametrize("suffix", ["_1", "_2", "_run_2"])
    def test_dedup_suffixes_map_to_the_same_task(self, suffix):
        assert map_acquisition(f"task-flanker_bold{suffix}") == {
            "modality": "func", "task": "flanker"}

    def test_unknown_task_is_skipped_not_guessed(self):
        # An allowlist, so a typo is dropped rather than curated under a wrong entity.
        assert map_acquisition("task-shapeMaching_bold") is None
        assert map_acquisition("task-notARealTask_bold") is None

    def test_anat_labels(self):
        assert map_acquisition("NEW Sag_MPRAGE_T1")["suffix"] == "T1w"
        assert map_acquisition("T2w CUBE PROMO .8mm sag")["suffix"] == "T2w"

    @pytest.mark.parametrize("label", [
        "3Plane Loc SSFSE", "HO Shim", "T1w MPRAGE PROMO", "fmap-fieldmap_1",
    ])
    def test_skipped_acquisitions(self, label):
        assert map_acquisition(label) is None

    def test_qa_reject_marker_blocks_any_label(self):
        """The anat exclusion gate. Losing this re-imports every rejected scan."""
        for label in ("NEW Sag_MPRAGE_T1", "T2w CUBE PROMO .8mm sag", "task-flanker_bold"):
            assert map_acquisition(label) is not None
            assert map_acquisition(label + MARKER) is None


class TestRejectList:
    def test_targets_are_well_formed(self):
        for target in REJECTS:
            sub, ses, suffix = target.split("/")
            assert sub.startswith("s") and ses.isdigit() and len(ses) == 2
            assert suffix in {"T1w", "T2w"}

    def test_marking_is_idempotent(self):
        """suffix_labels must match already-marked labels, or a replay double-marks."""
        labels = suffix_labels("T1w")
        base = {lab for lab in labels if not lab.endswith(MARKER)}
        assert base, "no unmarked T1w label"
        assert all(lab + MARKER in labels for lab in base)

    def test_every_reject_suffix_is_a_known_anat(self):
        suffixes = {e.get("suffix") for e in NON_FUNC.values()}
        assert {t.split("/")[2] for t in REJECTS} <= suffixes


class TestSessionNumbering:
    def _rec(self, label, ts):
        return {"label": label, "timestamp": ts}

    def test_chronological_and_one_indexed(self):
        recs = [self._rec("22800", 300), self._rec("22700", 100), self._rec("22750", 200)]
        assert timeline(recs) == {"22700": "01", "22750": "02", "22800": "03"}

    def test_numbering_ignores_label_order(self):
        """Numbers follow acquisition time, not label sort -- 'unknown' sorts oddly."""
        recs = [self._rec("unknown", 100), self._rec("28270", 200)]
        assert timeline(recs) == {"unknown": "01", "28270": "02"}

    def test_merge_gives_the_stray_its_twins_number(self):
        recs = [self._rec("unknown_2", 100), self._rec("28338", 200), self._rec("28400", 300)]
        got = timeline(recs, merges={"unknown_2": "28338"})
        assert got["unknown_2"] == got["28338"] == "01"
        assert got["28400"] == "02", "the merged stray must not consume a number"

    def test_duplicate_labels_raise(self):
        with pytest.raises(ValueError, match="duplicate session label"):
            timeline([self._rec("ses-22700", 100), self._rec("22700", 200)])

    def test_normalize_strips_bids_prefixes(self):
        assert normalize("ses-22700") == "22700"
        assert normalize("sub-s03") == "s03"

    def test_aliases_point_at_canonical_subjects(self):
        assert set(SUBJECT_ALIASES.values()) <= {"s19", "s29", "s43", "s297"}
        assert "s19-2" in SUBJECT_ALIASES
