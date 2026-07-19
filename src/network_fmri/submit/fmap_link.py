"""Submit the ``fmap_link`` stage: stamp B0Field* metadata on the staged tree.

Single job: ``fw2bids fmap-link <staging>/<cohort>``. Lightweight (edits small
JSON sidecars); runs for all cohorts. Must precede ``datalad`` so the linkage is
committed into the tracked tree.
"""

from __future__ import annotations

import argparse
import sys

from network_fmri.submit import _common

STAGE = "fmap_link"
DEFAULT_RESOURCES = {"nthreads": 2, "mem_gb": 8, "time": "00:20:00"}


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit the network_fmri fmap_link SLURM job")
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
