#!/usr/bin/env bash
#SBATCH --job-name=network-bold-weights
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#SBATCH --propagate=NONE

# One subject per array task, using a previously validated frozen checkout.
# Usage: sbatch --array=0-45%4 --output=/staging/audit-%A_%a.log \
#   scripts/run_bold_reference_audit.sh /staging/inventory.json /staging/results [--check-only]
set -euo pipefail
: "${SLURM_JOB_ID:?Submit this script with Slurm}"
: "${SLURM_ARRAY_TASK_ID:?Select an inventory subject using a Slurm array}"
: "${L_SCRATCH_JOB:?Node-local temporary storage is required}"
: "${OAK:?}"
: "${TEMPLATEFLOW_HOME:?Set the existing template cache}"
: "${FMRIPREP_IMAGE:?Set the campaign image}"
: "${FMRIPREP_IMAGE_SHA256:?Set the committed image checksum}"
: "${NETWORK_FMRI_CODE:?Set the absolute validated source checkout path}"
manifest=$(realpath "$1")
output=$(realpath "$2")
# Slurm copies this script into its spool, so $0 cannot locate the checkout.
code=$(realpath "$NETWORK_FMRI_CODE")
test -f "$code/src/network_fmri/bold_reference_qc.py"
case "${3:-}" in
  ''|--check-only) ;;
  *) echo 'Only --check-only is accepted as a third argument' >&2; exit 2 ;;
esac
printf '%s  %s\n' "$FMRIPREP_IMAGE_SHA256" "$FMRIPREP_IMAGE" | sha256sum --check
args=(--cleanenv --containall -B "$OAK:$OAK:ro" -B "$code:/code:ro"
      -B "$manifest:/manifest.json:ro" -B "$output:/out" -B "$L_SCRATCH_JOB:/work"
      -B "$TEMPLATEFLOW_HOME:/templateflow:ro"
      --env "PYTHONPATH=/code/src,TEMPLATEFLOW_HOME=/templateflow,SLURM_JOB_ID=$SLURM_JOB_ID,OMP_NUM_THREADS=2,OPENBLAS_NUM_THREADS=1")
subject=$(apptainer exec "${args[@]}" "$FMRIPREP_IMAGE" python -c \
  'import json,sys; from pathlib import Path; import network_fmri.bold_reference_qc as qc; import fmriprep; assert Path(qc.__file__).resolve() == Path("/code/src/network_fmri/bold_reference_qc.py"); assert fmriprep.__version__ == qc.VERSION; print(json.load(open("/manifest.json"))["subjects"][int(sys.argv[1])])' "$SLURM_ARRAY_TASK_ID")
if [ "${3:-}" = --check-only ]; then
  printf 'AUDIT_PREFLIGHT_PASSED code=%s subject=%s\n' "$code" "$subject"
  exit 0
fi
apptainer exec "${args[@]}" "$FMRIPREP_IMAGE" python -m network_fmri.bold_reference_qc audit \
  --manifest /manifest.json --output "/out/$subject" --work-root /work/audit --subject "$subject"
