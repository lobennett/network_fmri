#!/usr/bin/env bash
#SBATCH --job-name=network-seed-repair
#SBATCH --time=24:00:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --propagate=NONE

# One reviewed acquisition per task, with all echoes and at most three attempts.
# sbatch --array=0-N%4 --partition=<verified> --output=<stage>/job-%A_%a.log \
#   scripts/run_fmriprep_seed_rerun.sh <plan.json> <stage> [--preflight-only]
set -euo pipefail
: "${SLURM_JOB_ID:?Submit through Slurm}"
: "${L_SCRATCH_JOB:?Node-local storage is required}"
: "${OAK:?}"
: "${NETWORK_FMRI_CODE:?Set a validated frozen code snapshot}"
: "${UV_PROJECT_ENVIRONMENT:?Set the validated pinned project environment}"
: "${FMRIPREP_IMAGE:?}"
: "${FMRIPREP_IMAGE_SHA256:?}"
: "${FREESURFER_SOURCE:?Set the original reviewed FreeSurfer derivative}"
: "${FREESURFER_LICENSE:?}"
: "${TEMPLATEFLOW_HOME:?}"
case "${3:-}" in
  ''|--preflight-only) ;;
  *) echo 'Only --preflight-only is accepted as a third argument' >&2; exit 2 ;;
esac
ml system git
export UV_CACHE_DIR="$SCRATCH/.uv" UV_OFFLINE=true UV_PYTHON_DOWNLOADS=never
export PYTHONPATH="$NETWORK_FMRI_CODE/src" PYTHONPYCACHEPREFIX="$L_SCRATCH_JOB/pycache"
plan=$(realpath "$1")
stage=$(realpath "$2")
code=$(realpath "$NETWORK_FMRI_CODE")
test -f "$code/src/network_fmri/fmriprep_seed_workflow.py"
python="$UV_PROJECT_ENVIRONMENT/bin/python"
run=$("$python" -c 'import json,sys; p=json.load(open(sys.argv[1])); print(p["runs"][int(sys.argv[2])]["before"]["run"])' "$plan" "${SLURM_ARRAY_TASK_ID:-0}")
args=(--plan "$plan" --stage "$stage" --run "$run" --code "$code"
      --surface-root "$FREESURFER_SOURCE" --license-file "$FREESURFER_LICENSE"
      --image "$FMRIPREP_IMAGE" --image-sha256 "$FMRIPREP_IMAGE_SHA256"
      --templateflow "$TEMPLATEFLOW_HOME")
if [ "${3:-}" = --preflight-only ]; then args+=(--preflight-only); fi
( cd "$code" && uv run --frozen --offline --no-sync python -m network_fmri.fmriprep_seed_rerun run "${args[@]}" )
