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

# One dataset per subject: avoids 40+ tasks contending on a single git index, and
# export writes into <dataset>/bids so its rmtree cannot touch .git.
{network_fmri} import-subject --project {project} --cohort {cohort} \
  --staging {staging} --heuristic {heuristic} --subject "$SUBJ"{live}
