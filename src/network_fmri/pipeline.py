"""Submit the whole Flywheel -> BIDS chain for one cohort as dependent Slurm jobs.

One command launches everything and returns immediately: Slurm is the DAG engine, so each
stage carries ``--dependency=afterok`` on the one before it and a failure anywhere stops
the rest rather than corrupting the tree. Nothing here blocks or polls.

``--from`` restarts at a stage, which is how you resume after fixing a failure. ``--print``
shows the plan without submitting.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from network_fmri.cohorts import COHORTS, DEFAULT_STAGING, roster

NF = str(Path(sys.executable).parent / "network_fmri")
# Same venv: network_events is a pinned dependency, so its console script sits beside ours.
NE = str(Path(sys.executable).parent / "network-events")


def stages(cohort: str, staging: str, events_bin: str) -> list[dict]:
    """The chain, in order. ``cmd`` is None for the array, which submits itself."""
    nf = [NF]
    return [
        # Stage 1 is the per-subject array; `pipeline` submits it through sbatch_array.
        dict(name="export", cpus=2, mem="8G", time="08:00:00", cmd=None),
        dict(name="merge", cpus=2, mem="8G", time="12:00:00",
             cmd=nf + ["merge", "--cohort", cohort, "--staging", staging]),
        dict(name="fix-sidecars", cpus=2, mem="8G", time="02:00:00",
             cmd=nf + ["fix-sidecars", "--cohort", cohort, "--staging", staging]),
        dict(name="validate-pre", cpus=2, mem="8G", time="04:00:00",
             cmd=nf + ["validate", "--cohort", cohort, "--staging", staging,
                       "--", "--ignoreWarnings"]),
        dict(name="gs-pre", cpus=4, mem="16G", time="12:00:00",
             cmd=nf + ["global-signal", "--cohort", cohort, "--staging", staging,
                       "--label", "pre-trim", "--tr-marker", "7"]),
        # 16 cores: trim is per-file parallel. Wider than one node is impossible — array
        # tasks would contend on the dataset's git index.
        dict(name="trim", cpus=16, mem="32G", time="08:00:00",
             cmd=nf + ["trim", "--cohort", cohort, "--staging", staging, "--jobs", "16"]),
        dict(name="b0link", cpus=2, mem="8G", time="02:00:00",
             cmd=nf + ["b0link", "--cohort", cohort, "--staging", staging]),
        dict(name="gs-post", cpus=4, mem="16G", time="12:00:00",
             cmd=nf + ["global-signal", "--cohort", cohort, "--staging", staging,
                       "--label", "post-trim"]),
        dict(name="ingest-beh", cpus=2, mem="8G", time="02:00:00",
             cmd=nf + ["ingest-beh", "--cohort", cohort, "--staging", staging]),
        # network_events owns three steps: events, the truncation QC that writes
        # trim_list.json, and the behaviour-driven truncation that consumes it. `run`
        # would also re-migrate out-of-scanner data, which the canonical tree covers.
        dict(name="events", cpus=4, mem="16G", time="08:00:00",
             cmd=[events_bin, "create", "--sourcedata", "sourcedata", "--bids-dir", "."],
             cwd=True),
        dict(name="events-qc", cpus=2, mem="8G", time="04:00:00",
             cmd=[events_bin, "qc", "--sourcedata", "sourcedata", "--bids-dir", "."],
             cwd=True),
        dict(name="events-trim", cpus=4, mem="16G", time="06:00:00",
             cmd=[events_bin, "trim", "--bids-dir", "."], cwd=True),
        dict(name="validate-post", cpus=2, mem="8G", time="04:00:00",
             cmd=nf + ["validate", "--cohort", cohort, "--staging", staging,
                       "--", "--ignoreWarnings"]),
    ]


def get_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="network_fmri pipeline")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--live", action="store_true",
                   help="tag Flywheel and export for real (without it, export is a dry run)")
    p.add_argument("--partition", default="russpold,normal")
    p.add_argument("--throttle", type=int, default=3,
                   help="concurrent export tasks; Flywheel returns HTTP 500s above ~8")
    p.add_argument("--project", default="r01network")
    p.add_argument("--from", dest="start", default="export",
                   help="resume at this stage instead of the beginning")
    p.add_argument("--print", dest="print_only", action="store_true",
                   help="print the plan instead of submitting")
    return p


def main(argv: list[str] | None = None) -> int:
    from network_fmri.fw2bids.jobs import get_parser as array_parser
    from network_fmri.fw2bids.jobs import sbatch_array

    args = get_parser().parse_args(argv)
    chain = stages(args.cohort, args.staging, NE)
    names = [s["name"] for s in chain]
    if args.start not in names:
        raise SystemExit(f"--from must be one of: {' '.join(names)}")
    chain = chain[names.index(args.start):]

    tree = Path(args.staging) / args.cohort / "bids"
    log_dir = Path(args.staging) / "logs" / args.cohort
    log_dir.mkdir(parents=True, exist_ok=True)

    if args.print_only:
        print(f"cohort {args.cohort} ({len(roster(args.cohort))} subjects), "
              f"{len(chain)} stages, staging {args.staging}")
        for s in chain:
            what = f"array x{len(roster(args.cohort))}" if s["cmd"] is None else " ".join(s["cmd"])
            print(f"  {s['name']:14s} c{s['cpus']:<3} {s['mem']:>5} {s['time']}  {what}")
        return 0

    dep = None
    for s in chain:
        if s["cmd"] is None:
            a = array_parser().parse_args(
                ["--cohort", args.cohort, "--staging", args.staging, "--project", args.project,
                 "--partition", args.partition, "--throttle", str(args.throttle),
                 *(["--live"] if args.live else [])]
            )
            job = sbatch_array(a)
        else:
            cmd = ["sbatch", "-J", f"nf-{s['name']}-{args.cohort}", "-p", args.partition,
                   "-c", str(s["cpus"]), f"--mem={s['mem']}", "-t", s["time"],
                   "-o", f"{log_dir}/{s['name']}-%j.out", "-e", f"{log_dir}/{s['name']}-%j.err"]
            if dep:
                cmd.append(f"--dependency=afterok:{dep}")
            # The events stages take paths relative to the dataset, so run them there.
            wrap = " ".join(s["cmd"])
            if s.get("cwd"):
                wrap = f"cd {tree} && {wrap}"
            cmd += ["--wrap", wrap]
            out = subprocess.run(cmd, capture_output=True, text=True, check=True)
            job = out.stdout.strip().split()[-1]
        print(f"  {s['name']:14s} {job}" + (f"  after {dep}" if dep else ""))
        dep = job

    print(f"\n{len(chain)} stages queued for {args.cohort}. Watch: squeue --me | grep nf-")
    return 0


if __name__ == "__main__":
    sys.exit(main())
