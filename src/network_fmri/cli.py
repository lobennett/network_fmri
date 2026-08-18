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
from network_fmri.trim import N_DUMMY

TEMPLATE = Path(__file__).parent / "template.sbatch"
# Not bids_staging: that holds the previous pipeline's output, our baseline.
DEFAULT_STAGING = str(Path(os.environ.get("SCRATCH", Path.home())) / "network_fmri")
DEFAULT_PROJECT = "r01network"

_USAGE = """usage:
  network_fmri submit fw-heudiconv [options]   render + sbatch a per-subject array
  network_fmri curate [options]                run one subject here (what a task does)
  network_fmri import-subject [options]        curate+export one subject via datalad run
  network_fmri merge --cohort C [options]      rsync per-subject parts into one tree
  network_fmri validate --cohort C [options]   run the BIDS validator on the merged tree
  network_fmri global-signal --cohort C --label L   global-signal QA -> derivatives/
  network_fmri trim --cohort C [options]       trim dummy volumes in place (recorded)
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
    # 3 concurrent: Flywheel returns sporadic HTTP 500s at 8.
    p.add_argument("--throttle", type=int, default=3,
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
        cohort=name,
        staging=args.staging,
        live=" --live" if args.live else "",
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


def import_subject(argv: list[str]) -> int:
    """Curate + export one subject inside its own dataset, via ``datalad run``.

    A dataset per subject keeps 40+ array tasks from contending on one git index,
    while still recording the command and outputs in history.
    """
    from network_fmri import dataset

    p = argparse.ArgumentParser(prog="network_fmri import-subject")
    p.add_argument("--project", default=DEFAULT_PROJECT)
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--subject", required=True)
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--heuristic", default=str(HEURISTIC))
    p.add_argument("--live", action="store_true")
    p.add_argument("--retries", type=int, default=3)
    args = p.parse_args(argv)

    ds = Path(args.staging) / args.cohort / "parts" / args.subject
    env = dataset.datalad_env()
    dataset.ensure_dataset(ds, env)

    payload = [
        str(Path(sys.executable).parent / "network_fmri"), "curate",
        "--project", args.project, "--subject", args.subject,
        "--heuristic", args.heuristic, "--retries", str(args.retries),
    ]
    if args.live:
        # Relative to the dataset root, so the recorded command is portable.
        payload += ["--live", "--out", "bids"]

    dataset.run_recorded(
        ds, payload,
        f"network_fmri@{dataset.code_version()}: import {args.subject} "
        f"({'live' if args.live else 'dry run'})",
        outputs=["bids"] if args.live else [],
        env=env,
    )
    return 0


def _cohort_dataset(staging: str, cohort: str) -> Path:
    tree = Path(staging) / cohort / "bids"
    if not (tree / ".datalad").is_dir():
        raise SystemExit(f"{tree} is not a DataLad dataset (run `network_fmri merge` first)")
    return tree


def global_signal(argv: list[str]) -> int:
    """Record a global-signal QA pass into derivatives/global_signal/<label>."""
    from network_fmri import dataset

    p = argparse.ArgumentParser(prog="network_fmri global-signal")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--label", required=True, help="e.g. pre-trim, post-trim")
    p.add_argument("--tr-marker", type=int, default=None,
                   help="draw a marker at this volume (e.g. 7 to show where trim cuts)")
    args = p.parse_args(argv)

    tree = _cohort_dataset(args.staging, args.cohort)
    out = f"derivatives/global_signal/{args.label}"
    cmd = [
        str(Path(sys.executable).parent / "nf-global-signal"),
        "--bids-dir", ".",
        "--out-tsv", f"{out}/gs_metrics.tsv",
        "--out-pdf", f"{out}/gs_traces.pdf",
    ]
    if args.tr_marker is not None:
        cmd += ["--tr-marker", str(args.tr_marker)]

    env = dataset.datalad_env()
    (tree / out).mkdir(parents=True, exist_ok=True)
    dataset.run_recorded(
        tree, cmd,
        f"network_fmri@{dataset.code_version()}: global signal ({args.label}) {args.cohort}",
        outputs=[out], env=env,
    )
    return 0


def trim(argv: list[str]) -> int:
    """Record an in-place trim of the cohort's BOLD volumes.

    Outputs are not declared: `datalad run` unlocks declared outputs, which for
    annexed NIfTIs means copying ~100 GB out of the annex. Trimming replaces each
    file by rename instead, so the default save-everything behaviour is enough.
    """
    from network_fmri import dataset

    p = argparse.ArgumentParser(prog="network_fmri trim")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--jobs", type=int, default=4)
    args = p.parse_args(argv)

    tree = _cohort_dataset(args.staging, args.cohort)
    env = dataset.datalad_env()
    dataset.run_recorded(
        tree,
        [str(Path(sys.executable).parent / "network_fmri"), "trim-bold",
         "--bids-dir", ".", "--jobs", str(args.jobs)],
        f"network_fmri@{dataset.code_version()}: trim {N_DUMMY} dummy volumes "
        f"from {args.cohort}",
        outputs=[], env=env,
    )
    return 0


def merge(argv: list[str]) -> int:
    """rsync per-subject exports into one BIDS tree, recorded with ``datalad run``.

    The parts datasets are outside the cohort dataset, so their commits go in the
    run message rather than being pinned as ``--input``.
    """
    from network_fmri import dataset

    p = argparse.ArgumentParser(prog="network_fmri merge")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    args = p.parse_args(argv)

    parts = Path(args.staging) / args.cohort / "parts"
    dest = Path(args.staging) / args.cohort / "bids"
    sources = sorted(d for d in parts.glob("*/bids") if d.is_dir())
    if not sources:
        raise SystemExit(f"no per-subject exports under {parts}/*/bids")

    env = dataset.datalad_env()
    dataset.ensure_dataset(dest, env)
    provenance = " ".join(
        f"{s.parent.name}@{dataset.subject_commit(s.parent)}" for s in sources
    )
    # -L dereferences: the parts are datasets, so their NIfTIs are annex symlinks
    # into a .git/annex this dataset does not have. Without it we commit dangling links.
    script = "; ".join(f"rsync -aL {s}/ ." for s in sources)
    dataset.run_recorded(
        dest, ["bash", "-c", script],
        f"network_fmri@{dataset.code_version()}: merge {args.cohort} "
        f"({len(sources)} subjects) from {provenance}",
        outputs=["."],
        env=env,
    )
    print(f"merged {len(sources)} subjects -> {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:2] == ["submit", "fw-heudiconv"]:
        return submit(argv[2:])
    if argv[:1] == ["curate"]:
        from network_fmri.curate import main as curate_main

        return curate_main(argv[1:])
    if argv[:1] == ["import-subject"]:
        return import_subject(argv[1:])
    if argv[:1] == ["merge"]:
        return merge(argv[1:])
    if argv[:1] == ["global-signal"]:
        return global_signal(argv[1:])
    if argv[:1] == ["trim"]:
        return trim(argv[1:])
    if argv[:1] == ["trim-bold"]:
        from network_fmri.trim import main as trim_main

        return trim_main(argv[1:])
    if argv[:1] == ["validate"]:
        from network_fmri.validate import main as validate_main

        return validate_main(argv[1:])
    sys.stderr.write(_USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
