"""Coerce exported sidecars to the types BIDS requires.

A multi-valued DICOM tag arrives as a JSON list. BIDS defines the fields below as
strings, so the validator rejects them with ``JSON_SCHEMA_VALIDATION_ERROR ... must be
string``. Joining the values is lossless and idempotent, so this can run on every pull —
unlike a one-time edit on Flywheel, it also catches newly uploaded scans from the same
scanner software.

``ImageType`` is deliberately absent: BIDS defines it as an array, and a tree full of
list-valued ``ImageType`` validates clean.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from network_fmri.prepare.sidecar import read, update

# BIDS type: string. All three come from multi-valued GE DICOM tags.
STRING_FIELDS = ("SoftwareVersions", "ScanningSequence", "ScanOptions")
SEP = "/"


def fix_one(path: Path) -> bool:
    """Join list-valued string fields in one sidecar. True if it changed."""
    data = read(path)
    fields = {f: SEP.join(str(v) for v in data[f])
              for f in STRING_FIELDS if isinstance(data.get(f), list)}
    return update(path, **fields) if fields else False


def fix_tree(bids_dir: Path) -> dict:
    paths = sorted(p for p in bids_dir.glob("sub-*/ses-*/*/*.json")
                   if "derivatives" not in p.parts)
    fixed = sum(fix_one(p) for p in paths)
    return {"scanned": len(paths), "fixed": fixed}


def get_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="network_fmri fix-sidecars-run")
    p.add_argument("--bids-dir", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = get_parser().parse_args(argv)
    summary = fix_tree(Path(args.bids_dir))
    print(f"[fix-sidecars] {summary}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
