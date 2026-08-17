"""Submit the ``prune`` stage: remove excluded scans, renumber survivors, save.

Single job: ``fw2bids prune <staging>/<cohort>`` followed by a ``datalad save``.
Runs AFTER ``datalad`` (so the as-acquired tree is committed first and every removal
stays recoverable from history) and BEFORE ``select`` (which re-renders scans.tsv over
the pruned tree). Needs the container for git-annex >= 10, same as datalad/select.
"""

from __future__ import annotations

import argparse
import sys

from network_fmri.submit import _common

STAGE = "prune"
DEFAULT_RESOURCES = {"nthreads": 2, "mem_gb": 8, "time": "00:30:00"}


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit the network_fmri prune SLURM job")
    _common.add_common_args(parser)
    parser.add_argument(
        "--source",
        action="append",
        default=None,
        help="exclusion source to prune (repeatable; default: short-run)",
    )
    parser.add_argument(
        "--anat-keep",
        action="append",
        default=None,
        metavar="SUB=SES",
        help="anat-QC-selected session for a subject, e.g. sub-s03=ses-13 (repeatable)",
    )
    parser.add_argument(
        "--anat-acquisition",
        default=None,
        help="anat acquisition label --anat-keep applies to (e.g. SagMPRAGE)",
    )
    return parser


def render(args: argparse.Namespace) -> str:
    ctx = _common.single_context(args, DEFAULT_RESOURCES, stage=STAGE)
    flags = [f"--source {s}" for s in (args.source or ["short-run"])]
    flags += [f"--anat-keep {kv}" for kv in (args.anat_keep or [])]
    if args.anat_acquisition:
        flags.append(f"--anat-acquisition {args.anat_acquisition}")
    ctx["prune_flags"] = " ".join(flags)
    return _common.render(STAGE, ctx)


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    return _common.finish(render(args), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
