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
# NOTE: this tree was promoted out of ``_archive_someone_plz_clean`` in the 2026-08
# Oak cleanup, which then removed that directory. Both this default and the
# manifests' raw_path must name the promoted location or a run finds no behavioural
# data at all (network_events now fails loudly rather than skipping).
DEFAULT_BEHAVIORAL_DIR = (
    "/oak/stanford/groups/russpold/data/network_grant/"
    "behavioral_data/raw_cleaned"
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

        # Manifests ship inside the package: src/network_events/config/manifests/.
        cand = Path(network_events.__file__).resolve().parent / rel
        if cand.is_file():
            return str(cand)
    except Exception:
        pass
    # Fallback for dry-run before network_events is installed in this env.
    return str(Path(_NETWORK_EVENTS_CHECKOUT) / "src" / "network_events" / rel)


def container_manifest(cohort: str) -> str:
    """A shell expression that locates the manifest INSIDE the container.

    The manifest is resolved by whichever interpreter renders the sbatch, but it is
    read by the one inside the image. Rendering on the host therefore baked in the
    host env's copy — possibly an older network_events pin than the image runs, which
    is how a stale manifest reached a job whose preview looked correct. Emitting a
    command substitution defers resolution to the container, so the path is found
    where it is used.
    """
    return (
        '"$(python -c "import network_events, os; '
        'print(os.path.join(os.path.dirname(network_events.__file__), \'config\', '
        f'\'manifests\', \'reconciliation_{cohort}.tsv\'))")"'
    )


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
    if args.manifest:
        ctx["manifest"] = args.manifest
    elif getattr(args, "container", None):
        ctx["manifest"] = container_manifest(args.cohort)
    else:
        ctx["manifest"] = default_manifest(args.cohort)
    return _common.render(STAGE, ctx)


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    return _common.finish(render(args), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
