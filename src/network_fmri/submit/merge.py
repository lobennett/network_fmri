"""Submit the ``merge`` stage: move the per-subject export parts into one tree.

Single job: ``mv <parts>/<cohort>/*/sub-*/ <staging>/<cohort>/`` collects the
independently-exported per-subject parts into the cohort's staged BIDS tree.
Both sides live on the same ($SCRATCH/Lustre) filesystem, so each subject dir
is a near-instant rename rather than a ~825 GB byte-copy; top-level BIDS files
(identical across parts) are rsync'd once. See merge.sbatch.tmpl for detail.
"""

from __future__ import annotations

import argparse
import sys

from network_fmri.submit import _common

STAGE = "merge"
# 08:00:00 kept as a safety ceiling: the same-filesystem mv is near-instant, but
# an idempotent re-run (--start-stage merge) may fall back to rsync-reconcile a
# large cohort's per-subject parts (validation = 41 subjects x multi-echo BOLD).
DEFAULT_RESOURCES = {"nthreads": 2, "mem_gb": 8, "time": "08:00:00"}


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit the network_fmri merge SLURM job")
    _common.add_common_args(parser)
    return parser


def render(args: argparse.Namespace) -> str:
    ctx = _common.single_context(args, DEFAULT_RESOURCES, stage=STAGE)
    return _common.render(STAGE, ctx)


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    return _common.finish(render(args), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
