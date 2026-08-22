"""Assemble fMRIPrep output from the campaign's per-subject BABS zips into derivatives/.

The campaign produces one zip per subject (subject-level fMRIPrep); ``glm-lev1
--fmriprep-dir`` wants one unpacked derivatives tree. Unlike ``mriqc-iqms`` this is bulk
data, so everything is extracted, not just sidecars. Subject-level zips cannot collide,
so extraction is a plain unzip per subject into the same tree.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from network_fmri import provenance
from network_fmri.cohorts import COHORTS, DEFAULT_STAGING, cohort_dataset
from network_fmri.qa.mriqc import CAMPAIGN, SEVENZIP_BIN, sevenzip

PIPELINE = "fMRIPrep-25.2.5"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="network_fmri fmriprep-derivs-run")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--campaign", default=str(CAMPAIGN))
    p.add_argument("--out", required=True, help="derivative dir, e.g. derivatives/fmriprep")
    args = p.parse_args(argv)

    src = (Path(args.campaign) / "studies" / f"study-{args.cohort}"
           / "derivatives" / PIPELINE)
    zips = sorted(src.glob(f"sub-*_{PIPELINE}*.zip"))
    if not zips:
        raise SystemExit(f"no merged {PIPELINE} zips under {src} — merge the cell first")
    unfetched = [z for z in zips if not z.exists()]
    if unfetched:
        raise SystemExit(
            f"{len(unfetched)}/{len(zips)} zips have no content locally.\n"
            f"run: datalad get -d {src} {src}/'sub-*_{PIPELINE}*.zip'"
        )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    exe = sevenzip()
    for z in zips:
        # The zip's top folder is the pipeline name; extract its contents into out/.
        subprocess.run([exe, "x", "-y", f"-o{out}", str(z)],
                       check=True, capture_output=True)
        print(f"[fmriprep-derivs] {z.name}", flush=True)
    inner = out / PIPELINE
    if inner.is_dir():
        # Flatten <out>/fMRIPrep-25.2.5/* -> <out>/* so the tree is a plain
        # fMRIPrep layout (sub-*/, sourcedata/freesurfer, dataset_description.json).
        subprocess.run(["rsync", "-a", f"{inner}/", f"{out}/"], check=True)
        subprocess.run(["rm", "-rf", str(inner)], check=True)
    n = len(list(out.glob("sub-*")))
    print(f"[fmriprep-derivs] {n} subjects -> {out}", flush=True)
    return 0


def record(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="network_fmri fmriprep-derivs")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--campaign", default=str(CAMPAIGN))
    p.add_argument("--out", default="derivatives/fmriprep")
    args = p.parse_args(argv)

    tree = cohort_dataset(args.staging, args.cohort)
    cmd = [str(Path(sys.executable).parent / "network_fmri"), "fmriprep-derivs-run",
           "--cohort", args.cohort, "--campaign", args.campaign, "--out", args.out]
    env = provenance.datalad_env()
    env["PATH"] = f"{SEVENZIP_BIN}{os.pathsep}{env.get('PATH', '')}"
    provenance.run_recorded(
        tree, cmd,
        f"network_fmri@{provenance.code_version()}: assemble fMRIPrep derivatives "
        f"for {args.cohort}",
        outputs=[args.out], env=env,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
