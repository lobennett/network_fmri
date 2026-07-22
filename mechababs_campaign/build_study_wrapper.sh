#!/bin/bash
# Build a local OpenNeuroStudies-shaped "study" wrapper around one of our OAK
# BIDS DataLad datasets, so con/mechababs@main can select+run it. con/main runs a
# local dataset by cloning a study superdataset whose git-tracked metadata TSV
# drives `select` (no `add-dataset file://`, no network, no `datalad get`).
#
# Faithful to the blessed recipe in con/mechababs tests/e2e/conftest.py::_build_study.
# Then: `mechababs add-dataset <identity-url> --study file://<this study>` and, if the
# study lacks the metadata TSV route, `--processing-level {subject,session}`.
#
# Usage:  build_study_wrapper.sh <cohort>            # cohort = discovery | validation
# Env:    STUDIES_ROOT (default /scratch/users/logben/mechababs_campaigns/studies)
#
# PREREQ — a WORKING datalad + git-annex>=10 on PATH. NOTE (2026-07-22): the
# `datalad-uv` Lmod module fails on the login/dev node (missing libpython3.12.so.1.0);
# run this INSIDE the same job/container context network_fmri uses to build datalad
# datasets, or after that module is fixed. git-annex 10 standalone is at
# /home/groups/russpold/sw/git-annex-standalone (v8 modules CANNOT touch our v10 repos).
set -euo pipefail

COHORT="${1:?usage: build_study_wrapper.sh <discovery|validation>}"
STUDIES_ROOT="${STUDIES_ROOT:-/scratch/users/logben/mechababs_campaigns/studies}"
RAW="/oak/stanford/groups/russpold/data/network_grant/bids/${COHORT}"
ID="${COHORT}"                       # dataset_id = Path(identity-url).name
DEST="${STUDIES_ROOT}/study-${ID}"

export PATH="/home/groups/russpold/sw/git-annex-standalone:${PATH}"
command -v datalad >/dev/null || { echo "FATAL: datalad not on PATH (see PREREQ)"; exit 1; }
ga=$(git annex version | head -1); case "$ga" in *": 1"[0-9]*) : ;; *) echo "FATAL: need git-annex>=10, got $ga"; exit 1;; esac
[ -d "$RAW/.datalad" ] || { echo "FATAL: $RAW is not a DataLad dataset"; exit 1; }

echo "[build] study-${ID}  raw=$RAW  dest=$DEST"
mkdir -p "$STUDIES_ROOT"

# 1) study = a datalad dataset; text2git keeps metadata (TSV + description) in GIT
#    (not annex) so add-dataset's no-content clone gets readable files, not dangling
#    annex symlinks.
datalad create -c text2git "$DEST"

# 2) register the OAK raw BIDS as the sourcedata subdataset (no content fetched;
#    select globs by symlink name to count T1w/bold).
datalad clone --dataset "$DEST" "$RAW" "$DEST/sourcedata/${ID}"

SRC="$DEST/sourcedata/${ID}"

# 3a) subject-level metadata TSV (the fixture's exact columns).
python3 - "$SRC" "$DEST/sourcedata/sourcedata+subjects.tsv" <<'PY'
import sys, csv, glob, os
src, out = sys.argv[1], sys.argv[2]
subs = sorted(d for d in os.listdir(src) if d.startswith("sub-") and os.path.isdir(os.path.join(src, d)))
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["subject_id","datatypes","t1w_num","bold_num"], delimiter="\t")
    w.writeheader()
    for s in subs:
        sp = os.path.join(src, s)
        # datatypes = union across sessions (or direct children for single-session)
        dts = set()
        for root, dirs, _ in os.walk(sp):
            for d in dirs:
                if d in ("anat","func","fmap","dwi","perf"): dts.add(d)
        w.writerow({
            "subject_id": s,
            "datatypes": ",".join(sorted(dts)),
            "t1w_num": len(glob.glob(f"{sp}/**/anat/*_T1w.nii*", recursive=True)),
            "bold_num": len(glob.glob(f"{sp}/**/func/*_bold.nii*", recursive=True)),
        })
print(f"wrote {out}: {len(subs)} subjects")
PY

# 3b) session-level metadata TSV (our data is multi-session; session granularity may
#     be the chosen unit — Austin Q4). select.py reads +subjects+sessions.tsv if present.
python3 - "$SRC" "$DEST/sourcedata/sourcedata+subjects+sessions.tsv" <<'PY'
import sys, csv, glob, os
src, out = sys.argv[1], sys.argv[2]
rows = []
for s in sorted(d for d in os.listdir(src) if d.startswith("sub-")):
    sp = os.path.join(src, s)
    sess = sorted(d for d in os.listdir(sp) if d.startswith("ses-") and os.path.isdir(os.path.join(sp, d)))
    for ses in sess:
        pp = os.path.join(sp, ses)
        dts = sorted(d for d in os.listdir(pp) if d in ("anat","func","fmap","dwi","perf") and os.path.isdir(os.path.join(pp, d)))
        rows.append({
            "subject_id": s, "session_id": ses, "datatypes": ",".join(dts),
            "t1w_num": len(glob.glob(f"{pp}/anat/*_T1w.nii*")),
            "bold_num": len(glob.glob(f"{pp}/func/*_bold.nii*")),
        })
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["subject_id","session_id","datatypes","t1w_num","bold_num"], delimiter="\t")
    w.writeheader(); w.writerows(rows)
print(f"wrote {out}: {len(rows)} (subject,session) rows")
PY

# 4) study-level dataset_description.json (OpenNeuroStudies shape).
cat > "$DEST/dataset_description.json" <<JSON
{
  "Name": "study-${ID}",
  "BIDSVersion": "1.9.0",
  "DatasetType": "study",
  "GeneratedBy": [{"Name": "network_fmri", "Description": "local wrapper around network_grant/bids/${COHORT} for con/mechababs"}]
}
JSON

# 5) commit
datalad save -d "$DEST" -m "study-${ID}: wrap network_grant/bids/${COHORT} for con/mechababs"

echo "[done] $DEST"
echo "  subdataset:"; datalad -f '{path}' subdatasets -d "$DEST" 2>/dev/null || true
echo "  TSVs git-tracked:"; git -C "$DEST" ls-files sourcedata/ | grep -E '\.tsv$'
