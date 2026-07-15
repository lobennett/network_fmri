"""fw2bids — orchestrate Flywheel → BIDS curation for the r01network project.

Thin runner over the pinned ``fw-heudiconv`` fork (the open-source engine) using
this repo's study-specific heuristic + curation map. network_fmri is the single
source of truth for the BIDS dataset. For each canonical subject it (1) queries
Flywheel and writes the chronological ``{accession: "NN"}`` session map, then
(2) drives ``fw-heudiconv-curate`` once per planned job (own/aliased sessions,
plus reassigned-in sessions under a forced subject).

Writing the BIDS dataset is two steps of the fw-heudiconv engine:
  1. ``curate`` (``--live``) persists the BIDS naming into each file's ``info.BIDS``
     on the shared Flywheel project (a WRITE — snapshot first).
  2. ``export`` (``--out``) downloads the tagged files into a BIDS tree on disk.

Curation is DRY-RUN by default (read-only; computes intended BIDS names without
writing). ``--live`` is required to tag Flywheel; ``--out DIR`` additionally
exports the tagged files to ``DIR`` (only meaningful with ``--live``).

Examples
--------
    fw2bids discovery                         # dry-run all discovery subjects
    fw2bids discovery --subject s03           # dry-run one subject
    fw2bids discovery --live --out $SCRATCH/bids_staging/discovery   # tag + export
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from network_fmri import curation, datalad_ds, session_map
from network_fmri.trim import trim_bold_directory

_HEURISTIC = Path(__file__).resolve().parent / "heuristic.py"
_PROJECT = curation._flywheel_config()["project"]

# Seconds to wait between export retries (linear backoff: attempt N waits N * this).
_RETRY_BACKOFF_S = 5


def _client():
    import flywheel

    return flywheel.Client()


def _project_subjects(fw):
    proj = fw.projects.find_first(f'label="{_PROJECT}"')
    if proj is None:
        raise SystemExit(f"Flywheel project {_PROJECT!r} not found")
    return proj.subjects()


def _clear(fw_subject, sessions, env):
    """Wipe any existing ``info.BIDS`` on this subject/session before curating.

    Makes a live run idempotent and, crucially, prevents stale tags from a prior
    curation (e.g. a duplicate gear NIfTI our heuristic no longer selects) from
    surviving into ``export``, where two files at one BIDS path abort the download.
    """
    cmd = [
        "fw-heudiconv-clear",
        "--project", _PROJECT,
        "--subject", fw_subject,
        "--session", *sessions,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit(f"clear failed for {fw_subject} {sessions} (rc={proc.returncode})")
    return proc.stdout + proc.stderr


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


def _export(fw_subjects, out, env, retries=2):
    """Download curated (tagged) files for these Flywheel subjects into a BIDS tree.

    Writes to ``<out>`` via fw-heudiconv-export's destination/directory-name. Include
    the reassignment SOURCE subjects (e.g. s03 for s10's 22752) so reassigned sessions
    — tagged under the target subject — materialize under the right sub-*.

    Retries ``retries`` times on a non-zero exit. Large downloads intermittently die
    on a transient Flywheel ``ChunkedEncodingError``/``IncompleteRead`` mid-file; the
    whole subject is re-fetched on retry, which is cheap when each parallel task
    exports a single subject to its own directory.
    """
    out = Path(out)
    cmd = [
        "fw-heudiconv-export", "--project", _PROJECT,
        "--subject", *sorted(fw_subjects),
        "--destination", str(out.parent or "."), "--directory-name", out.name,
    ]
    proc = None
    for attempt in range(retries + 1):
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if proc.returncode == 0:
            return proc.stdout + proc.stderr
        sys.stderr.write(
            f"[export] attempt {attempt + 1}/{retries + 1} failed "
            f"(rc={proc.returncode}) for {sorted(fw_subjects)}\n"
        )
        if attempt < retries:
            time.sleep(_RETRY_BACKOFF_S * (attempt + 1))
    sys.stderr.write(proc.stdout + proc.stderr)
    raise SystemExit(f"export failed for {sorted(fw_subjects)} (rc={proc.returncode})")


def resolve_fw_subjects(fw, canonical):
    """Flywheel subject labels touched by one canonical subject — READ-ONLY, no writes.

    Same plan as :func:`curate_subject` (own/aliased sessions plus reassigned-in
    sessions under a forced subject), but returns only the fw-subject set. Lets an
    export-only run skip curation entirely — safe to fan out one task per subject
    since it never tags Flywheel.
    """
    subjects = _project_subjects(fw)
    return {job["fw_subject"] for job in session_map.plan_jobs(subjects, canonical)}


def curate_subject(fw, canonical, live=False):
    """Curate one canonical subject; return the set of Flywheel subject labels touched.

    Curation only — export is done once for the whole roster in ``main`` (a
    per-subject export races on the shared output directory). The returned
    fw-subject set lets the caller aggregate a single export ``--subject`` list.
    """
    subjects = _project_subjects(fw)
    smap = session_map.build_flat_map(subjects, [canonical])
    jobs = session_map.plan_jobs(subjects, canonical)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(smap, fh)
        map_path = fh.name
    try:
        for job in jobs:
            env = dict(os.environ, FWBIDS_SESSION_MAP=map_path)
            if job["force_subject"]:
                env["FWBIDS_FORCE_SUBJECT"] = job["force_subject"]
            if live:
                # Reset the BIDS namespace first so re-runs are idempotent and no
                # stale/duplicate tag leaks into export (see _clear docstring).
                _clear(job["fw_subject"], job["sessions"], dict(os.environ))
            _curate(job["fw_subject"], job["sessions"], _HEURISTIC, env, live)
    finally:
        os.unlink(map_path)
    return {job["fw_subject"] for job in jobs}


_COHORTS = ("discovery", "validation", "excluded")


def _curate_main(argv):
    ap = argparse.ArgumentParser(prog="fw2bids", description=__doc__.splitlines()[0])
    ap.add_argument("cohort", choices=list(_COHORTS))
    ap.add_argument("--subject", action="append", help="limit to these subjects (repeatable)")
    ap.add_argument("--live", action="store_true",
                    help="WRITE to Flywheel (default: dry-run, read-only). Snapshot first.")
    ap.add_argument("--out", metavar="BIDS_DIR",
                    help="export the tagged files to this BIDS dir (requires --live)")
    args = ap.parse_args(argv)

    if args.out and not args.live:
        ap.error("--out requires --live (export needs the curated tags)")

    roster = args.subject or curation.roster(args.cohort)
    fw = _client()
    all_fw_subjects: set[str] = set()
    for canonical in roster:
        all_fw_subjects |= curate_subject(fw, canonical, live=args.live)
        print(f"[{canonical}] {'curated LIVE' if args.live else 'dry-run'}")
    if args.live and args.out:
        # Single export for the whole roster — a per-subject export races on the
        # shared output directory (the 2nd subject's mkdir hits FileExistsError).
        _export(all_fw_subjects, args.out, dict(os.environ))
        print(f"[export] {len(sorted(all_fw_subjects))} fw-subjects -> {args.out}")


def _export_main(argv):
    """Export-only path — download already-curated files, no Flywheel writes.

    Curation (``--live``) is a one-time write per cohort; once done, export is a
    pure read. This subcommand fans out cleanly: run one task per ``--subject``,
    each with its own ``--out``, so a Slurm array turns a fragile whole-roster
    download into many small, independent, retryable jobs.
    """
    ap = argparse.ArgumentParser(
        prog="fw2bids export",
        description="Export already-curated (tagged) BIDS files (no Flywheel writes).",
    )
    ap.add_argument("cohort", choices=list(_COHORTS))
    ap.add_argument("--subject", action="append",
                    help="limit to these subjects (repeatable); default: whole cohort roster")
    ap.add_argument("--out", metavar="BIDS_DIR", required=True,
                    help="export destination; point each parallel task at its OWN dir")
    ap.add_argument("--retries", type=int, default=2,
                    help="retry a failed export this many times (transient Flywheel drops)")
    args = ap.parse_args(argv)

    roster = args.subject or curation.roster(args.cohort)
    fw = _client()
    fw_subjects: set[str] = set()
    for canonical in roster:
        fw_subjects |= resolve_fw_subjects(fw, canonical)
    _export(fw_subjects, args.out, dict(os.environ), retries=args.retries)
    print(f"[export] {sorted(fw_subjects)} -> {args.out}")


def _datalad_main(argv):
    ap = argparse.ArgumentParser(
        prog="fw2bids datalad",
        description="Make a staged BIDS tree a DataLad dataset (idempotent).",
    )
    ap.add_argument("bids_dir", help="staged BIDS directory to DataLad-ify")
    ap.add_argument("--message", "-m", default="network_fmri: import BIDS",
                    help="commit message for datalad save")
    args = ap.parse_args(argv)
    datalad_ds.dataladify(args.bids_dir, message=args.message)
    print(f"[datalad] {args.bids_dir} is now a DataLad dataset")


def _trim_main(argv):
    """Trim 7 dummy volumes from every BOLD NIfTI in a staged BIDS tree (idempotent).

    ``fw2bids export`` produces un-trimmed BIDS, but fMRIPrep is run with
    ``--dummy-scans 0``, so this must run on the staged tree before fMRIPrep.
    Safe to re-run: already-trimmed files are skipped via the sidecar
    ``NumberOfVolumesDiscardedByUser`` flag.
    """
    ap = argparse.ArgumentParser(
        prog="fw2bids trim",
        description="Trim 7 dummy (non-steady-state) BOLD volumes in a staged BIDS dir.",
    )
    ap.add_argument("bids_dir", help="staged BIDS directory to trim in place")
    ap.add_argument(
        "--subjects",
        nargs="+",
        default=None,
        help="restrict to these subject IDs (e.g. s10 sub-s19); "
        "default processes all sub-*. Enables array sharding.",
    )
    ap.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="parallel workers over BOLD files (default 1 = serial)",
    )
    args = ap.parse_args(argv)

    summary = trim_bold_directory(args.bids_dir, subjects=args.subjects, jobs=args.jobs)
    print(f"Trimmed: {summary['trimmed']}")
    print(f"Skipped (already trimmed): {summary['skipped_already_trimmed']}")
    print(f"Skipped (too short): {summary['skipped_too_short']}")
    print(f"Errors: {summary['errors']}")
    # NOTE: must return an int, not the summary dict. The `fw2bids` console
    # entrypoint does `sys.exit(main())`; sys.exit(<dict>) prints the dict to
    # stderr and exits with code 1, falsely marking every trim job FAILED.
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Backward-compatible dispatch: a bare cohort is the implicit `curate` path
    # (the existing `fw2bids <cohort> [--live] [--out ...]` invocation is
    # unchanged); `datalad` routes to the DataLad-ify path.
    if argv and argv[0] == "datalad":
        return _datalad_main(argv[1:])
    if argv and argv[0] == "export":
        return _export_main(argv[1:])
    if argv and argv[0] == "trim":
        return _trim_main(argv[1:])
    if argv and argv[0] == "submit":
        from network_fmri.submit import main as _submit_main

        return _submit_main(argv[1:])
    if argv and argv[0] == "pipeline":
        from network_fmri.submit.pipeline import main as _pipeline_main

        return _pipeline_main(argv[1:])
    return _curate_main(argv)


if __name__ == "__main__":
    main()
