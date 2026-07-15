"""Submit the ``merge`` stage: rsync the per-subject export parts into one tree.

Single job: ``rsync -a <parts>/<cohort>/*/ <staging>/<cohort>/`` collects the
independently-exported per-subject parts into the cohort's staged BIDS tree.
"""

from __future__ import annotations

import argparse
import sys

from network_fmri.submit import _common

STAGE = "merge"
DEFAULT_RESOURCES = {"nthreads": 2, "mem_gb": 8, "time": "01:00:00"}


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
