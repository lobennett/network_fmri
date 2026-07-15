"""Submit the ``events`` stage: the behavioral half over the staged cohort.

Single job: ``network-events run --behavioral-dir <oak raw> --bids-dir
<staging>/<cohort> --manifest <reconciliation_<cohort>.tsv>``. That orchestrator
migrates behavioral CSVs (per the reviewed manifest), then creates event TSVs,
runs behavioral QC, and trims events.

Skipped for the ``excluded`` cohort — it has no reconciliation manifest.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from network_fmri.submit import _common

STAGE = "events"
DEFAULT_RESOURCES = {"nthreads": 4, "mem_gb": 16, "time": "04:00:00"}

# Read-only raw behavioral data on OAK (the reconcile/migrate source). Matches
# the absolute ``raw_path`` base in the reviewed reconciliation manifests.
DEFAULT_BEHAVIORAL_DIR = (
    "/oak/stanford/groups/russpold/data/network_grant/"
    "_archive_someone_plz_clean/behavioral_data/raw_cleaned"
)

# Fallback network_events checkout (used until network_events is installed in the
# orchestrator env / its manifests are packaged into the wheel).
_NETWORK_EVENTS_CHECKOUT = "/scratch/users/logben/network_events"


def default_manifest(cohort: str) -> str:
    """Path to the vendored ``reconciliation_<cohort>.tsv``.

    Resolved from the installed ``network_events`` package layout
    (``<repo>/config/manifests/``); falls back to a package-relative literal if
    ``network_events`` isn't importable (so ``--dry-run`` still renders).
    """
    rel = Path("config") / "manifests" / f"reconciliation_{cohort}.tsv"
    try:
        import network_events

        base = Path(network_events.__file__).resolve().parents[2]
        cand = base / rel
        if cand.is_file():
            return str(cand)
    except Exception:
        pass
    return str(Path(_NETWORK_EVENTS_CHECKOUT) / rel)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit the network_fmri events SLURM job")
    _common.add_common_args(parser)
    parser.add_argument(
        "--behavioral-dir",
        default=DEFAULT_BEHAVIORAL_DIR,
        help=f"raw behavioral dir (default OAK: {DEFAULT_BEHAVIORAL_DIR})",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="reconciliation manifest TSV (default: vendored network_events manifest)",
    )
    return parser


def render(args: argparse.Namespace) -> str:
    if args.cohort not in _common.EVENTS_COHORTS:
        raise SystemExit(
            f"events stage is not defined for cohort {args.cohort!r} "
            f"(no reconciliation manifest); valid: {_common.EVENTS_COHORTS}"
        )
    ctx = _common.single_context(args, DEFAULT_RESOURCES, stage=STAGE)
    ctx["behavioral_dir"] = args.behavioral_dir
    ctx["manifest"] = args.manifest or default_manifest(args.cohort)
    return _common.render(STAGE, ctx)


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    return _common.finish(render(args), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
