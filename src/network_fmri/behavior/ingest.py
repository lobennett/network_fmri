"""Copy the canonical behavioural tree into a cohort's ``sourcedata/``.

Deciding which BOLD run each raw behavioural CSV belongs to is a one-time reconciliation:
the raw filenames encode no run index, so it has to be worked out from session alignment
and volume counts. That answer does not change unless the functional side does, so it is
frozen at :data:`CANONICAL` — a DataLad dataset carrying its own derivation record and the
code that produced it.

This stage only copies. Nothing here re-derives anything, reads a NIfTI, or depends on the
raw tree, which is being archived.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from network_fmri import provenance
from network_fmri.cohorts import COHORTS, DEFAULT_STAGING, cohort_dataset, roster

CANONICAL = Path(
    "/oak/stanford/groups/russpold/data/network_grant/behavioral_data/canonical"
)


def subject_dirs(source: Path, cohort: str) -> list[str]:
    """The cohort's subject directories present in the canonical tree."""
    return [f"sub-{s}" for s in roster(cohort) if (source / f"sub-{s}").is_dir()]


def check_content(source: Path, subjects: list[str]) -> None:
    """Fail early if the canonical dataset's annexed content is not present.

    Its CSVs are annex symlinks; rsync would otherwise copy dangling links and the cohort
    dataset would look populated while holding nothing.
    """
    for sub in subjects:
        sample = next((source / sub).rglob("*_beh.csv"), None)
        if sample is None:
            raise SystemExit(f"{source / sub} contains no behavioural CSV")
        if not sample.exists():                      # resolves the symlink
            raise SystemExit(
                f"content not available: {sample}\n"
                f"run: datalad -C {source} get {' '.join(subjects)}"
            )


def record(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="network_fmri ingest-beh")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--source", default=str(CANONICAL))
    p.add_argument("--out", default="sourcedata")
    args = p.parse_args(argv)

    tree = cohort_dataset(args.staging, args.cohort)
    source = Path(args.source)
    subjects = subject_dirs(source, args.cohort)
    if not subjects:
        # Not an error: the excluded cohort has no behavioural data in any raw session, so a
        # scripted chain should carry on rather than stop here.
        print(f"no canonical behavioural data for {args.cohort} under {source} — nothing to do",
              flush=True)
        return 0
    check_content(source, subjects)

    (tree / args.out).mkdir(parents=True, exist_ok=True)
    # -L dereferences: the source is a dataset, so its CSVs are annex symlinks into a
    # .git/annex this dataset does not have.
    cmd = ["rsync", "-aL", *(f"{source}/{s}" for s in subjects), f"{args.out}/"]
    provenance.run_recorded(
        tree, cmd,
        f"network_fmri@{provenance.code_version()}: ingest behavioural data for "
        f"{args.cohort} ({len(subjects)} subjects) from {source}",
        outputs=[args.out], env=provenance.datalad_env(),
    )
    print(f"ingested {len(subjects)} subjects -> {tree / args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(record())
