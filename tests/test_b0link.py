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
