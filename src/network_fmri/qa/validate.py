"""``network_fmri validate`` — official BIDS validator on a merged tree.

Runs the upstream ``bids/validator`` container, so no host tooling is needed. Checks
NIfTI headers and sidecar field types, which a schema-only checker cannot.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from network_fmri.qa import container

VALIDATOR_URI = "docker://bids/validator:3.0.1"


def get_parser() -> argparse.ArgumentParser:
    from network_fmri.cohorts import COHORTS, DEFAULT_STAGING

    p = argparse.ArgumentParser(prog="network_fmri validate")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--uri", default=VALIDATOR_URI, help=f"default: {VALIDATOR_URI}")
    p.add_argument("--image", default=None,
                   help="path to an existing .sif, skipping the pull")
    p.add_argument("--cache", default=None,
                   help=f"where pulled images live (default: {container.cache_dir()})")
    # After --, args pass to the validator (--ignoreWarnings, --format json).
    p.add_argument("validator_args", nargs=argparse.REMAINDER,
                   help="extra bids-validator args (prefix with --)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    tree = Path(args.staging) / args.cohort / "bids"
    if not tree.is_dir():
        raise SystemExit(f"no merged tree at {tree} (run `network_fmri merge` first)")

    extra = [a for a in args.validator_args if a != "--"]
    sif = container.resolve(args.uri, args.image, args.cache)
    rc = container.run(sif, [str(tree), *extra])
    print(f"[{args.cohort}] validator rc={rc}", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
