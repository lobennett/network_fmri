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
from network_fmri.prepare.trim import N_DUMMY

_USAGE = """usage:
  network_fmri submit fw-heudiconv [options]   render + sbatch a per-subject array
  network_fmri curate [options]                run one subject here (what a task does)
  network_fmri import-subject [options]        curate+export one subject via datalad run
  network_fmri merge --cohort C [options]      rsync per-subject parts into one tree
  network_fmri behavior-clean --cohort C       materialise the cleaned 1:1 behavioral tree
  network_fmri validate --cohort C [options]   run the BIDS validator on the merged tree
  network_fmri global-signal --cohort C --label L   global-signal QA -> derivatives/
  network_fmri trim --cohort C [options]       trim dummy volumes in place (recorded)
  network_fmri b0link --cohort C               link field maps to their BOLD runs
  network_fmri fix-sidecars --cohort C         coerce sidecar fields to BIDS types
"""


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
        from network_fmri.qa.globalsignal import record

        return record(argv[1:])
    if argv[:1] == ["trim"]:
        return trim(argv[1:])
    if argv[:1] == ["trim-bold"]:
        from network_fmri.prepare.trim import main as trim_main

        return trim_main(argv[1:])
    if argv[:1] == ["b0link"]:
        from network_fmri.prepare.b0link import record

        return record(argv[1:])
    if argv[:1] == ["b0link-run"]:
        from network_fmri.prepare.b0link import main as b0link_main

        return b0link_main(argv[1:])
    if argv[:1] == ["behavior-clean"]:
        from network_fmri.behavior.clean import record

        return record(argv[1:])
    if argv[:1] == ["behavior-clean-run"]:
        from network_fmri.behavior.clean import clean

        return clean(argv[1:])
    if argv[:1] == ["validate"]:
        from network_fmri.qa.validate import main as validate_main

        return validate_main(argv[1:])
    if argv[:1] == ["fix-sidecars"]:
        return fix_sidecars(argv[1:])
    if argv[:1] == ["fix-sidecars-run"]:
        from network_fmri.prepare.sidecars import main as sidecar_main

        return sidecar_main(argv[1:])
    sys.stderr.write(_USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
