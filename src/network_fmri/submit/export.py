"""Submit the ``export`` stage: per-subject BIDS download (``--array``).

One array task per roster subject runs ``fw2bids export <cohort> --subject X
--out <parts>/<cohort>/X`` — a pure read (no Flywheel writes), each to its own
parts dir, so it fans out cleanly.
"""

from __future__ import annotations

import argparse
import sys

from network_fmri.submit import _common

STAGE = "export"
DEFAULT_RESOURCES = {"nthreads": 4, "mem_gb": 16, "time": "04:00:00"}


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit the network_fmri export SLURM array job")
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
