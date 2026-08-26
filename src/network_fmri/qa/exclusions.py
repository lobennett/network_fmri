"""Compile network_qa's exclusion lockfiles.

Two compiles gate the models, each downstream of the step producing its evidence:

* ``qa-motion`` after ``mriqc-iqms`` — motion and behavioural exclusions, the lockfile
  ``glm-lev1 --exclusions-file`` consumes. Motion comes from MRIQC's IQMs, so this needs
  no fMRIPrep output and the exclusion set is known before preprocessing.
* ``qa-lev1`` after ``glm-outliers`` — adds lev1 outliers, gating what enters lev2.

Flags after ``--`` go to ``network-qa compile`` untouched, so what they mean stays that
package's business. Nothing here filters a pipeline's inputs: the full BIDS tree is
preprocessed, and exclusion happens at the point of use.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from network_fmri.cohorts import COHORTS, DEFAULT_STAGING, cohort_dataset

QA = str(Path(sys.executable).parent / "network-qa")
DATALAD = str(Path(sys.executable).parent / "datalad")

STAGES = {
    "motion": ("motion", "behavioral"),
    "lev1": ("motion", "behavioral", "lev1_outlier"),
}


def _run(stage: str, argv: list[str] | None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    own, extra = (raw[:raw.index("--")], raw[raw.index("--") + 1:]) if "--" in raw else (raw, [])

    p = argparse.ArgumentParser(prog=f"network_fmri qa-{stage}",
                                epilog="Flags after -- go to `network-qa compile`.")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--out", default=None, help="lockfile path; default <bids>/derivatives/qa/")
    p.add_argument("--mriqc-dir", default=None,
                   help="MRIQC IQMs; default <bids>/derivatives/mriqc (see mriqc-iqms)")
    if stage == "lev1":
        # The lev1_outlier generator refuses to run without this, so wire it here rather
        # than leaving it to a passthrough nobody remembers.
        p.add_argument("--lev1-dir", default=None,
                       help="lev1 results tree; the outliers csv is taken from "
                            "<lev1-dir>/cohort_qa (see glm-outliers)")
        p.add_argument("--lev1-outliers-csv", default=None,
                       help="explicit path, overriding --lev1-dir")
    p.add_argument("--partition", default="russpold,normal")
    p.add_argument("--cpus", type=int, default=2)
    p.add_argument("--mem-gb", type=int, default=16)
    p.add_argument("--time", default="02:00:00")
    p.add_argument("--dependency", default=None, help="Slurm job id to wait on")
    p.add_argument("--print", dest="print_only", action="store_true")
    args = p.parse_args(own)

    tree = cohort_dataset(args.staging, args.cohort)
    out = Path(args.out) if args.out else tree / "derivatives" / "qa" / f"{args.cohort}_{stage}_lock.json"
    log_dir = Path(args.staging) / "logs" / args.cohort

    mriqc = Path(args.mriqc_dir) if args.mriqc_dir else tree / "derivatives" / "mriqc"

    extra_args = ""
    if stage == "lev1":
        csv = args.lev1_outliers_csv or (
            f"{Path(args.lev1_dir) / 'cohort_qa' / 'lev1_outliers.csv'}" if args.lev1_dir else None)
        if not csv:
            raise SystemExit("qa-lev1 needs --lev1-dir (or --lev1-outliers-csv): the "
                             "lev1_outlier generator has no default location for it")
        if not Path(csv).is_file():
            raise SystemExit(f"no lev1 outliers csv at {csv} — run glm-outliers first")
        extra_args = f" --lev1-outliers-csv {csv}"
    # `datalad save` the lockfile: it is a tracked artefact the models consume, and an
    # untracked one leaves the cohort dataset dirty, which blocks every later
    # `datalad run` stage (fmriprep-derivs, mriqc-iqms).
    body = (f"set -euo pipefail\n{QA} compile --dataset {args.cohort} "
            f"--generators {' '.join(STAGES[stage])} --bids-dir {tree} --out {out} "
            f"--mriqc-dir {mriqc}{extra_args} {' '.join(extra)}\n"
            f"export PATH=\"$SCRATCH/git-annex/usr/bin:$PATH\"\n"
            f"{DATALAD} save -d {tree} -m "
            f"'network_qa exclusions lockfile: {args.cohort} {stage}' {out}")
    cmd = ["sbatch", "-J", f"nf-qa-{stage}-{args.cohort}", "-p", args.partition,
           "-c", str(args.cpus), f"--mem={args.mem_gb}G", "-t", args.time,
           "-o", f"{log_dir}/qa-{stage}-%j.out", "-e", f"{log_dir}/qa-{stage}-%j.err"]
    if args.dependency:
        cmd.append(f"--dependency=afterok:{args.dependency}")
    cmd += ["--wrap", body]

    if args.print_only:
        print(f"  {' '.join(cmd[:-1])}\n  --wrap:\n{body}")
        return 0

    # After the dry-run exit, so --print never touches the filesystem.
    log_dir.mkdir(parents=True, exist_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    job = res.stdout.strip().split()[-1]
    print(f"  qa-{stage} {job}  generators={','.join(STAGES[stage])} -> {out}")
    return 0


def motion(argv: list[str] | None = None) -> int:
    """Motion + behavioural exclusions, for the first level."""
    return _run("motion", argv)


def lev1(argv: list[str] | None = None) -> int:
    """Adds lev1 outliers, for the second level."""
    return _run("lev1", argv)
