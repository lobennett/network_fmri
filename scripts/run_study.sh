#!/usr/bin/env bash
#SBATCH --job-name=network-study
#SBATCH --partition=russpold,normal
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=7-00:00:00
#SBATCH --propagate=NONE

# Submit after the final raw-preparation job. Restart after each manual review.
set -euo pipefail
umask 007
config=${1:?Usage: run_study.sh workflow.toml [dashboard-index]}
publication=("$config" --group oak_russpold)
if [[ $# -gt 1 ]]; then publication+=(--index "$2"); fi
network-fmri study init "$config"
unset UV_PROJECT_ENVIRONMENT
network-fmri publish "${publication[@]}"
network-fmri processing run "$config" --poll-seconds 300
network-fmri publish "${publication[@]}"
