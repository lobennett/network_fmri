"""Audit raw behavioral files against the BIDS tree.

Read-only. Reports, per (subject, session, task), whether the in-scanner behavioral
file and the BOLD run(s) correspond 1:1 — the precondition for writing events.tsv.

Four filename regimes coexist in the raw tree and none encodes a run index, so run
assignment can never come from a filename. Out-of-scanner practice data is excluded:
it appears both in `practice/` subdirectories and loose in session directories.
"""

from __future__ import annotations

import argparse
import collections
import csv
import os
import re
import sys
from pathlib import Path

RAW_ROOT = Path("/oak/stanford/groups/russpold/data/network_grant/behavioral_data/raw_cleaned")

# Behavioral task token -> BIDS task. Dual tasks appear in both word orders.
TASKS = {
    "go_nogo": "goNogo", "n_back": "nBack", "flanker": "flanker",
    "directed_forgetting": "directedForgetting", "cued_task_switching": "cuedTS",
    "spatial_task_switching": "spatialTS", "shape_matching": "shapeMatching",
    "stop_signal": "stopSignal",
    "stop_signal_with_flanker": "stopSignalWFlanker",
    "stop_signal_with_directed_forgetting": "stopSignalWDirectedForgetting",
    "directed_forgetting_with_flanker": "directedForgettingWFlanker",
    "flanker_with_shape_matching": "flankerWShapeMatching",
    "flanker_with_cued_task_switching": "cuedTSWFlanker",
    "cued_task_switching_with_directed_forgetting": "directedForgettingWCuedTS",
    "cued_task_switching_with_flanker": "cuedTSWFlanker",
    "spatial_task_switching_with_cued_task_switching": "spatialTSWCuedTS",
    "cued_task_switching_with_spatial_task_switching": "spatialTSWCuedTS",
    "shape_matching_with_cued_task_switching": "shapeMatchingWCuedTS",
    "cued_task_switching_with_shape_matching": "shapeMatchingWCuedTS",
    "shape_matching_with_spatial_task_switching": "spatialTSWShapeMatching",
    "spatial_task_switching_with_shape_matching": "spatialTSWShapeMatching",
    "n_back_with_shape_matching": "nBackWShapeMatching",
    "n_back_with_spatial_task_switching": "nBackWSpatialTS",
    # `*_desc_beh` regime writes the task in camelCase
    "spatialtaskswitching": "spatialTS", "cuedtaskswitching": "cuedTS",
    "nback": "nBack", "gonogo": "goNogo", "stopsignal": "stopSignal",
    "shapematching": "shapeMatching", "directedforgetting": "directedForgetting",
}


def parse_task(name: str) -> str | None:
    """BIDS task for an in-scanner behavioral filename, or None."""
    if "practice" in name.lower():
        return None
    for rx in (r"^(.+?)(?:_single_task_network)?__fmri_results",
               r"^sub-\S+?_ses[-_]\d+_task-(.+?)_desc[-_](?:raw|beh)\.csv$"):
        m = re.match(rx, name)
        if m:
            tok = m.group(1).replace("-", "_")
            return TASKS.get(tok) or TASKS.get(tok.replace("_", "").lower())
    return None


def bids_runs(bids_root: Path, subjects: list[str]) -> dict:
    """{(subject, session, task): {run, ...}} from echo-1 BOLD filenames."""
    runs = collections.defaultdict(set)
    for f in bids_root.glob("sub-*/ses-*/func/*echo-1_bold.nii.gz"):
        m = re.match(r"sub-(\S+?)_ses-(\d+)_task-(\S+?)_run-(\d+)_", f.name)
        if m and m.group(1) in subjects:
            runs[(m.group(1), m.group(2), m.group(3))].add(int(m.group(4)))
    return runs


def behavior_files(subjects: list[str], root: Path = RAW_ROOT) -> tuple[dict, list]:
    """{(subject, session, task): [filenames]} plus any unparsed names."""
    found, unparsed = collections.defaultdict(list), []
    for sub in subjects:
        for sesdir in sorted((root / sub).glob("ses-*")):
            ses = sesdir.name.split("-")[1].zfill(2)
            for f in sorted(sesdir.iterdir()):
                if f.is_dir() or f.suffix != ".csv":
                    continue
                task = parse_task(f.name)
                if task:
                    found[(sub, ses, task)].append(f.name)
                elif "practice" not in f.name.lower():
                    unparsed.append(f"{sub}/{sesdir.name}/{f.name}")
    return found, unparsed


