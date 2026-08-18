#!/bin/bash
# Rendered with str.format: double literal braces, and use $VAR / $((...)).
#SBATCH -J {job_name}
#SBATCH --array=0-{last}%{throttle}
#SBATCH -p {partition}
#SBATCH --cpus-per-task={cpus}
#SBATCH --mem={mem_gb}G
#SBATCH --time={time}
#SBATCH -o {log_dir}/%x-%A-%a.out
#SBATCH -e {log_dir}/%x-%A-%a.err

set -euo pipefail

SUBJ="$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" {subjects_file})"

# Each task owns its output dir: fw-heudiconv-export rmtree's its root on a file
# conflict, so a shared destination would let one task destroy another's work.
OUT="{parts}/$SUBJ"

{network_fmri} curate --project {project} --heuristic {heuristic} \
  --subject "$SUBJ"{live}{out}
