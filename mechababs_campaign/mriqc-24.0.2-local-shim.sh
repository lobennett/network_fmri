#!/bin/bash
# mriqc-24.0.2-local-shim.sh — register OUR local mriqc_24.0.2.sif as a container
# DataLad dataset at the path vanilla BABS expects.
#
# Why a shim: BABS hardcodes the image location to
#   <container-ds>/.datalad/environments/<name>/image
# and does not yet understand ReproNim/containers' native layout (babs#383, open).
# Upstream mechababs only ships tmp-repronim-container-shim.sh, which CLONES
# ReproNim/containers and registers whatever MRIQC version ReproNim currently
# ships. We want our exact, version-pinned 24.0.2 sif (no ~5 GB re-download and no
# version drift vs the pipeline yaml's zip_foldernames: 24-0-2). This does the same
# `datalad containers-add` the ReproNim shim uses for its own locally-built images,
# but points --url at our local file.
#
# Build ONCE, OUTSIDE any campaign, as a SIBLING of the campaign dir — the pipeline
# yaml references it relatively as `../mriqc-24.0.2-shim`, and mechababs resolves a
# relative local container source against the campaign root.
#
# Requires git-annex >=10 on PATH (see README §git-annex-10) and datalad.
set -euo pipefail

SIF="${SIF:-/home/groups/russpold/singularity_images/mriqc_24.0.2.sif}"
# Default: sibling of the current dir named mriqc-24.0.2-shim. Override with SHIM=.
SHIM="${SHIM:-$(pwd)/mriqc-24.0.2-shim}"

[ -f "$SIF" ] || { echo "FATAL: sif not found: $SIF" >&2; exit 1; }

if [ ! -e "$SHIM/.datalad" ]; then
  datalad create -c text2git "$SHIM"
fi

datalad containers-add -d "$SHIM" bids-mriqc --update \
  -i ".datalad/environments/bids-mriqc/image" \
  --url "$SIF" \
  --call-fmt 'apptainer exec --cleanenv {img} {cmd}'

echo "shim ready at: $SHIM"
echo "image: $(readlink -f "$SHIM/.datalad/environments/bids-mriqc/image" 2>/dev/null || echo '<annexed>')"
