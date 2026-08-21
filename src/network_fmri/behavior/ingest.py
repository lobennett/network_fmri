"""Copy the canonical behavioural tree into a cohort's ``sourcedata/``.

Raw behavioural filenames encode no run index, so pairing each CSV to a BOLD run took
session alignment and volume counts. That answer only changes if the functional side does,
so it is frozen at :data:`CANONICAL` — a DataLad dataset with its own derivation record.

This stage only copies: it re-derives nothing, reads no NIfTI, and never touches the raw
tree, which is being archived.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from network_fmri import provenance
from network_fmri.cohorts import COHORTS, DEFAULT_STAGING, cohort_dataset, roster

CANONICAL = Path(
    "/oak/stanford/groups/russpold/data/network_grant/behavioral_data/canonical"
)

# The canonical dataset's commit at the time these trees were built. A re-run against a
# different commit silently ingests different behaviour, which changes events and so every
# downstream exclusion -- so the mismatch is refused rather than reported.
CANONICAL_COMMIT = "445eba8"


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


def source_commit(source: Path) -> str:
    """The canonical dataset's HEAD, or "unknown" if it is not a git checkout."""
    try:
        out = subprocess.run(["git", "-C", str(source), "rev-parse", "--short=7", "HEAD"],
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def check_commit(source: Path, expected: str) -> str:
    """Refuse to ingest a different version of the behavioural data than we recorded."""
    got = source_commit(source)
    if expected and got != "unknown" and not got.startswith(expected):
        raise SystemExit(
            f"{source} is at {got}, expected {expected}.\n"
            "Behaviour changed underneath the pipeline: re-running would produce different "
            "events and so different exclusions. Check out the expected commit, or update "
            "ingest.CANONICAL_COMMIT deliberately and rebuild the events."
        )
    return got


def record(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="network_fmri ingest-beh")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--source", default=str(CANONICAL))
    p.add_argument("--expect-commit", default=CANONICAL_COMMIT,
                   help="required source commit; empty string disables the check")
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
    commit = check_commit(source, args.expect_commit)
    check_content(source, subjects)

    (tree / args.out).mkdir(parents=True, exist_ok=True)
    # -L dereferences: the source is a dataset, so its CSVs are annex symlinks into a
    # .git/annex this dataset does not have.
    cmd = ["rsync", "-aL", *(f"{source}/{s}" for s in subjects), f"{args.out}/"]
    provenance.run_recorded(
        tree, cmd,
        f"network_fmri@{provenance.code_version()}: ingest behavioural data for "
        f"{args.cohort} ({len(subjects)} subjects) from {source}@{commit}",
        outputs=[args.out], env=provenance.datalad_env(),
    )
    print(f"ingested {len(subjects)} subjects -> {tree / args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(record())
