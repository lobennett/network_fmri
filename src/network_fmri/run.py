"""fw2bids — orchestrate Flywheel → BIDS curation for the r01network project.

Thin runner over the pinned ``fw-heudiconv`` fork (the open-source engine) using
this repo's study-specific heuristic + curation map. network_fmri is the single
source of truth for the BIDS dataset. For each canonical subject it (1) queries
Flywheel and writes the chronological ``{accession: "NN"}`` session map, then
(2) drives ``fw-heudiconv-curate`` once per planned job (own/aliased sessions,
plus reassigned-in sessions under a forced subject).

Curation is DRY-RUN by default (read-only; computes intended BIDS names without
writing to the shared PHI Flywheel project). ``--live`` must be given explicitly
to mutate Flywheel — do that only after a snapshot.

Examples
--------
    fw2bids discovery                         # dry-run all discovery subjects
    fw2bids discovery --subject s03           # dry-run one subject
    fw2bids validation --live                 # WRITE (snapshot first)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from network_fmri import curation, session_map

_HEURISTIC = Path(__file__).resolve().parent / "heuristic.py"
_PROJECT = curation._flywheel_config()["project"]


def _client():
    import flywheel

    return flywheel.Client()


def _project_subjects(fw):
    proj = fw.projects.find_first(f'label="{_PROJECT}"')
    if proj is None:
        raise SystemExit(f"Flywheel project {_PROJECT!r} not found")
    return proj.subjects()


def _curate(fw_subject, sessions, heuristic, env, live):
    cmd = [
        "fw-heudiconv-curate",
        "--project", _PROJECT,
        "--subject", fw_subject,
        "--session", *sessions,
        "--heuristic", str(heuristic),
    ]
    if not live:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit(f"curate failed for {fw_subject} {sessions} (rc={proc.returncode})")
    return proc.stdout + proc.stderr


def curate_subject(fw, canonical, live=False):
    """Curate one canonical subject; return the concatenated dry-run log text."""
    subjects = _project_subjects(fw)
    smap = session_map.build_flat_map(subjects, [canonical])
    jobs = session_map.plan_jobs(subjects, canonical)

    log = []
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(smap, fh)
        map_path = fh.name
    try:
        for job in jobs:
            env = dict(os.environ, FWBIDS_SESSION_MAP=map_path)
            if job["force_subject"]:
                env["FWBIDS_FORCE_SUBJECT"] = job["force_subject"]
            log.append(_curate(job["fw_subject"], job["sessions"], _HEURISTIC, env, live))
    finally:
        os.unlink(map_path)
    return "\n".join(log)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fw2bids", description=__doc__.splitlines()[0])
    ap.add_argument("cohort", choices=["discovery", "validation", "excluded"])
    ap.add_argument("--subject", action="append", help="limit to these subjects (repeatable)")
    ap.add_argument("--live", action="store_true",
                    help="WRITE to Flywheel (default: dry-run, read-only). Snapshot first.")
    args = ap.parse_args(argv)

    roster = args.subject or curation.roster(args.cohort)
    fw = _client()
    for canonical in roster:
        curate_subject(fw, canonical, live=args.live)
        print(f"[{canonical}] curated ({'LIVE' if args.live else 'dry-run'})")


if __name__ == "__main__":
    main()
