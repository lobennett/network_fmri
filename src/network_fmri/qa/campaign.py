"""Run the mechababs campaign's own CLI, with its environment set up correctly.

The campaign vendors a pinned mechababs+babs inside itself and refuses to run under any
other install (the wrong-babs guard), so this deliberately does NOT import or depend on
mechababs -- it submits a job that sources the campaign's own venv. What it encodes is the
setup that otherwise fails three different ways: the venv must be *sourced*, git-annex must
be on PATH, and it must run on a compute node.

    network_fmri campaign -- iterate --dry-run
    network_fmri campaign -- iterate --batch 2
    network_fmri campaign -- status
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from network_fmri.cohorts import DEFAULT_STAGING
from network_fmri.qa.mriqc import CAMPAIGN

GIT_ANNEX = "$SCRATCH/git-annex/usr/bin"


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    own, extra = (raw[:raw.index("--")], raw[raw.index("--") + 1:]) if "--" in raw else (raw, [])
    p = argparse.ArgumentParser(prog="network_fmri campaign",
                                epilog="Everything after -- goes to `mechababs`.")
    p.add_argument("--campaign", default=str(CAMPAIGN))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--partition", default="russpold,normal")
    p.add_argument("--time", default="04:00:00")
    p.add_argument("--print", dest="print_only", action="store_true")
    args = p.parse_args(own)
    if not extra:
        raise SystemExit("nothing to run -- pass mechababs args after `--`, e.g. -- iterate --dry-run")

    body = (f"set -uo pipefail\ncd {args.campaign}\nsource .venv/bin/activate\n"
            f'export PATH="{GIT_ANNEX}:$PATH"\nmechababs {" ".join(extra)}')
    # Logs live OUTSIDE the campaign: anything untracked inside it leaves the dataset
    # dirty, and mechababs refuses to iterate on a dirty campaign.
    log = Path(args.staging) / "logs" / "campaign"
    cmd = ["sbatch", "-J", "nf-campaign", "-p", args.partition, "-c", "4", "--mem=16G",
           "-t", args.time, "-o", f"{log}/campaign-%j.out", "-e", f"{log}/campaign-%j.err",
           "--wrap", body]
    if args.print_only:
        print(f"  {' '.join(cmd[:-1])}\n  --wrap:\n{body}")
        return 0
    log.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    job = out.stdout.strip().split()[-1]
    print(f"  campaign {job}  mechababs {' '.join(extra)}  logs: {log}/campaign-{job}.*")
    return 0
