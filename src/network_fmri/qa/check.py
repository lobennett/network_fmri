"""Assert the invariants the BIDS validator cannot see.

`validate` proves the tree is well-formed, not that it is right -- every defect this dataset
has produced passed validation silently. One check per class, each asserting a stage's
intended outcome so a rerun fails loudly instead of handing on a plausible tree:

``events``  onsets inside the acquired scan, so lev1 cannot regress on volumes that
            were never imaged
``anat``    at most one T1w and one T2w per subject, i.e. the ``_qa-reject`` marks took
``trim``    every BOLD stamped ``NumberOfVolumesDiscardedByUser``, matching the shift
``b0link``  field maps and their BOLDs cross-referenced, or SDCFlows pairs nothing

See docs/SCAN-NOTES.md for which scans each of these is about.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import nibabel as nib

from network_fmri.cohorts import COHORTS, DEFAULT_STAGING, cohort_dataset

# Whole sessions with no field map are expected, not a linkage failure.
SUFFIX = re.compile(r"_(T1w|T2w)\.nii\.gz$")


def _sidecar(nii: Path) -> dict:
    js = Path(str(nii).replace(".nii.gz", ".json"))
    try:
        return json.loads(js.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _acquisitions(func: Path) -> dict[str, Path]:
    """One entry per acquisition, echoes collapsed."""
    return {re.sub(r"_echo-\d+", "", n.name): n
            for n in sorted(func.glob("*_bold.nii.gz"))}


def check_events(tree: Path) -> list[str]:
    bad = []
    for func in sorted(tree.glob("sub-*/ses-*/func")):
        for stem, nii in _acquisitions(func).items():
            ev = func / stem.replace("_bold.nii.gz", "_events.tsv")
            if not ev.exists():
                continue
            img = nib.load(nii)
            scan = img.shape[3] * float(img.header.get_zooms()[3])
            onsets = [float(line.split("\t")[0])
                      for line in ev.read_text().splitlines()[1:] if line.strip()]
            over = [o for o in onsets if o >= scan]
            if over:
                bad.append(f"{ev.relative_to(tree)}: {len(over)} onsets at/after the "
                           f"{scan:.1f}s end of the scan (max {max(over):.1f}s)")
    return bad


def check_anat(tree: Path) -> list[str]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for nii in tree.glob("sub-*/ses-*/anat/*.nii.gz"):
        m = SUFFIX.search(nii.name)
        if m:
            counts[nii.parent.parent.parent.name][m.group(1)] += 1
    bad = []
    for sub in sorted(counts):
        for suffix in ("T1w", "T2w"):
            n = counts[sub][suffix]
            if n > 1:
                bad.append(f"{sub}: {n} {suffix} across all sessions, expected at most 1")
        if not counts[sub]["T1w"]:
            bad.append(f"{sub}: no T1w in any session")
    return bad


def check_trim(tree: Path) -> list[str]:
    return [f"{nii.relative_to(tree)}: no NumberOfVolumesDiscardedByUser"
            for func in sorted(tree.glob("sub-*/ses-*/func"))
            for nii in sorted(func.glob("*_bold.nii.gz"))
            if "NumberOfVolumesDiscardedByUser" not in _sidecar(nii)]


def check_b0link(tree: Path) -> list[str]:
    bad = []
    for ses in sorted(tree.glob("sub-*/ses-*")):
        fmaps = sorted((ses / "fmap").glob("*.nii.gz")) if (ses / "fmap").is_dir() else []
        bolds = sorted((ses / "func").glob("*_bold.nii.gz")) if (ses / "func").is_dir() else []
        # A field map in a session with no BOLD has nothing to identify itself for --
        # b0link marks those `orphan_fmap` and skips them by design.
        if not fmaps or not bolds:
            continue
        for nii in fmaps:
            if not _sidecar(nii).get("B0FieldIdentifier"):
                bad.append(f"{nii.relative_to(tree)}: no B0FieldIdentifier")
        for nii in bolds:
            if not _sidecar(nii).get("B0FieldSource"):
                bad.append(f"{nii.relative_to(tree)}: no B0FieldSource, "
                           f"but {ses.name} has a field map")
    return bad


CHECKS = {"events": check_events, "anat": check_anat,
          "trim": check_trim, "b0link": check_b0link}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="network_fmri check",
                                description=__doc__.split("\n\n")[0])
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--only", nargs="+", choices=list(CHECKS),
                   help="run a subset (default: all)")
    args = p.parse_args(argv)

    tree = cohort_dataset(args.staging, args.cohort)
    failed = 0
    for name in (args.only or CHECKS):
        bad = CHECKS[name](tree)
        print(f"[check] {name}: {'FAIL' if bad else 'ok'} ({len(bad)} problem(s))",
              flush=True)
        for line in bad:
            print(f"    {line}", flush=True)
        failed += len(bad)

    print(f"\n[{args.cohort}] {failed} problem(s)", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
