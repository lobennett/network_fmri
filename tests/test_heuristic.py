"""Offline logic tests for fwbids.heuristic (fw-heudiconv key assignment).

These verify the deterministic mapping logic with fake seqinfo objects. The
multi-echo {item} behavior and the 22752 cross-subject move need live
`curate --dry-run` validation (see heuristic.py caveats) and are not asserted here.
"""

from network_fmri import heuristic as h


class FakeSeq:
    def __init__(self, acquisition_label, series_id="x", patient_id="s03"):
        self.acquisition_label = acquisition_label
        self.series_id = series_id
        self.patient_id = patient_id


def test_replace_subject_alias():
    assert h.ReplaceSubject("s19-2") == "s19"
    assert h.ReplaceSubject("s03") == "s03"


def test_replace_session_uses_env_map(tmp_path, monkeypatch):
    import json

    m = tmp_path / "map.json"
    m.write_text(json.dumps({"25210": "13", "22461": "01"}))
    monkeypatch.setenv("FWBIDS_SESSION_MAP", str(m))
    assert h.ReplaceSession("25210") == "13"  # rescue T1w sorts last -> ses-13
    assert h.ReplaceSession("22461") == "01"
    assert h.ReplaceSession("99999") == "99999"  # not in map -> passthrough


def test_replace_session_passthrough_without_env(monkeypatch):
    monkeypatch.delenv("FWBIDS_SESSION_MAP", raising=False)
    assert h.ReplaceSession("22461") == "22461"


def test_templates_for_each_modality():
    func = h._templates_for({"modality": "func", "task": "goNogo"})
    assert func == ["sub-{subject}/{session}/func/sub-{subject}_{session}"
                    "_task-goNogo_run-1_echo-{echo}_bold"]
    anat = h._templates_for({"modality": "anat", "suffix": "T1w", "acq": "SagMPRAGE"})
    assert anat[0].endswith("_acq-SagMPRAGE_run-1_T1w")
    dwi = h._templates_for({"modality": "dwi", "dir": "AP", "acq": "g105"})
    assert dwi[0].endswith("_acq-g105_dir-AP_run-1_dwi")
    fmap = h._templates_for({"modality": "fmap"})
    assert [t.split("/")[-1].split("_")[-1] for t in fmap] == ["fieldmap", "magnitude"]


def test_infotodict_maps_and_skips():
    seqs = [
        FakeSeq("task-goNogo_bold", series_id="a"),
        FakeSeq("stop_signal_w_flanker_bold", series_id="b"),
        FakeSeq("NEW Sag_MPRAGE_T1", series_id="c"),
        FakeSeq("3Plane Loc SSFSE", series_id="loc"),  # localizer -> skip
        FakeSeq("some_unknown_series", series_id="u"),  # unmapped -> skip
    ]
    info = h.infotodict(seqs)
    assigned = {sid for ids in info.values() for sid in ids}
    assert assigned == {"a", "b", "c"}  # loc + u skipped
    # the goNogo series lands under a func/task-goNogo template
    templates = [k[0] for k in info]
    assert any("task-goNogo" in t for t in templates)
    assert any("task-stopSignalWFlanker" in t for t in templates)
    assert any("_acq-SagMPRAGE_run-1_T1w" in t for t in templates)