# Subjects whose behavioral sessions do not map 1:1 onto populated BIDS sessions.
# s321's first visit was split across two scans (BIDS ses-01 {flanker,nBack,stopSignal}
# + ses-02 {spatialTS} == behavioral ses-01), so it has one more populated session.
SESSION_OVERRIDES = {
    "s321": {"01": "01", "02": "03", "03": "04", "04": "05", "05": "06", "06": "07",
             "07": "08", "08": "09", "09": "10", "10": "11", "11": "12", "12": "13"},
}
# Tasks in a split session that live in the *next* BIDS session.
SPLIT_TASKS = {("s321", "01"): {"spatialTS": "02"}}


def session_map(subject: str, beh_sessions: list[str], populated: list[str]) -> dict:
    """behavioral session -> BIDS session.

    Rule: the Nth behavioral session is the Nth BIDS session that contains functional
    runs. A session number missing from the BIDS tree is one that produced no func
    (anat-only or fully excluded), which shifts every later session by one.
    """
    if subject in SESSION_OVERRIDES:
        return SESSION_OVERRIDES[subject]
    if len(beh_sessions) != len(populated):
        return {}                      # cannot align; caller flags it
    return dict(zip(beh_sessions, populated))


def pick_run(runs: list[int], volumes: dict, median: float) -> tuple[int, list[int]]:
    """Which run the behavioral file belongs to, plus the aborted runs to drop.

    With more than one run, the behavioral file belongs to the one closest to the
    task's cohort median; the others are false starts.
    """
    if len(runs) == 1:
        return runs[0], []
    best = min(runs, key=lambda r: abs(volumes.get(r, 0) - median))
    return best, [r for r in runs if r != best]


def classify(runs: list[int], n_behavior: int, task: str) -> str:
    if task == "rest":
        return "rest_no_behavior_expected"
    if runs and not n_behavior:
        return "BOLD_without_behavior"
    if n_behavior and not runs:
        return "behavior_without_BOLD"
    if len(runs) == n_behavior == 1:
        return "ok_1to1"
    if len(runs) > 1 and n_behavior == 1:
        return "AMBIGUOUS_multirun_single_behavior"
    return f"other_{len(runs)}bold_{n_behavior}beh"


def get_parser() -> argparse.ArgumentParser:
    from network_fmri.cli import DEFAULT_STAGING
    from network_fmri.cohorts import COHORTS

    p = argparse.ArgumentParser(prog="network_fmri behavior-inventory")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--bids-root", default=None,
                   help="override; defaults to <staging>/<cohort>/bids")
    p.add_argument("--out", default=None, help="TSV output path")
    return p


