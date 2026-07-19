"""CLI dispatch tests for fw2bids — backward-compat curate path + datalad path.

The existing `fw2bids <cohort> --live --out X` invocation (a running Slurm job
depends on it) MUST still route to the curate path; `fw2bids datalad <dir>`
routes to dataladify. Underlying functions are monkeypatched so nothing real
runs (no Flywheel, no datalad).
"""

import pytest

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

    def fake_dataladify(path, message="network_fmri: import BIDS", text2git=True, jobs=None):
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


def test_export_routes_to_export_only(monkeypatch):
    """`fw2bids export <cohort> --out DIR` resolves fw-subjects and exports the whole
    roster WITHOUT curating (no Flywheel writes)."""
    calls = {"resolve": [], "export": []}

    monkeypatch.setattr(run, "_client", lambda: "FW")
    monkeypatch.setattr(run.curation, "roster", lambda c: ["s1035", "s1057"])
    monkeypatch.setattr(run, "resolve_fw_subjects",
                        lambda fw, canonical: (calls["resolve"].append(canonical) or {canonical}))
    monkeypatch.setattr(run, "_export",
                        lambda subs, out, env, retries=2: calls["export"].append((sorted(subs), out, retries)))
    # curate must NOT be reached on the export path
    monkeypatch.setattr(run, "curate_subject",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not curate")))

    run.main(["export", "validation", "--out", "/stage/parts/all", "--retries", "3"])

    assert calls["resolve"] == ["s1035", "s1057"]
    assert calls["export"] == [(["s1035", "s1057"], "/stage/parts/all", 3)]


def test_export_subject_flag_limits_roster(monkeypatch):
    """`--subject` scopes the export to one subject (the per-array-task invocation)
    and bypasses the cohort roster entirely."""
    calls = {"resolve": [], "export": []}

    monkeypatch.setattr(run, "_client", lambda: "FW")
    monkeypatch.setattr(run.curation, "roster",
                        lambda c: (_ for _ in ()).throw(AssertionError("roster must not be read")))
    monkeypatch.setattr(run, "resolve_fw_subjects",
                        lambda fw, canonical: (calls["resolve"].append(canonical) or {canonical}))
    monkeypatch.setattr(run, "_export",
                        lambda subs, out, env, retries=2: calls["export"].append((sorted(subs), out)))

    run.main(["export", "validation", "--subject", "s286", "--out", "/stage/parts/s286"])

    assert calls["resolve"] == ["s286"]
    assert calls["export"] == [(["s286"], "/stage/parts/s286")]


def test_trim_routes_to_trim_bold_directory(monkeypatch):
    """`fw2bids trim <dir> [--subjects ...]` reaches trim_bold_directory and
    never touches Flywheel (no _client call)."""
    calls = {"trim": []}

    monkeypatch.setattr(
        run,
        "trim_bold_directory",
        lambda bids_dir, subjects=None, jobs=1: (
            calls["trim"].append((bids_dir, subjects))
            or {"trimmed": 1, "skipped_already_trimmed": 0, "skipped_too_short": 0, "errors": 0}
        ),
    )
    monkeypatch.setattr(
        run, "_client", lambda: (_ for _ in ()).throw(AssertionError("wrong path"))
    )

    rc = run.main(["trim", "/stage/discovery"])
    assert calls["trim"] == [("/stage/discovery", None)]
    # `fw2bids` console entrypoint does sys.exit(main()) -- returning the summary
    # dict here would make sys.exit(dict) print it and exit 1, falsely marking
    # every trim job FAILED. main() must return an int (0 on success).
    assert rc == 0

    rc2 = run.main(["trim", "/stage/validation", "--subjects", "s10", "s19"])
    assert calls["trim"][-1] == ("/stage/validation", ["s10", "s19"])
    assert rc2 == 0


class _Proc:
    def __init__(self, rc, stdout="out", stderr="err"):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


def test_export_retries_transient_failure(monkeypatch):
    """A single transient non-zero exit (e.g. Flywheel IncompleteRead) is retried
    and then succeeds — the download is re-attempted, not aborted."""
    attempts = {"n": 0}

    def fake_run(cmd, **kw):
        attempts["n"] += 1
        return _Proc(1 if attempts["n"] == 1 else 0)

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    monkeypatch.setattr(run.time, "sleep", lambda s: None)

    out = run._export({"s286"}, "/stage/parts/s286", {}, retries=2)

    assert attempts["n"] == 2          # failed once, retried, succeeded
    assert out == "outerr"


def test_export_raises_after_exhausting_retries(monkeypatch):
    """Persistent failure raises SystemExit only after all retries are spent."""
    attempts = {"n": 0}

    def fake_run(cmd, **kw):
        attempts["n"] += 1
        return _Proc(1)

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    monkeypatch.setattr(run.time, "sleep", lambda s: None)

    with pytest.raises(SystemExit):
        run._export({"s286"}, "/x", {}, retries=1)

    assert attempts["n"] == 2          # initial try + 1 retry


def test_fmap_link_routes_to_link_b0_fields(monkeypatch, capsys):
    from network_fmri import b0link, run

    seen = {}

    def fake_link(cohort_dir):
        seen["dir"] = cohort_dir
        return b0link.LinkSummary(sessions_linked=3, bolds_stamped=27, no_fmap=1, orphan_fmap=0)

    monkeypatch.setattr(run, "link_b0_fields", fake_link)
    # curate/client path must NOT be reached
    monkeypatch.setattr(run, "_client",
                        lambda: (_ for _ in ()).throw(AssertionError("wrong path")))

    rc = run.main(["fmap-link", "/some/discovery"])
    assert rc == 0
    assert seen["dir"] == "/some/discovery"
    out = capsys.readouterr().out
    assert "sessions_linked=3" in out
    assert "no_fmap=1" in out
