"""Tests for network_fmri.b0link — session-scoped B0Field* linkage."""

import json

from network_fmri.b0link import LinkSummary, _detect_indent, _set_sidecar_key


def test_detect_indent_two_space():
    text = '{\n  "A": 1,\n  "B": 2\n}\n'
    assert _detect_indent(text) == 2


def test_detect_indent_four_space():
    text = '{\n    "A": 1,\n    "B": {\n        "C": 3\n    }\n}\n'
    assert _detect_indent(text) == 4


def test_detect_indent_defaults_to_two_when_flat():
    assert _detect_indent('{}\n') == 2


def test_set_sidecar_key_appends_and_preserves_indent(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{\n  "A": 1,\n  "Z": 2\n}\n')
    wrote = _set_sidecar_key(p, "B0FieldSource", "s1_ses-01")
    assert wrote is True
    # key appended last, 2-space indent preserved, trailing newline
    assert p.read_text() == (
        '{\n  "A": 1,\n  "Z": 2,\n  "B0FieldSource": "s1_ses-01"\n}\n'
    )
    assert json.loads(p.read_text())["B0FieldSource"] == "s1_ses-01"


def test_set_sidecar_key_idempotent_noop(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{\n  "B0FieldSource": "s1_ses-01"\n}\n')
    before = p.read_text()
    wrote = _set_sidecar_key(p, "B0FieldSource", "s1_ses-01")
    assert wrote is False
    assert p.read_text() == before  # untouched


def test_set_sidecar_key_overwrites_different_value(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{\n  "B0FieldSource": "old"\n}\n')
    wrote = _set_sidecar_key(p, "B0FieldSource", "new")
    assert wrote is True
    assert json.loads(p.read_text())["B0FieldSource"] == "new"


def test_link_summary_defaults_zero():
    s = LinkSummary()
    assert (s.sessions_linked, s.bolds_stamped, s.no_fmap, s.orphan_fmap) == (0, 0, 0, 0)


import pytest

from network_fmri.b0link import link_b0_fields


def _sidecar(path, obj=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj or {"RepetitionTime": 1.0}, indent=2) + "\n")


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def _make_session(root, sub, ses, *, fmap=True, tasks=("flanker", "rest"), echoes=(1, 2, 3)):
    """Build a minimal BIDS-ish session: optional fmap + multi-echo BOLD per task."""
    base = root / f"sub-{sub}" / f"ses-{ses}"
    if fmap:
        for suffix in ("fieldmap", "magnitude"):
            stem = f"sub-{sub}_ses-{ses}_run-1_{suffix}"
            _touch(base / "fmap" / f"{stem}.nii.gz")
            _sidecar(base / "fmap" / f"{stem}.json", {"Units": "Hz"})
    for task in tasks:
        for echo in echoes:
            stem = f"sub-{sub}_ses-{ses}_task-{task}_run-1_echo-{echo}_bold"
            _touch(base / "func" / f"{stem}.nii.gz")
            _sidecar(base / "func" / f"{stem}.json")
    return base


def test_happy_path_stamps_fmap_magnitude_and_all_bolds(tmp_path):
    base = _make_session(tmp_path, "s1035", "01", tasks=("flanker", "nBack", "rest"))
    summary = link_b0_fields(tmp_path)

    ident = "s1035_ses-01"
    for suffix in ("fieldmap", "magnitude"):
        j = json.loads((base / "fmap" / f"sub-s1035_ses-01_run-1_{suffix}.json").read_text())
        assert j["B0FieldIdentifier"] == ident
    bolds = sorted((base / "func").glob("*_bold.json"))
    assert len(bolds) == 9  # 3 tasks x 3 echoes
    for b in bolds:
        assert json.loads(b.read_text())["B0FieldSource"] == ident

    assert summary.sessions_linked == 1
    assert summary.bolds_stamped == 9
    assert summary.no_fmap == 0
    assert summary.orphan_fmap == 0


def test_no_fmap_session_leaves_bolds_untouched(tmp_path):
    base = _make_session(tmp_path, "s1258", "06", fmap=False)
    summary = link_b0_fields(tmp_path)
    for b in (base / "func").glob("*_bold.json"):
        assert "B0FieldSource" not in json.loads(b.read_text())
    assert summary.no_fmap == 1
    assert summary.sessions_linked == 0
    assert summary.bolds_stamped == 0


def test_orphan_fmap_not_stamped(tmp_path):
    base = _make_session(tmp_path, "s0", "01", tasks=())  # fmap, no bold
    summary = link_b0_fields(tmp_path)
    j = json.loads((base / "fmap" / "sub-s0_ses-01_run-1_fieldmap.json").read_text())
    assert "B0FieldIdentifier" not in j
    assert summary.orphan_fmap == 1
    assert summary.sessions_linked == 0


def test_idempotent_second_run_is_noop(tmp_path):
    _make_session(tmp_path, "s1035", "01")
    link_b0_fields(tmp_path)
    snapshot = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    summary2 = link_b0_fields(tmp_path)
    for p, content in snapshot.items():
        assert p.read_bytes() == content  # byte-identical, untouched
    assert summary2.bolds_stamped == 0  # nothing newly written


def test_deterministic_identical_trees_produce_identical_sidecars(tmp_path):
    a = tmp_path / "A"
    b = tmp_path / "B"
    _make_session(a, "s1035", "01")
    _make_session(b, "s1035", "01")
    link_b0_fields(a)
    link_b0_fields(b)
    for pa in a.rglob("*.json"):
        pb = b / pa.relative_to(a)
        assert pa.read_bytes() == pb.read_bytes()


def test_multi_fmap_raises(tmp_path):
    base = _make_session(tmp_path, "s1", "01")
    # a second field map in the same session → assert-never → raise
    _touch(base / "fmap" / "sub-s1_ses-01_run-2_fieldmap.nii.gz")
    _sidecar(base / "fmap" / "sub-s1_ses-01_run-2_fieldmap.json", {"Units": "Hz"})
    with pytest.raises(ValueError, match="multiple field maps"):
        link_b0_fields(tmp_path)
