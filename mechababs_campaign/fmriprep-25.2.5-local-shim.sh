#!/bin/bash
# fmriprep-25.2.5-local-shim.sh — register OUR local fmriprep_25.2.5.sif as a
# container DataLad dataset at the path vanilla BABS expects.
#
# Same rationale as mriqc-24.0.2-local-shim.sh (see it for the full story): BABS
# hardcodes the image location to <container-ds>/.datalad/environments/<name>/image
# (babs#383, open), and we want our exact pinned sif rather than whatever version a
# ReproNim fetch currently registers — the version has to match the pipeline yaml's
# `zip_foldernames: 25-2-5`.
#
# Build ONCE, OUTSIDE any campaign, as a SIBLING of the campaign dir: the pipeline
# yamls reference it relatively as `../fmriprep-25.2.5-shim`, which mechababs
# resolves against the campaign root.
#
# Requires git-annex >=10 on PATH (see README §git-annex-10) and datalad.
set -euo pipefail

SIF="${SIF:-/home/groups/russpold/singularity_images/fmriprep_25.2.5.sif}"
SHIM="${SHIM:-$(pwd)/fmriprep-25.2.5-shim}"

[ -f "$SIF" ] || { echo "FATAL: sif not found: $SIF" >&2; exit 1; }

if [ ! -e "$SHIM/.datalad" ]; then
  datalad create -c text2git "$SHIM"
fi

datalad containers-add -d "$SHIM" bids-fmriprep --update \
  -i ".datalad/environments/bids-fmriprep/image" \
  --url "$SIF" \
  --call-fmt 'apptainer exec --cleanenv {img} {cmd}'

echo "shim ready at: $SHIM"
echo "image: $(readlink -f "$SHIM/.datalad/environments/bids-fmriprep/image" 2>/dev/null || echo '<annexed>')"
