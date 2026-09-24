#!/usr/bin/env bash
#SBATCH --job-name=network-mriqc-handoff
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --time=2-00:00:00
#SBATCH --output=network-mriqc-handoff-%j.log

# Submit from the pinned network_fmri checkout with DataLad/git-annex on PATH.
set -euo pipefail
exec uv run --frozen network-fmri processing run-mriqc "$@"
