# MRIQC-via-BABS/mechababs campaign (Sherlock)

Version-controlled **campaign inputs** for running **MRIQC 24.0.2** on our BIDS DataLad
datasets through **BABS**, driven by **`con/mechababs`@main**, on Sherlock. These files are
config only — the campaign itself (its `.venv`, vendored `code/{babs,mechababs}`, ledger,
`studies/`, `derivatives/`) is a separate DataLad dataset that lives on `$SCRATCH`/`$OAK`,
never in this repo.

**Single source of truth for current state, the con/main schema, the pre-flight snags, and
the Austin agenda:** `/scratch/users/logben/mechababs_campaigns/MEETING_BRIEF_2026-07-22.md`.

Upstream: `con/mechababs`@main (read-only reference clone at
`/scratch/users/logben/mechababs`) and `PennLINC/babs`.

## Files here
- **`pipelines/MRIQC-24.0.2.yaml`** — the pipeline axis, con/main schema. `mechababs:` namespace
  (selection + container), BIDS-study RIA keys, and the Sherlock-specific singularity binds
  (`--pwd`, `-B $JOB_TMP:/tmp`, group TemplateFlow). Filename stem = derivative dir name.
- **`clusters/sherlock.yaml`** — the cluster axis. Job preamble (Lmod, git-annex-10 standalone
  on PATH, campaign venv activate, per-job `$JOB_TMP`) + Slurm `cluster_resources`
  (`--partition=russpold,normal`; overrides the pipeline block).
- **`build_study_wrapper.sh`** — build the local "study" wrapper con/main selects against
  (`_build_study` recipe: `datalad create -c text2git` → clone the raw BIDS as
  `sourcedata/<id>` → write subject- and session-level metadata TSVs → `dataset_description.json`
  → `datalad save`). Run once per cohort.
- **`mriqc-24.0.2-local-shim.sh`** — build the container DataLad dataset (our local
  `mriqc_24.0.2.sif`) in the `.datalad/environments/bids-mriqc/image` layout babs#383 requires.

## How it composes
A **campaign** = a DataLad superdataset; each run = **dataset × pipeline × cluster**.
`merge_config` composes pipeline × cluster into the single `babs-config.yaml` that `babs init`
consumes (stripping the `mechababs:` namespace). `configure` vendors the container;
`build_study_wrapper.sh` + `mechababs add-dataset … --study …` register a dataset;
`mechababs iterate` drives babs init → submit → status → merge per cell until merged.

## Run sequence (post-green-light)
`bootstrap.sh` (campaign venv: babs + mechababs + con-duct) → `mechababs configure` (vendor
shim) → `build_study_wrapper.sh discovery && build_study_wrapper.sh validation` →
`mechababs add-dataset <cohort-path> --study file://…/study-<id> [--processing-level session]` →
`mechababs iterate` (repeat until merged) → `mechababs status`.

## Input location (2026-08-17)

The cohort DataLad datasets are **not on OAK**. They were promoted to
`/oak/.../network_grant/bids/<cohort>` on 2026-07-19, then moved back to scratch by the
2026-08-02 Oak reorg (`safemove`, verified `residual_diffs=0`), so the canonical
promote-ready clones are:

```
/scratch/users/logben/network_grant_staging/bids/{discovery,validation,excluded}
```

`build_study_wrapper.sh` reads `$RAW_ROOT` (default = that path); point it back at OAK
after a re-promote. Verified 2026-08-17 for discovery inside `network_fmri.sif`:
clean tree, `n_missing=0`, `annex fsck --fast` rc=0.

## Two traps found while wiring the filter files (2026-08-17)

1. **Don't run MRIQC at session level.** `bids-mriqc` has no "prep" in its container
   name, so babs's `flag_filterfile` is False and it generates no session filter;
   the MRIQC YAML also has no `$SESSION_SELECTION_FLAG`, so every session job would
   process *all* of that subject's sessions. Adding the flag doesn't fix it either —
   babs passes `$sesid` verbatim (`ses-11`), while pybids session entities are bare
   (`11`). Run MRIQC with `--processing-level subject`.

2. **fMRIPrep's per-session filter must supersede babs's, and babs's is wrong here.**
   babs session-scopes `t1w`/`t2w`/`flair`/`roi`, but only **7 of 61** discovery
   sessions contain a SagMPRAGE T1w — anat is acquired sparsely — so babs's own
   filter would starve 54/61 jobs. Pass our per-session file
   (`code/bids-filter_fmriprep_${subid}_${sesid}.json`, rendered by the `select`
   stage) via `bids_app_args`; babs emits its own `--bids-filter-file` first, so ours
   wins by argparse. Because a session-level job then resolves anat from another
   session, FreeSurfer would be recomputed per session — which is the argument for
   babs **pipeline mode** (anat step first, session steps reusing it) rather than a
   single-step session-level fMRIPrep.
