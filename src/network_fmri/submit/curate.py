"""Submit the ``curate`` stage: per-subject Flywheel BIDS-tagging (``--array``).

One array task per roster subject runs ``fw2bids <cohort> --subject X --live``,
which WRITES the BIDS naming into that subject's ``info.BIDS`` on Flywheel.
"""

from __future__ import annotations

import argparse
import sys

from network_fmri.submit import _common

STAGE = "curate"
DEFAULT_RESOURCES = {"nthreads": 2, "mem_gb": 8, "time": "02:00:00"}


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit the network_fmri curate SLURM array job")
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
