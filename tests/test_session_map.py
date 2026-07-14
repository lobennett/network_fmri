"""Offline tests for fwbids.session_map (chronological BIDS session numbering)."""

from network_fmri import session_map as sm


class FakeSession:
    def __init__(self, label, timestamp):
        self.label = label
        self._ts = timestamp

    @property
    def timestamp(self):
        return self._ts


class FakeSubject:
    def __init__(self, label, sessions):
        self.label = label
        self._sessions = sessions

    def sessions(self):
        return self._sessions


def test_timeline_numbers_by_timestamp():
    sessions = [
        {"label": "22568", "timestamp": "2020-05-02"},
        {"label": "22461", "timestamp": "2020-04-01"},
        {"label": "25210", "timestamp": "2022-05-24"},  # rescue T1w, latest
    ]
    got = sm.timeline(sessions)
    # Chronological order regardless of accession value; rescue session (latest
    # timestamp) sorts last. With s03's real 13 sessions, 25210 lands at ses-13.
    assert got == {"22461": "01", "22568": "02", "25210": "03"}


def test_timeline_normalizes_ses_prefixed_labels():
    # A session literally labeled "ses-2" on Flywheel: the map key must be the
    # force_label_format-normalized "2" (ses- stripped), because ReplaceSession is
    # called with the stripped label. Otherwise it misses → unpadded "ses-2". (s415)
    sessions = [
        {"label": "unknown", "timestamp": "2023-02-03"},
        {"label": "ses-2", "timestamp": "2023-02-04"},
        {"label": "26616", "timestamp": "2023-03-03"},
    ]
    got = sm.timeline(sessions)
    assert got == {"unknown": "01", "2": "02", "26616": "03"}
    assert "ses-2" not in got  # not the raw label


def test_collect_excludes_and_reassigns_out():
    s03 = FakeSubject(
        "s03",
        [
            FakeSession("22461", "2020-04-01"),
            FakeSession("22752", "2021-02-12"),  # reassigned to s10
        ],
    )
    s10 = FakeSubject("s10", [FakeSession("22400", "2021-01-01")])
    overrides = {"s03": {"22752": {"reassign_to": "s10"}}}

    s03_sessions = sm.collect_sessions("s03", [s03, s10], {}, overrides)
    assert {s["label"] for s in s03_sessions} == {"22461"}  # 22752 dropped

    s10_sessions = sm.collect_sessions("s10", [s03, s10], {}, overrides)
    assert {s["label"] for s in s10_sessions} == {"22400", "22752"}  # pulled in


def test_collect_honors_exclude():
    s29 = FakeSubject(
        "s29",
        [FakeSession("22424", "2020-11-11"), FakeSession("22500", "2021-03-05")],
    )
    overrides = {"s29": {"22424": {"exclude": True}}}
    got = sm.collect_sessions("s29", [s29], {}, overrides)
    assert {s["label"] for s in got} == {"22500"}


def test_collect_merges_aliases():
    s29 = FakeSubject("s29", [FakeSession("22500", "2021-01-01")])
    s29b = FakeSubject("s29-2", [FakeSession("22600", "2021-03-05")])
    got = sm.collect_sessions("s29", [s29, s29b], {"s29-2": "s29"}, {})
    assert {s["label"] for s in got} == {"22500", "22600"}


def test_build_flat_map_merges_subjects():
    a = FakeSubject("s01", [FakeSession("100", "2020-01-01"), FakeSession("101", "2020-02-01")])
    b = FakeSubject("s02", [FakeSession("200", "2020-01-01")])
    flat = sm.build_flat_map([a, b], ["s01", "s02"], aliases={}, overrides={})
    assert flat == {"100": "01", "101": "02", "200": "01"}