def main(argv: list[str] | None = None) -> int:
    from network_fmri.cohorts import roster

    args = get_parser().parse_args(argv)
    subjects = roster(args.cohort)
    bids_root = Path(args.bids_root) if args.bids_root else \
        Path(args.staging) / args.cohort / "bids"
    if not bids_root.is_dir():
        raise SystemExit(f"no BIDS tree at {bids_root}")

    runs = bids_runs(bids_root, subjects)
    beh, unparsed = behavior_files(subjects)

    counts, rows = collections.Counter(), []
    for key in sorted(set(runs) | set(beh)):
        sub, ses, task = key
        r, files = sorted(runs.get(key, [])), beh.get(key, [])
        status = classify(r, len(files), task)
        counts[status] += 1
        rows.append([sub, ses, task, len(r), ",".join(map(str, r)), len(files),
                     status, ";".join(files)])

    out = Path(args.out) if args.out else \
        Path(args.staging) / "logs" / args.cohort / "behavior_inventory.tsv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["subject", "session", "task", "n_bold_runs", "runs",
                    "n_behavior", "status", "behavior_files"])
        w.writerows(rows)

    print(f"[{args.cohort}] {len(rows)} units, unparsed behavioral names: {len(unparsed)}")
    for u in unparsed[:10]:
        print(f"    {u}")
    for status, n in counts.most_common():
        print(f"  {n:5d}  {status}")
    print(f"\nneeds a decision:")
    for r in rows:
        if r[6] not in ("ok_1to1", "rest_no_behavior_expected"):
            print(f"  sub-{r[0]:7s} ses-{r[1]} {r[2]:30s} runs={r[4] or '-':6s} beh={r[5]}  {r[6]}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


def resolve(bids_root: Path, subjects: list[str]) -> tuple[list[dict], collections.Counter]:
    """Resolve every in-scanner behavioral file to a (session, task, run) in BIDS terms."""
    import nibabel as nib

    runs = bids_runs(bids_root, subjects)
    beh, _ = behavior_files(subjects)

    volumes, by_task = {}, collections.defaultdict(list)
    for f in bids_root.glob("sub-*/ses-*/func/*echo-1_bold.nii.gz"):
        m = re.match(r"sub-(\S+?)_ses-(\d+)_task-(\S+?)_run-(\d+)_", f.name)
        if not m:
            continue
        n = nib.load(str(f)).shape[3]
        volumes[(m.group(1), m.group(2), m.group(3), int(m.group(4)))] = n
        if m.group(3) != "rest":
            by_task[m.group(3)].append(n)
    median = {t: sorted(v)[len(v) // 2] for t, v in by_task.items()}

    populated = collections.defaultdict(list)
    for (sub, ses, task) in runs:
        if task != "rest" and ses not in populated[sub]:
            populated[sub].append(ses)
    for sub in populated:
        populated[sub].sort()

    rows, stats = [], collections.Counter()
    for sub in subjects:
        bses = sorted({s for (s, ses, t) in beh if s == sub for s in [ses]})
        beh_sessions = sorted({ses for (s, ses, t) in beh if s == sub})
        smap = session_map(sub, beh_sessions, populated.get(sub, []))
        for (s, ses, task), files in sorted(beh.items()):
            if s != sub:
                continue
            dest_ses = SPLIT_TASKS.get((sub, ses), {}).get(task) or smap.get(ses)
            if not dest_ses:
                stats["unaligned_session"] += 1
                rows.append(dict(subject=sub, beh_session=ses, task=task, bids_session="",
                                 run="", status="unaligned_session", src=files[0], dest=""))
                continue
            r = sorted(runs.get((sub, dest_ses, task), []))
            if not r:
                stats["no_matching_bold"] += 1
                rows.append(dict(subject=sub, beh_session=ses, task=task,
                                 bids_session=dest_ses, run="", status="no_matching_bold",
                                 src=files[0], dest=""))
                continue
            vols = {n: volumes.get((sub, dest_ses, task, n), 0) for n in r}
            run, dropped = pick_run(r, vols, median.get(task, 0))
            status = "ok" if len(r) == 1 else f"picked_run-{run}_dropped{dropped}"
            stats["resolved"] += 1
            if len(files) > 1:
                stats["multiple_behavior_files"] += 1
            rows.append(dict(subject=sub, beh_session=ses, task=task, bids_session=dest_ses,
                             run=run, status=status, src=files[0],
                             dest=f"sub-{sub}/ses-{dest_ses}/"
                                  f"sub-{sub}_ses-{dest_ses}_task-{task}_run-{run}_beh.csv"))
    return rows, stats


def clean(argv: list[str] | None = None) -> int:
    """Materialise the cleaned 1:1 behavioral tree under the BIDS dataset.

    The result is canonical: one CSV per BOLD run, named for the run it belongs to.
    Raw behavioral files carry no trim adjustment, so consumers apply the onset shift
    unconditionally.
    """
    import shutil

    from network_fmri.cohorts import roster

    p = argparse.ArgumentParser(prog="network_fmri behavior-clean-run")
    p.add_argument("--cohort", required=True)
    p.add_argument("--bids-dir", required=True)
    p.add_argument("--out", default="sourcedata/behavioral")
    args = p.parse_args(argv)

    bids_root = Path(args.bids_dir)
    rows, stats = resolve(bids_root, roster(args.cohort))
    out_root = bids_root / args.out
    out_root.mkdir(parents=True, exist_ok=True)

    copied = 0
    for r in rows:
        if not r["dest"]:
            continue
        src = RAW_ROOT / r["subject"] / f"ses-{int(r['beh_session'])}" / r["src"]
        if not src.is_file():
            src = RAW_ROOT / r["subject"] / f"ses-{r['beh_session']}" / r["src"]
        dst = out_root / r["dest"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    # No mapping file is written: this tree becomes canonical, so a table of paths
    # into the raw tree would go stale the moment that tree is archived. Decisions
    # that are not recoverable from the result are logged here and in
    # docs/SCAN-NOTES.md instead.
    print(f"[behavior-clean] copied {copied} files -> {out_root}")
    for k, v in stats.most_common():
        print(f"  {v:5d}  {k}")
    for r in rows:
        if r["status"] != "ok":
            print(f"  DECISION {r['subject']} beh ses-{r['beh_session']} {r['task']} -> "
                  f"ses-{r['bids_session'] or '--'} run-{r['run'] or '-'}  {r['status']}")
    return 0
