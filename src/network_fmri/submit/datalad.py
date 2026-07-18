"""Submit the ``datalad`` stage: DataLad-ify the staged BIDS tree.

Single job: ``fw2bids datalad <staging>/<cohort>``. The template ``module load
system git-annex`` first, since git-annex is not on the login node.
"""

from __future__ import annotations

import argparse
import sys

from network_fmri.submit import _common

STAGE = "datalad"
DEFAULT_RESOURCES = {"nthreads": 8, "mem_gb": 24, "time": "04:00:00"}


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit the network_fmri datalad SLURM job")
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
