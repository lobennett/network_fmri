"""Assemble MRIQC IQMs from the campaign's BABS result zips into derivatives/mriqc/.

BABS stores one zip per session, so the IQM JSONs are not readable as a BIDS derivative
until they are unpacked. `network_qa`'s motion generator wants a conventional MRIQC
derivatives tree, so this produces one: only the IQM JSONs, not the multi-gigabyte SVG
reports.

Assembly rather than computation, and orchestration rather than QA -- hence here and not in
network_qa, which stays free of the campaign layout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from network_fmri import provenance
from network_fmri.cohorts import COHORTS, DEFAULT_STAGING, cohort_dataset

CAMPAIGN = Path("/scratch/users/logben/mechababs_campaigns/r01network")
PIPELINE = "MRIQC-24.0.2"
# p7zip is not on the default PATH here; BABS's own zip step adds the same bindir.
SEVENZIP_BIN = Path("/share/software/user/open/p7zip/16.02/bin")
IQM = re.compile(r".*_(bold|T1w|T2w)\.json$")


def sevenzip() -> str:
    exe = shutil.which("7z") or shutil.which("7z", path=str(SEVENZIP_BIN))
    if exe is None:
        raise SystemExit(f"7z not found on PATH or in {SEVENZIP_BIN}")
    return exe


def derivative_dir(campaign: Path, cohort: str) -> Path:
    return campaign / "studies" / f"study-{cohort}" / "derivatives" / PIPELINE


def members(exe: str, zip_path: Path) -> list[str]:
    out = subprocess.run([exe, "l", "-ba", "-slt", str(zip_path)],
                         capture_output=True, text=True, check=True)
    return [ln[7:] for ln in out.stdout.splitlines() if ln.startswith("Path = ")]


def fd_thres(iqm: dict) -> float | None:
    """The `--fd_thres` MRIQC ran with; `fd_perc` is meaningless without it."""
    try:
        return float(iqm["provenance"]["settings"]["fd_thres"])
    except (KeyError, TypeError, ValueError):
        return None


def extract(exe: str, zips: list[Path], out_dir: Path) -> dict:
    """Copy every IQM JSON out of the zips, keeping their BIDS-relative layout."""
    n, thresholds = 0, set()
    for z in zips:
        for m in members(exe, z):
            if not IQM.match(m):
                continue
            # Inside the zip: <pipeline>/sub-X/ses-Y/<datatype>/<file>. Keep from sub- on.
            parts = Path(m).parts
            try:
                rel = Path(*parts[next(i for i, p in enumerate(parts)
                                       if p.startswith("sub-")):])
            except StopIteration:
                continue
            blob = subprocess.run([exe, "x", "-so", str(z), m],
                                  capture_output=True, check=True).stdout
            try:
                iqm = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if rel.name.endswith("_bold.json"):
                t = fd_thres(iqm)
                if t is not None:
                    thresholds.add(t)
            dest = out_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
            n += 1
    return {"files": n, "fd_thres": sorted(thresholds)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="network_fmri mriqc-iqms-run")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--campaign", default=str(CAMPAIGN))
    p.add_argument("--out", required=True, help="derivative dir to write, e.g. derivatives/mriqc")
    args = p.parse_args(argv)

    src = derivative_dir(Path(args.campaign), args.cohort)
    zips = sorted(src.glob("sub-*_mriqc-*.zip"))
    if not zips:
        raise SystemExit(f"no merged MRIQC result zips under {src} — merge the cell first")
    unfetched = [z for z in zips if not z.exists()]     # dangling annex symlinks
    if unfetched:
        raise SystemExit(
            f"{len(unfetched)}/{len(zips)} result zips have no content locally.\n"
            f"run: datalad get -d {src} {src}/'sub-*_mriqc-*.zip'"
        )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "dataset_description.json").write_text(json.dumps({
        "Name": f"MRIQC IQMs ({args.cohort})",
        "BIDSVersion": "1.9.0",
        "DatasetType": "derivative",
        "GeneratedBy": [{"Name": "MRIQC", "Version": PIPELINE.split("-")[-1]}],
    }, indent=2) + "\n")

    stats = extract(sevenzip(), zips, out)
    print(f"[mriqc-iqms] {stats['files']} IQM files from {len(zips)} sessions -> {out}",
          flush=True)
    # Surface it here as well as in network_qa: a mixed set means some sessions were
    # processed with a different spike threshold, so fd_perc is not comparable.
    print(f"[mriqc-iqms] fd_thres recorded in the IQMs: {stats['fd_thres']}", flush=True)
    if len(stats["fd_thres"]) > 1:
        raise SystemExit("IQMs disagree on fd_thres — re-run MRIQC so every session matches")
    return 0


def record(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="network_fmri mriqc-iqms")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--campaign", default=str(CAMPAIGN))
    p.add_argument("--out", default="derivatives/mriqc")
    args = p.parse_args(argv)

    tree = cohort_dataset(args.staging, args.cohort)
    cmd = [str(Path(sys.executable).parent / "network_fmri"), "mriqc-iqms-run",
           "--cohort", args.cohort, "--campaign", args.campaign, "--out", args.out]
    env = provenance.datalad_env()
    env["PATH"] = f"{SEVENZIP_BIN}{os.pathsep}{env.get('PATH', '')}"
    provenance.run_recorded(
        tree, cmd,
        f"network_fmri@{provenance.code_version()}: assemble MRIQC IQMs for {args.cohort}",
        outputs=[args.out], env=env,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
