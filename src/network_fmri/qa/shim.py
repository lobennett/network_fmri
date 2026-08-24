"""Build a container shim dataset for a mechababs pipeline, and vendor it into the campaign.

Every pipeline in the campaign needs a shim: a tiny DataLad dataset whose only content is
the container image, registered under the name the pipeline's yaml expects. babs clones the
shim rather than taking a path to a `.sif`, so without one `babs init` dies with
"Failed to clone from any candidate source URL".

The shim is a build step, not an artefact -- it is not in git, and rebuilding it is how a
fresh checkout gets one. Two steps, both here because each fails on its own:

1. Build the standalone shim at `--dest`. This is what the pipeline yaml's
   `container.source` points at.
2. Vendor it into `<campaign>/code/<name>` with `datalad clone -d`, so the campaign tracks
   it as a subdataset, then fetch the image content into the clone. A plain `datalad clone`
   leaves it untracked and the next `iterate` refuses to run on the dirty campaign.

    network_fmri shim --name bids-xcpd \\
        --image /oak/stanford/groups/russpold/shared/containers/xcp_d-26.0.2.sif \\
        --dest $SCRATCH/mechababs_campaigns/xcpd-26.0.2-shim

Runs `datalad containers-add` out of the campaign's own venv, the only one holding
datalad-container.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from network_fmri.cohorts import DEFAULT_STAGING
from network_fmri.qa.campaign import GIT_ANNEX
from network_fmri.qa.mriqc import CAMPAIGN

# MD5E because babs addresses images by key; largefiles keeps the image annexed but the
# dataset's own git files in git. Matches the mriqc and fmriprep shims.
GITATTRIBUTES = (
    "* annex.backend=MD5E\n"
    "**/.git* annex.largefiles=nothing\n"
    "* annex.largefiles=((mimeencoding=binary)and(largerthan=0))\n"
)
CALL_FMT = "apptainer exec --cleanenv {img} {cmd}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="network_fmri shim")
    p.add_argument("--name", required=True,
                   help="container name the pipeline yaml expects, e.g. bids-xcpd")
    p.add_argument("--image", required=True, help="path to the .sif")
    p.add_argument("--dest", required=True,
                   help="standalone shim path; must match the yaml's container.source")
    p.add_argument("--campaign", default=str(CAMPAIGN))
    p.add_argument("--vendor", default=None,
                   help="path under <campaign>/code to vendor into "
                        "(default: code/<basename of --dest>)")
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--partition", default="russpold,normal")
    p.add_argument("--time", default="02:00:00")
    p.add_argument("--print", dest="print_only", action="store_true")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    image = Path(args.image)
    if not args.print_only and not image.is_file():
        raise SystemExit(f"no container image at {image}")
    dest = Path(args.dest)
    vendor = Path(args.vendor) if args.vendor else Path(args.campaign) / "code" / dest.name

    # `datalad create` refuses a non-empty target but tolerates an empty directory, which
    # is exactly the state a half-finished earlier attempt leaves behind.
    body = f"""set -euo pipefail
cd {args.campaign}
source .venv/bin/activate
export PATH="{GIT_ANNEX}:$PATH"

echo "== 1/4 dataset at {dest}"
if [ -e {dest}/.datalad/config ]; then
    echo "   exists, reusing"
else
    mkdir -p {dest}
    datalad create -f {dest}
    printf '%b' {GITATTRIBUTES!r} > {dest}/.gitattributes
    datalad save -d {dest} -m 'annex config for a container shim' .gitattributes
fi

# Check the registration, not the directory. A shim can hold the committed image and still
# have no `datalad.containers.<name>` config -- babs then clones it happily and fails later
# with "container not found", which is a much more confusing error than a missing clone.
echo "== 2/4 register {args.name}"
if git -C {dest} config -f .datalad/config --get datalad.containers.{args.name}.image >/dev/null 2>&1; then
    echo "   already registered"
elif [ -e {dest}/.datalad/environments/{args.name}/image ]; then
    echo "   image present but unregistered -- registering in place"
    datalad containers-add {args.name} -d {dest} \\
        --image .datalad/environments/{args.name}/image --call-fmt {CALL_FMT!r}
else
    datalad containers-add {args.name} -d {dest} --url {image} --call-fmt {CALL_FMT!r}
fi
git -C {dest} config -f .datalad/config --get datalad.containers.{args.name}.image

echo "== 3/4 vendor into {vendor}"
want=$(git -C {dest} rev-parse HEAD)
have=$(git -C {vendor} rev-parse HEAD 2>/dev/null || echo none)
if [ "$want" = "$have" ]; then
    echo "   already vendored at $want"
else
    # A clone pinned to an older shim commit predates the registration above, so replace it.
    echo "   vendored at $have, want $want -- recloning"
    chmod -R u+w {vendor} 2>/dev/null || true
    rm -rf {vendor}
    datalad clone -d {args.campaign} {dest} {vendor}
fi

echo "== 4/4 fetch image content into the clone"
datalad get -d {vendor} {vendor}/.datalad/environments/{args.name}/image
echo "== campaign status (must be clean, or the next iterate refuses)"
datalad status -d {args.campaign}
echo "shim ready: {vendor}"
"""
    log = Path(args.staging) / "logs" / "campaign"
    cmd = ["sbatch", "-J", f"nf-shim-{args.name}", "-p", args.partition, "-c", "4",
           "--mem=16G", "-t", args.time, "-o", f"{log}/shim-%j.out",
           "-e", f"{log}/shim-%j.err", "--wrap", body]
    if args.print_only:
        print(f"  {' '.join(cmd[:-1])}\n  --wrap:\n{body}")
        return 0
    log.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    job = out.stdout.strip().split()[-1]
    print(f"  shim {job}  {args.name} -> {vendor}  logs: {log}/shim-{job}.*")
    return 0
