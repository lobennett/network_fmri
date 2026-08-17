"""Tests for network_fmri.prune — physically remove excluded scans, then renumber
the survivors so run indices are contiguous from run-1.

Why physical deletion rather than a bids-filter-file: a filter dict has no per-task
run selectivity (it can say "these 19 tasks", never "goNogo run-2 but not run-1, and
only in ses-01"), so it cannot express a per-scan quality call. Deleting the file has
exactly that precision, and it makes the BIDS tree map 1:1 onto the derivatives —
no filter plumbing anywhere, and at subject level babs generates no filter of its own.

Provenance is not lost: this runs as a DAG stage (so Flywheel -> BIDS -> prune
reproduces the tree), the reasons live in code/exclusions_lock.json, the old->new
mapping is recorded in code/pruned.tsv, and DataLad keeps the deleted content
recoverable from history (as long as nobody runs `git annex dropunused`).
"""
from pathlib import Path

import pytest

from network_fmri.prune import apply_prune, plan_prune


def _scan(bids, sub, ses, task, run, *, echoes=(1, 2, 3), events=False):
    """Create one multi-echo scan (bold + sidecar per echo), optionally with events."""
    func = bids / f"sub-{sub}" / f"ses-{ses}" / "func"
    func.mkdir(parents=True, exist_ok=True)
    stem = f"sub-{sub}_ses-{ses}_task-{task}_run-{run}"
    for e in echoes:
        (func / f"{stem}_echo-{e}_bold.nii.gz").write_text(f"nii {stem} echo{e}")
        (func / f"{stem}_echo-{e}_bold.json").write_text("{}")
    if events:
        (func / f"{stem}_events.tsv").write_text("onset\tduration\n0\t1\n")
    return func


def _excl(sub, ses, task, run, source="short-run"):
    return {"subject": f"sub-{sub}", "session": f"ses-{ses}", "task": f"task-{task}",
            "run": f"run-{run}", "source": source, "action": "exclude",
            "reason": f"{run} is truncated"}


def test_deletes_every_file_of_an_excluded_single_run_scan(tmp_path):
    func = _scan(tmp_path, "s19", "02", "goNogo", 1, events=True)
    _scan(tmp_path, "s19", "02", "nBack", 1, events=True)

    plan = plan_prune(tmp_path, [_excl("s19", "02", "goNogo", 1)])
    apply_prune(plan)

    assert not list(func.glob("*goNogo*")), "all echoes + sidecar + events must go"
    assert len(list(func.glob("*nBack*"))) == 7, "untouched task keeps 3 echoes x2 + events"


def test_renumbers_surviving_run_to_run_1(tmp_path):
    """run-1 excluded, run-2 good -> run-2 becomes run-1, so no gap is left behind."""
    func = _scan(tmp_path, "s10", "01", "goNogo", 1)
    _scan(tmp_path, "s10", "01", "goNogo", 2, events=True)

    plan = plan_prune(tmp_path, [_excl("s10", "01", "goNogo", 1)])
    apply_prune(plan)

    names = sorted(p.name for p in func.glob("*goNogo*"))
    assert names == [
        "sub-s10_ses-01_task-goNogo_run-1_echo-1_bold.json",
        "sub-s10_ses-01_task-goNogo_run-1_echo-1_bold.nii.gz",
        "sub-s10_ses-01_task-goNogo_run-1_echo-2_bold.json",
        "sub-s10_ses-01_task-goNogo_run-1_echo-2_bold.nii.gz",
        "sub-s10_ses-01_task-goNogo_run-1_echo-3_bold.json",
        "sub-s10_ses-01_task-goNogo_run-1_echo-3_bold.nii.gz",
        "sub-s10_ses-01_task-goNogo_run-1_events.tsv",
    ]
    # the surviving run's own content moved, not the deleted one's
    assert "echo1" in (func / "sub-s10_ses-01_task-goNogo_run-1_echo-1_bold.nii.gz").read_text()


def test_renumber_is_contiguous_with_three_runs(tmp_path):
    """runs 1,2,3 with run-2 excluded -> survivors become run-1, run-2."""
    func = _scan(tmp_path, "s29", "05", "rest", 1)
    _scan(tmp_path, "s29", "05", "rest", 2)
    _scan(tmp_path, "s29", "05", "rest", 3)

    plan = plan_prune(tmp_path, [_excl("s29", "05", "rest", 2)])
    apply_prune(plan)

    runs = sorted({p.name.split("_run-")[1].split("_")[0] for p in func.glob("*rest*")})
    assert runs == ["1", "2"]
    # run-3's content must land on run-2, not run-1
    assert "nii sub-s29_ses-05_task-rest_run-3" in (
        func / "sub-s29_ses-05_task-rest_run-2_echo-1_bold.nii.gz"
    ).read_text()


