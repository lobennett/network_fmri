"""Mark QA-failed acquisitions on Flywheel so later pulls skip them.

Two changes are needed, and the label alone is not enough. Renaming stops `curate` from
tagging the file again, but `curate` only ever adds tags -- a tag written by an earlier
run survives, and `export` downloads anything whose ``info.BIDS.ignore`` is falsy. So the
files also get ``ignore`` set.

Appends ``_qa-reject`` to the acquisition label, which makes
:func:`network_fmri.acquisitions.map_acquisition` return ``None`` — the scan is never
curated, so ``export`` never downloads it. Fixing it at the source keeps the decision
out of a per-subject table in this package and survives a fresh pull.

Targets are given as ``subject/bids_session/suffix`` (e.g. ``s03/05/T1w``). The BIDS
session number is resolved through the same chronological map curate uses, so the
argument matches what you see in the BIDS tree rather than a Flywheel accession.

:data:`REJECTS` is the set applied to the project, so ``qa-reject --apply`` with no
``--target`` replays every decision made so far. Marking is idempotent, so replaying costs
nothing; without this list a fresh Flywheel project could not be brought to the same state
from the repository alone.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from network_fmri.fw2bids import sessions
from network_fmri.fw2bids.acquisitions import NON_FUNC, QA_REJECT

MARKER = "_qa-reject"

# Anatomicals dropped on MRIQC evidence -- which scan won, and why, is in
# docs/SCAN-NOTES.md. Order is the order applied.
REJECTS = (
    "s03/05/T1w",
    "s19/03/T1w",
    "s19/03/T2w",
    "s29/01/T2w",
    "s1127/01/T1w",
    "s1258/01/T1w",
    "s1270/01/T1w",
    "s1351/08/T1w",
    "s216/01/T1w",
    "s1399/02/T2w",
)


def suffix_labels(suffix: str) -> set[str]:
    """Labels mapping to a BIDS suffix, marked or not, so re-running is idempotent."""
    base = {label for label, e in NON_FUNC.items() if e.get("suffix") == suffix}
    return base | {label + MARKER for label in base}


def plan(client, project_label: str, targets: list[str]) -> list[dict]:
    """Resolve ``subject/session/suffix`` targets to concrete acquisitions."""
    project = client.projects.find_first(f'label="{project_label}"')
    if project is None:
        raise SystemExit(f"Flywheel project {project_label!r} not found")
    all_subjects = project.subjects()

    out = []
    for target in targets:
        sub, ses_num, suffix = target.split("/")
        want = suffix_labels(suffix)
        if not want:
            raise SystemExit(f"no acquisition label maps to suffix {suffix!r}")

        # Exactly what curate does, so a target names the same session the BIDS tree does.
        records = sessions.collect(all_subjects, sub)
        if not records:
            raise SystemExit(f"{target}: no Flywheel sessions for subject {sub!r}")
        numbers = sessions.timeline(records, sessions.SESSION_MERGES.get(sub))
        want_labels = {lab for lab, n in numbers.items() if n == ses_num.zfill(2)}
        if not want_labels:
            raise SystemExit(f"{target}: no session numbered {ses_num} for {sub}")

        for rec in records:
            if sessions.normalize(rec["label"]) not in want_labels:
                continue
            subj = next(s for s in all_subjects if s.label == rec["fw_subject"])
            sess = next(s for s in subj.sessions() if s.label == rec["label"])
            for acq in sess.acquisitions():
                if acq.label in want:
                    out.append(dict(target=target, session_label=sess.label,
                                    acquisition_id=acq.id, old=acq.label,
                                    new=acq.label + MARKER))
    return out


def main(argv: list[str] | None = None) -> int:
    import flywheel

    p = argparse.ArgumentParser(prog="network_fmri qa-reject")
    p.add_argument("--project", default="r01network")
    p.add_argument("--target", nargs="+", default=list(REJECTS),
                   help="subject/bids_session/suffix, e.g. s03/05/T1w "
                        "(default: every target in REJECTS)")
    p.add_argument("--rollback", help="write a rollback record here before applying")
    p.add_argument("--apply", action="store_true", help="without this, only print the plan")
    args = p.parse_args(argv)

    client = flywheel.Client()
    steps = plan(client, args.project, args.target)
    if not steps:
        print("[qa-reject] nothing matched", flush=True)
        return 1

    for s in steps:
        done = " (already marked)" if QA_REJECT.search(s["old"]) else ""
        print(f"  {s['target']}: session {s['session_label']} {s['old']!r} -> {s['new']!r}{done}")

    todo = [s for s in steps if not QA_REJECT.search(s["old"])]
    if not args.apply:
        print(f"[qa-reject] dry run: {len(todo)} to apply", flush=True)
        return 0

    if args.rollback:
        Path(args.rollback).write_text(json.dumps(steps, indent=2) + "\n")
        print(f"[qa-reject] rollback written to {args.rollback}", flush=True)

    for s in todo:
        client.modify_acquisition(s["acquisition_id"], {"label": s["new"]})
    print(f"[qa-reject] renamed {len(todo)} acquisition(s)", flush=True)

    # Neutralise tags an earlier curate already wrote, which export would still honour.
    ignored = 0
    for s in steps:
        for f in client.get_acquisition(s["acquisition_id"]).files:
            if not f.name.endswith((".nii.gz", ".json")):
                continue
            client.set_acquisition_file_info(s["acquisition_id"], f.name,
                                             {"BIDS": {"ignore": True}})
            ignored += 1
    print(f"[qa-reject] set BIDS.ignore on {ignored} file(s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
