"""``network_fmri`` — submit Flywheel -> BIDS curation to Slurm, or run it here.

    network_fmri submit fw-heudiconv --cohort discovery --live   # one task per subject
    network_fmri curate --project P --subject s03                # what a task runs
    network_fmri merge --cohort discovery                        # parts -> one BIDS tree
    network_fmri validate --cohort discovery                     # BIDS validator
    network_fmri datalad --cohort discovery                      # version it
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from network_fmri.cohorts import COHORTS, DEFAULT_STAGING, cohort_dataset
from network_fmri.trim import N_DUMMY

_USAGE = """usage:
  network_fmri submit fw-heudiconv [options]   render + sbatch a per-subject array
  network_fmri curate [options]                run one subject here (what a task does)
  network_fmri import-subject [options]        curate+export one subject via datalad run
  network_fmri merge --cohort C [options]      rsync per-subject parts into one tree
  network_fmri behavior-clean --cohort C       materialise the cleaned 1:1 behavioral tree
  network_fmri validate --cohort C [options]   run the BIDS validator on the merged tree
  network_fmri global-signal --cohort C --label L   global-signal QA -> derivatives/
  network_fmri trim --cohort C [options]       trim dummy volumes in place (recorded)
  network_fmri fix-sidecars --cohort C         coerce sidecar fields to BIDS types
"""


def global_signal(argv: list[str]) -> int:
    """Record a global-signal QA pass into derivatives/global_signal/<label>."""
    from network_fmri import provenance

    p = argparse.ArgumentParser(prog="network_fmri global-signal")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--label", required=True, help="e.g. pre-trim, post-trim")
    p.add_argument("--tr-marker", type=int, default=None,
                   help="draw a marker at this volume (e.g. 7 to show where trim cuts)")
    args = p.parse_args(argv)

    tree = cohort_dataset(args.staging, args.cohort)
    out = f"derivatives/global_signal/{args.label}"
    cmd = [
        str(Path(sys.executable).parent / "nf-global-signal"),
        "--bids-dir", ".",
        "--out-tsv", f"{out}/gs_metrics.tsv",
        "--out-pdf", f"{out}/gs_traces.pdf",
    ]
    if args.tr_marker is not None:
        cmd += ["--tr-marker", str(args.tr_marker)]

    env = provenance.datalad_env()
    (tree / out).mkdir(parents=True, exist_ok=True)
    provenance.run_recorded(
        tree, cmd,
        f"network_fmri@{provenance.code_version()}: global signal ({args.label}) {args.cohort}",
        outputs=[out], env=env,
    )
    return 0


def trim(argv: list[str]) -> int:
    """Record an in-place trim of the cohort's BOLD volumes.

    Outputs are not declared: `datalad run` unlocks declared outputs, which for
    annexed NIfTIs means copying ~100 GB out of the annex. Trimming replaces each
    file by rename instead, so the default save-everything behaviour is enough.
    """
    from network_fmri import provenance

    p = argparse.ArgumentParser(prog="network_fmri trim")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--jobs", type=int, default=4)
    args = p.parse_args(argv)

    tree = cohort_dataset(args.staging, args.cohort)
    env = provenance.datalad_env()
    provenance.run_recorded(
        tree,
        [str(Path(sys.executable).parent / "network_fmri"), "trim-bold",
         "--bids-dir", ".", "--jobs", str(args.jobs)],
        f"network_fmri@{provenance.code_version()}: trim {N_DUMMY} dummy volumes "
        f"from {args.cohort}",
        outputs=[], env=env,
    )
    return 0


def fix_sidecars(argv: list[str]) -> int:
    """Record a sidecar type-coercion pass over the cohort dataset."""
    from network_fmri import provenance

    p = argparse.ArgumentParser(prog="network_fmri fix-sidecars")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    args = p.parse_args(argv)

    tree = cohort_dataset(args.staging, args.cohort)
    env = provenance.datalad_env()
    provenance.run_recorded(
        tree,
        [str(Path(sys.executable).parent / "network_fmri"), "fix-sidecars-run",
         "--bids-dir", "."],
        f"network_fmri@{provenance.code_version()}: coerce sidecar string fields "
        f"in {args.cohort}",
        outputs=[], env=env,
    )
    return 0


def behavior_clean(argv: list[str]) -> int:
    """Record the cleaned behavioral tree into the cohort dataset."""
    from network_fmri import provenance

    p = argparse.ArgumentParser(prog="network_fmri behavior-clean")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--out", default="sourcedata")
    args = p.parse_args(argv)

    tree = cohort_dataset(args.staging, args.cohort)
    env = provenance.datalad_env()
    provenance.run_recorded(
        tree,
        [str(Path(sys.executable).parent / "network_fmri"), "behavior-clean-run",
         "--cohort", args.cohort, "--bids-dir", ".", "--out", args.out],
        f"network_fmri@{provenance.code_version()}: clean behavioral -> {args.out} "
        f"({args.cohort})",
        outputs=[args.out], env=env,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:2] == ["submit", "fw-heudiconv"]:
        from network_fmri.fw2bids.jobs import submit

        return submit(argv[2:])
    if argv[:1] == ["curate"]:
        from network_fmri.fw2bids.curate import main as curate_main

        return curate_main(argv[1:])
    if argv[:1] == ["import-subject"]:
        from network_fmri.fw2bids.jobs import import_subject

        return import_subject(argv[1:])
    if argv[:1] == ["merge"]:
        from network_fmri.fw2bids.jobs import merge

        return merge(argv[1:])
    if argv[:1] == ["global-signal"]:
        return global_signal(argv[1:])
    if argv[:1] == ["trim"]:
        return trim(argv[1:])
    if argv[:1] == ["trim-bold"]:
        from network_fmri.trim import main as trim_main

        return trim_main(argv[1:])
    if argv[:1] == ["behavior-clean-run"]:
        from network_fmri.behavior import clean

        return clean(argv[1:])
    if argv[:1] == ["behavior-clean"]:
        return behavior_clean(argv[1:])
    if argv[:1] == ["validate"]:
        from network_fmri.validate import main as validate_main

        return validate_main(argv[1:])
    if argv[:1] == ["fix-sidecars"]:
        return fix_sidecars(argv[1:])
    if argv[:1] == ["fix-sidecars-run"]:
        from network_fmri.sidecars import main as sidecar_main

        return sidecar_main(argv[1:])
    sys.stderr.write(_USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
