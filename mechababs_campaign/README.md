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
  (`_build_study` recipe: `datalad create -c text2git` → clone the OAK raw BIDS as
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
`mechababs add-dataset <oak-path> --study file://…/study-<id> [--processing-level session]` →
`mechababs iterate` (repeat until merged) → `mechababs status`.