def test_refuses_to_delete_the_only_events_file_of_a_task(tmp_path):
    """Real case: s10/ses-01 goNogo has its events.tsv on the EXCLUDED run-1 while the
    good run-2 has none (the reconciliation manifest is missing dest_run=2). Deleting
    run-1 would take the only events with it and silently drop the good run from lev1,
    so refuse and say so rather than destroy data."""
    _scan(tmp_path, "s10", "01", "goNogo", 1, events=True)
    _scan(tmp_path, "s10", "01", "goNogo", 2)  # good run, no events

    with pytest.raises(ValueError, match="only events.tsv"):
        plan_prune(tmp_path, [_excl("s10", "01", "goNogo", 1)])


def test_deleting_the_only_events_is_fine_when_no_run_survives(tmp_path):
    """If the whole task goes, its events go with it — nothing is orphaned."""
    func = _scan(tmp_path, "s19", "07", "stopSignal", 1, events=True)

    plan = plan_prune(tmp_path, [_excl("s19", "07", "stopSignal", 1)])
    apply_prune(plan)

    assert not list(func.glob("*stopSignal*"))


def test_only_requested_sources_are_pruned(tmp_path):
    """behavioral-qc means the events logfile is defective and the BOLD is fine, so
    fMRIPrep should still preprocess it; it's excluded later at lev1."""
    func = _scan(tmp_path, "s03", "11", "stopSignalWDirectedForgetting", 1, events=True)

    plan = plan_prune(
        tmp_path,
        [_excl("s03", "11", "stopSignalWDirectedForgetting", 1, source="behavioral-qc")],
    )
    apply_prune(plan)

    assert len(list(func.glob("*stopSignalWDirectedForgetting*"))) == 7


def test_prunes_non_selected_anat(tmp_path):
    """Anat QC picks one T1w per subject (s03 keeps ses-13 over ses-05, cjv 0.69 vs
    0.98). Expressed as a keep-map so the losing anat is deleted by the same stage
    instead of by hand in a clone."""
    for ses in ("05", "13"):
        anat = tmp_path / "sub-s03" / f"ses-{ses}" / "anat"
        anat.mkdir(parents=True)
        for ext in ("nii.gz", "json"):
            (anat / f"sub-s03_ses-{ses}_acq-SagMPRAGE_run-1_T1w.{ext}").write_text("x")

    plan = plan_prune(tmp_path, [], anat_keep={"sub-s03": "ses-13"},
                      anat_acquisition="SagMPRAGE")
    apply_prune(plan)

    assert not list((tmp_path / "sub-s03" / "ses-05" / "anat").glob("*SagMPRAGE*T1w*"))
    assert len(list((tmp_path / "sub-s03" / "ses-13" / "anat").glob("*SagMPRAGE*T1w*"))) == 2


def test_records_old_to_new_mapping(tmp_path):
    """code/pruned.tsv is the machine-readable record of what this stage did."""
    _scan(tmp_path, "s10", "01", "goNogo", 1)
    _scan(tmp_path, "s10", "01", "goNogo", 2, events=True)

    plan = plan_prune(tmp_path, [_excl("s10", "01", "goNogo", 1)])
    apply_prune(plan)

    rows = (tmp_path / "code" / "pruned.tsv").read_text().splitlines()
    assert rows[0] == "action\told\tnew\treason"
    assert any(r.startswith("delete\t") and "run-1_echo-1_bold.nii.gz" in r for r in rows)
    assert any(r.startswith("rename\t") and "run-2_echo-1_bold.nii.gz" in r for r in rows)


def test_is_idempotent(tmp_path):
    """Re-running finds nothing to do — the excluded scan is already gone, and the
    renumbered survivor must NOT be mistaken for the excluded run-1."""
    func = _scan(tmp_path, "s10", "01", "goNogo", 1)
    _scan(tmp_path, "s10", "01", "goNogo", 2, events=True)
    apply_prune(plan_prune(tmp_path, [_excl("s10", "01", "goNogo", 1)]))
    before = sorted(p.name for p in func.glob("*"))

    plan2 = plan_prune(tmp_path, [_excl("s10", "01", "goNogo", 1)])
    apply_prune(plan2)

    assert plan2.deletions == [] and plan2.renames == []
    assert sorted(p.name for p in func.glob("*")) == before


def test_dry_run_plan_touches_nothing(tmp_path):
    func = _scan(tmp_path, "s19", "02", "goNogo", 1, events=True)
    plan = plan_prune(tmp_path, [_excl("s19", "02", "goNogo", 1)])
    assert len(plan.deletions) == 7
    assert list(func.glob("*goNogo*")), "planning must not delete anything"


