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
