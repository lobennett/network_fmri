#!/usr/bin/env bash
#SBATCH --job-name=network-processing-handoff
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=2-00:00:00
#SBATCH --propagate=NONE
#SBATCH --output=network-processing-handoff-%j.log

# Run from the pinned checkout with DataLad/git-annex available on PATH.
set -euo pipefail
exec uv run --frozen network-fmri processing run "$@"