def test_renumbered_scan_records_its_acquisition_run(tmp_path):
    """The rename is not information-destroying: the sidecar keeps the run label the
    scan was acquired under, which is also how re-runs detect prior work."""
    import json

    func = _scan(tmp_path, "s10", "01", "goNogo", 1)
    _scan(tmp_path, "s10", "01", "goNogo", 2, events=True)

    apply_prune(plan_prune(tmp_path, [_excl("s10", "01", "goNogo", 1)]))

    sidecar = json.loads(
        (func / "sub-s10_ses-01_task-goNogo_run-1_echo-1_bold.json").read_text()
    )
    assert sidecar["OriginalRun"] == "run-2"


def test_fixes_up_scans_tsv_to_match_the_tree(tmp_path):
    """scans.tsv lists excluded scans with their `why`. Once those files are deleted the
    rows point at nothing, which the BIDS validator rejects
    (SCANS_FILENAME_NOT_MATCH_DATASET) — so prune drops rows for deleted files and
    rewrites rows for renumbered ones. The `why` for a pruned scan lives on in
    exclusions_lock.json + code/pruned.tsv."""
    func = _scan(tmp_path, "s19", "02", "goNogo", 1, events=True)
    _scan(tmp_path, "s19", "02", "nBack", 1, events=True)
    _scan(tmp_path, "s19", "02", "nBack", 2, events=True)
    ses_dir = func.parent
    (ses_dir / "sub-s19_ses-02_scans.tsv").write_text(
        "filename\twhy\n"
        "func/sub-s19_ses-02_task-goNogo_run-1_echo-1_bold.nii.gz\t22/383 TRs\n"
        "func/sub-s19_ses-02_task-nBack_run-2_echo-1_bold.nii.gz\tbad events\n"
    )

    apply_prune(plan_prune(tmp_path, [_excl("s19", "02", "goNogo", 1),
                                      _excl("s19", "02", "nBack", 1)]))

    rows = (ses_dir / "sub-s19_ses-02_scans.tsv").read_text().splitlines()
    assert rows[0] == "filename\twhy"
    # the deleted goNogo row is gone
    assert not any("goNogo" in r for r in rows)
    # nBack run-2 survived but was renumbered to run-1, so its row follows it
    assert any("task-nBack_run-1_echo-1_bold.nii.gz\tbad events" in r for r in rows)
    # every listed file exists
    for row in rows[1:]:
        assert (ses_dir / row.split("\t")[0]).exists(), row


def _deriv_scan(bids, pipeline, sub, ses, task, run, echoes=(1, 2, 3)):
    """Mirror a scan under derivatives/<pipeline>/ with a desc- entity."""
    func = bids / "derivatives" / pipeline / f"sub-{sub}" / f"ses-{ses}" / "func"
    func.mkdir(parents=True, exist_ok=True)
    stem = f"sub-{sub}_ses-{ses}_task-{task}_run-{run}"
    for e in echoes:
        (func / f"{stem}_echo-{e}_desc-trimmed_bold.nii.gz").write_text("nii")
        (func / f"{stem}_echo-{e}_desc-trimmed_bold.json").write_text("{}")
    return func


def test_prunes_matching_files_under_derivatives(tmp_path):
    """Derivatives mirror the raw tree, so a pruned scan must not survive there.

    They can't be cleaned up in a later pass either: pruning is idempotent per
    (subject, session, task), so a second run skips the group entirely.
    """
    _scan(tmp_path, "s19", "07", "stopSignal", 1, events=True)
    deriv = _deriv_scan(tmp_path, "trimmed", "s19", "07", "stopSignal", 1)

    apply_prune(plan_prune(tmp_path, [_excl("s19", "07", "stopSignal", 1)]))

    assert not list(deriv.glob("*stopSignal*")), "derivative copies must go too"


def test_renumbers_derivatives_in_step_with_the_raw_tree(tmp_path):
    """A renumbered survivor keeps raw and derivative filenames in agreement."""
    _scan(tmp_path, "s10", "01", "goNogo", 1)
    _scan(tmp_path, "s10", "01", "goNogo", 2, events=True)
    _deriv_scan(tmp_path, "trimmed", "s10", "01", "goNogo", 1)
    deriv = _deriv_scan(tmp_path, "trimmed", "s10", "01", "goNogo", 2)

    apply_prune(plan_prune(tmp_path, [_excl("s10", "01", "goNogo", 1)]))

    names = sorted(p.name for p in deriv.glob("*goNogo*"))
    assert names == [
        "sub-s10_ses-01_task-goNogo_run-1_echo-1_desc-trimmed_bold.json",
        "sub-s10_ses-01_task-goNogo_run-1_echo-1_desc-trimmed_bold.nii.gz",
        "sub-s10_ses-01_task-goNogo_run-1_echo-2_desc-trimmed_bold.json",
        "sub-s10_ses-01_task-goNogo_run-1_echo-2_desc-trimmed_bold.nii.gz",
        "sub-s10_ses-01_task-goNogo_run-1_echo-3_desc-trimmed_bold.json",
        "sub-s10_ses-01_task-goNogo_run-1_echo-3_desc-trimmed_bold.nii.gz",
    ]
