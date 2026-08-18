"""``network_fmri`` — submit Flywheel -> BIDS curation to Slurm, or run it here.

    network_fmri submit fw-heudiconv --cohort discovery --live   # one task per subject
    network_fmri curate --project P --subject s03                # what a task runs
    network_fmri merge --cohort discovery                        # parts -> one BIDS tree
    network_fmri validate --cohort discovery                     # BIDS validator
    network_fmri datalad --cohort discovery                      # version it
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from network_fmri.cohorts import COHORTS, roster
from network_fmri.curate import HEURISTIC

TEMPLATE = Path(__file__).parent / "template.sbatch"
# Not bids_staging: that holds the previous pipeline's output, our baseline.
DEFAULT_STAGING = str(Path(os.environ.get("SCRATCH", Path.home())) / "network_fmri")
DEFAULT_PROJECT = "r01network"

_USAGE = """usage:
  network_fmri submit fw-heudiconv [options]   render + sbatch a per-subject array
  network_fmri curate [options]                run one subject here (what a task does)
  network_fmri merge --cohort C [options]      rsync per-subject parts into one tree
  network_fmri validate --cohort C [options]   run the BIDS validator on the merged tree
  network_fmri datalad --cohort C [options]    make the merged tree a DataLad dataset
"""


def get_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="network_fmri submit fw-heudiconv")
    p.add_argument("--project", default=DEFAULT_PROJECT, help="Flywheel project label")
    p.add_argument("--cohort", choices=list(COHORTS), help="submit this cohort's roster")
    p.add_argument("--subject", nargs="+", help="explicit subjects instead of --cohort")
    p.add_argument("--staging", default=DEFAULT_STAGING, help=f"default: {DEFAULT_STAGING}")
    p.add_argument("--heuristic", default=str(HEURISTIC), help=f"default: {HEURISTIC}")
    p.add_argument("--live", action="store_true",
                   help="tag Flywheel and export to <staging>/<cohort>/parts/<subject>")
    p.add_argument("--partition", default="normal")
    p.add_argument("--cpus", type=int, default=2)
    p.add_argument("--mem-gb", type=int, default=8)
    p.add_argument("--time", default="04:00:00")
    p.add_argument("--throttle", type=int, default=10,
                   help="max concurrent array tasks (the %%K in --array=0-N%%K)")
    p.add_argument("--template", default=str(TEMPLATE))
    p.add_argument("--print", dest="print_only", action="store_true",
                   help="print the rendered script instead of submitting")
    return p


def render(args: argparse.Namespace) -> str:
    subjects = args.subject or (roster(args.cohort) if args.cohort else None)
    if not subjects:
        get_parser().error("need --cohort or --subject")
    name = args.cohort or "adhoc"

    # Outside the BIDS tree: sbatch logs inside a dataset trip bids-validator.
    log_dir = Path(args.staging) / "logs" / name
    log_dir.mkdir(parents=True, exist_ok=True)
    subjects_file = log_dir / f"{name}_subjects.txt"
    subjects_file.write_text("\n".join(subjects) + "\n")

    return Path(args.template).read_text().format(
        job_name=f"nf-{name}",
        partition=args.partition,
        cpus=args.cpus,
        mem_gb=args.mem_gb,
        time=args.time,
        log_dir=log_dir,
        last=len(subjects) - 1,
        throttle=args.throttle,
        subjects_file=subjects_file,
        # Absolute path to this venv's console script: no PATH setup in the job.
        network_fmri=Path(sys.executable).parent / "network_fmri",
        project=args.project,
        heuristic=args.heuristic,
        parts=Path(args.staging) / name / "parts",
        live=" --live" if args.live else "",
        out=' --out "$OUT"' if args.live else "",
    )


def submit(argv: list[str]) -> int:
    args = get_parser().parse_args(argv)
    script = render(args)
    if args.print_only:
        print(script)
        return 0
    with tempfile.NamedTemporaryFile("w", suffix=".sbatch", delete=False) as f:
        f.write(script)
    print(f"sbatch script: {f.name}")
    return subprocess.run(["sbatch", f.name]).returncode


def merge(argv: list[str]) -> int:
    """rsync per-subject export dirs into one BIDS tree per cohort. Idempotent."""
    p = argparse.ArgumentParser(prog="network_fmri merge")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    args = p.parse_args(argv)

    parts = Path(args.staging) / args.cohort / "parts"
    dest = Path(args.staging) / args.cohort / "bids"
    sources = sorted(d for d in parts.glob("*") if d.is_dir())
    if not sources:
        raise SystemExit(f"no per-subject exports under {parts}")
    dest.mkdir(parents=True, exist_ok=True)
    for src in sources:
        rc = subprocess.run(["rsync", "-a", f"{src}/", f"{dest}/"]).returncode
        if rc != 0:
            raise SystemExit(f"rsync failed for {src} (rc={rc})")
    print(f"merged {len(sources)} subjects -> {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:2] == ["submit", "fw-heudiconv"]:
        return submit(argv[2:])
    if argv[:1] == ["curate"]:
        from network_fmri.curate import main as curate_main

        return curate_main(argv[1:])
    if argv[:1] == ["merge"]:
        return merge(argv[1:])
    if argv[:1] == ["validate"]:
        from network_fmri.validate import main as validate_main

        return validate_main(argv[1:])
    if argv[:1] == ["datalad"]:
        from network_fmri.dataset import main as datalad_main

        return datalad_main(argv[1:])
    sys.stderr.write(_USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
