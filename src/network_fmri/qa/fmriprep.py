"""Assemble fMRIPrep output from the campaign's per-subject BABS zips into derivatives/.

The campaign produces one zip per subject (subject-level fMRIPrep); ``glm-lev1
--fmriprep-dir`` wants one unpacked derivatives tree. Unlike ``mriqc-iqms`` this is bulk
data, so everything is extracted, not just sidecars. Subject-level zips cannot collide,
so extraction is a plain unzip per subject into the same tree.

Fetching a zip makes a second copy of it -- the campaign's output RIA already holds one --
so after a successful unpack the fetched copies are evicted (``--keep-zips`` to opt out).
That is ~200 GB per subject, and the unpacked tree is a third copy, so without this the
cohort costs 3x what it needs to. ``datalad drop`` refuses to remove a last copy, so the
RIA remains the archive and the zips can be re-fetched.
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
    p.add_argument("--keep-zips", action="store_true",
                   help="keep the fetched zips instead of dropping them after unpacking")
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
    # Directories only: the tree also holds one sub-*_*.html report per session.
    n = len([p for p in out.glob("sub-*") if p.is_dir()])
    print(f"[fmriprep-derivs] {n} subjects -> {out}", flush=True)

    if not args.keep_zips:
        # Only after the unpack succeeded, so a failure never costs the fetch. The drop
        # is in the campaign dataset, not this one -- a cache eviction, not an output.
        #
        # The zips live in a RIA, so verifying the remaining copy needs
        # git-annex-remote-ora; without it on PATH git-annex reports the confusing
        # "external special remote protocol error ... <EOF>". It ships in this venv.
        bindir = str(Path(sys.executable).parent)
        env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}
        r = subprocess.run([f"{bindir}/datalad", "drop", "-d", str(src),
                            *[str(z) for z in zips]],
                           capture_output=True, text=True, env=env)
        if r.returncode:
            print(f"[fmriprep-derivs] zips left in place ({src}): {r.stderr.strip()}",
                  flush=True)
        else:
            print(f"[fmriprep-derivs] dropped {len(zips)} fetched zips", flush=True)
    return 0


def record(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="network_fmri fmriprep-derivs")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--campaign", default=str(CAMPAIGN))
    p.add_argument("--out", default="derivatives/fmriprep")
    p.add_argument("--keep-zips", action="store_true",
                   help="keep the fetched zips instead of dropping them after unpacking")
    args = p.parse_args(argv)

    tree = cohort_dataset(args.staging, args.cohort)
    cmd = [str(Path(sys.executable).parent / "network_fmri"), "fmriprep-derivs-run",
           "--cohort", args.cohort, "--campaign", args.campaign, "--out", args.out]
    if args.keep_zips:
        cmd.append("--keep-zips")
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
