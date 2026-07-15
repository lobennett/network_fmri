"""Submit the ``trim`` stage: per-subject dummy-volume trimming (``--array``).

One array task per subject runs ``fw2bids trim <staging>/<cohort> --subjects X
--jobs N`` in the staged tree. Idempotent + atomic; disjoint subjects per task
means no write races.
"""

from __future__ import annotations

import argparse
import sys

from network_fmri.submit import _common

STAGE = "trim"
DEFAULT_RESOURCES = {"nthreads": 8, "mem_gb": 24, "time": "04:00:00"}


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit the network_fmri trim SLURM array job")
    _common.add_common_args(parser, array=True)
    return parser


def render(args: argparse.Namespace) -> str:
    ctx, _ = _common.array_context(args, DEFAULT_RESOURCES, stage=STAGE)
    return _common.render(STAGE, ctx)


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    return _common.finish(render(args), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
