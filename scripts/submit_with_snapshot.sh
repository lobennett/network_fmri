#!/bin/bash
# Freeze BABS' subject list before submitting an array; later retries overwrite it.
set -euo pipefail
args=("$@")
last=$((${#args[@]} - 1))
selection="${args[$last]}"
test "$(basename "$selection")" = job_submit.csv
project=$(dirname "$(dirname "$selection")")
mkdir -p "$project/.babs/submissions"
snapshot=$(mktemp "$project/.babs/submissions/subjects.XXXXXXXX")
cp "$selection" "$snapshot"
chmod 440 "$snapshot"
args[$last]="$snapshot"
exec sbatch "${args[@]}"
