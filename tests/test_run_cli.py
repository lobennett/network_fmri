"""CLI dispatch tests for fw2bids — backward-compat curate path + datalad path.

The existing `fw2bids <cohort> --live --out X` invocation (a running Slurm job
depends on it) MUST still route to the curate path; `fw2bids datalad <dir>`
routes to dataladify. Underlying functions are monkeypatched so nothing real
runs (no Flywheel, no datalad).
"""

from network_fmri import run


def test_cohort_routes_to_curate(monkeypatch):
    calls = {"curate": [], "export": [], "client": 0}

    monkeypatch.setattr(run, "_client", lambda: (calls.__setitem__("client", 1) or "FW"))
    monkeypatch.setattr(run.curation, "roster", lambda c: ["s03"])
    monkeypatch.setattr(run, "curate_subject",
                        lambda fw, canonical, live: (calls["curate"].append((canonical, live)) or {"s03"}))
    monkeypatch.setattr(run, "_export",
                        lambda subs, out, env: calls["export"].append((sorted(subs), out)))
    # dataladify must NOT be reached on the cohort path
    monkeypatch.setattr(run.datalad_ds, "dataladify",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("wrong path")))

    run.main(["discovery", "--live", "--out", "/some/staging/X"])

    assert calls["curate"] == [("s03", True)]
    assert calls["export"] == [(["s03"], "/some/staging/X")]


def test_datalad_routes_to_dataladify(monkeypatch):
    seen = {}

    def fake_dataladify(path, message="network_fmri: import BIDS", text2git=True):
        seen["path"] = path
        seen["message"] = message

    monkeypatch.setattr(run.datalad_ds, "dataladify", fake_dataladify)
    # curate helpers must NOT be reached on the datalad path
    monkeypatch.setattr(run, "_client",
                        lambda: (_ for _ in ()).throw(AssertionError("wrong path")))

    run.main(["datalad", "/some/dir"])
    assert seen == {"path": "/some/dir", "message": "network_fmri: import BIDS"}

    run.main(["datalad", "/other", "--message", "reimport"])
    assert seen == {"path": "/other", "message": "reimport"}
