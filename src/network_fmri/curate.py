"""``network_fmri curate`` — the per-subject payload each array task runs.

Builds the chronological session map for one subject, then invokes
``fw-heudiconv-curate`` once per ``(flywheel subject, forced subject)`` group with
the map (and any forced subject) in the environment for the heuristic to read.

Dry run by default; ``--live`` writes the BIDS naming into ``info.BIDS`` on the
shared Flywheel project. ``--out`` then downloads the tagged files, which only
makes sense with ``--live`` because export reads what curate wrote.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from network_fmri import sessions

HEURISTIC = Path(__file__).parent / "heuristic.py"

RETRY_BACKOFF_S = 5


def run_with_retries(cmd: list[str], what: str, retries: int, env: dict | None = None) -> None:
    """Run ``cmd`` with linear backoff. Curate hits HTTP 500s under load; large
    downloads die on transient connection drops."""
    for attempt in range(retries + 1):
        if subprocess.run(cmd, env=env).returncode == 0:
            return
        print(f"[{what}] attempt {attempt + 1}/{retries + 1} failed",
              file=sys.stderr, flush=True)
        if attempt < retries:
            time.sleep(RETRY_BACKOFF_S * (attempt + 1))
    raise SystemExit(f"{what} failed after {retries + 1} attempts")


def export(project: str, fw_subjects: set[str], out: Path, retries: int = 2) -> None:
    """Download tagged files into ``out``, which is wiped first.

    The engine rmtree's its output root on any file conflict, so a partial export
    would otherwise poison every retry. ``out`` must be a dir this task owns.
    """
    if out.exists():
        print(f"[export] clearing existing {out}", flush=True)
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    run_with_retries(
        [
            str(Path(sys.executable).parent / "fw-heudiconv-export"),
            "--project", project,
            "--subject", *sorted(fw_subjects),
            "--destination", str(out.parent),
            "--directory-name", out.name,
        ],
        f"export {sorted(fw_subjects)}",
        retries,
    )


def get_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="network_fmri curate")
    p.add_argument("--project", required=True, help="Flywheel project label")
    p.add_argument("--subject", required=True, help="one canonical subject, e.g. s03")
    p.add_argument("--heuristic", default=str(HEURISTIC), help=f"default: {HEURISTIC}")
    p.add_argument("--live", action="store_true",
                   help="write info.BIDS on Flywheel (default: dry run)")
    p.add_argument("--out", metavar="DIR",
                   help="export the tagged files here (requires --live); must be a "
                        "directory this run owns")
    p.add_argument("--retries", type=int, default=3,
                   help="retries for each curate and export invocation")
    return p


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    if args.out and not args.live:
        get_parser().error("--out requires --live (export reads what curate wrote)")

    import flywheel

    fw = flywheel.Client()
    project = fw.projects.find_first(f'label="{args.project}"')
    if project is None:
        raise SystemExit(f"Flywheel project {args.project!r} not found")

    records = sessions.collect(project.subjects(), args.subject)
    if not records:
        raise SystemExit(f"no Flywheel sessions found for subject {args.subject!r}")
    session_map = sessions.timeline(records)
    plan = sessions.jobs(records)

    # flush: engine logs to stderr unbuffered; buffered stdout would land last.
    print(f"[{args.subject}] sessions {session_map}", flush=True)
    for job in plan:
        forced = f" (forced -> {job['force_subject']})" if job["force_subject"] else ""
        print(f"[{args.subject}] curate {job['fw_subject']} {job['sessions']}{forced}", flush=True)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(session_map, f)
    try:
        for job in plan:
            env = dict(os.environ, **{sessions.SESSION_MAP_ENV: f.name})
            if job["force_subject"]:
                env[sessions.FORCE_SUBJECT_ENV] = job["force_subject"]
            cmd = [
                str(Path(sys.executable).parent / "fw-heudiconv-curate"),
                "--project", args.project,
                "--subject", job["fw_subject"],
                "--session", *job["sessions"],
                "--heuristic", args.heuristic,
            ]
            if not args.live:
                cmd.append("--dry-run")
            run_with_retries(cmd, f"curate {job['fw_subject']}", args.retries, env=env)
    finally:
        os.unlink(f.name)

    print(f"[{args.subject}] {'curated LIVE' if args.live else 'dry run, nothing written'}",
          flush=True)

    if args.out:
        # Includes reassignment sources: 22752 is tagged s10 but lives under s03.
        fw_subjects = {job["fw_subject"] for job in plan}
        export(args.project, fw_subjects, Path(args.out), retries=args.retries)
        print(f"[{args.subject}] exported {sorted(fw_subjects)} -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
