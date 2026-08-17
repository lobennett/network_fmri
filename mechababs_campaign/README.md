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
- **`pipelines/fMRIPrep-25.2.5+anat.yaml`** / **`pipelines/fMRIPrep-25.2.5+full.yaml`** —
  the two staged fMRIPrep phases, adapted from `con/mechababs@main pipelines/` (which
  already carries our Sherlock fixes: `--pwd`, `$JOB_TMP`, group templateflow, the
  `--fs-license-file` no-double-bind from con/mechababs#80). Our deltas: the local
  container shim, the per-session `--bids-filter-file`, and the phase split below.
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
- **`mriqc-24.0.2-local-shim.sh`** / **`fmriprep-25.2.5-local-shim.sh`** — build the
  container DataLad dataset (our local `mriqc_24.0.2.sif` / `fmriprep_25.2.5.sif`) in the
  `.datalad/environments/<name>/image` layout babs#383 requires.

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

## Staged fMRIPrep — why it's two registrations, not one chain

`processing_level` is a **dataset** property in the mechababs ledger
(`mechababs/state.py` `IDENTITY_COLUMNS`), not a per-pipeline one: every pipeline run
against a registered dataset inherits that dataset's level. Staged fMRIPrep wants two
different levels, so it can't be one chained campaign cell:

| phase | pipeline | level | why |
|---|---|---|---|
| 1 | `fMRIPrep-25.2.5+anat` | **subject** | anat is acquired sparsely — 7 of 61 discovery sessions have a SagMPRAGE T1w. Session-level would either starve, or (with an unscoped-anat filter) recompute recon-all 61× instead of 5×. |
| 2 | `fMRIPrep-25.2.5+full` | **session** | per-session `--bids-filter-file` is what drops the truncated runs, and a per-session job keeps one bad run from failing a whole subject. |

Phase 2 therefore consumes phase 1's zips as an **external** input dataset rather than
a chained one: `iterate._resolve_chained_inputs` only injects `origin_url` for
`input_datasets` keys that match a *selected* pipeline, so with `+anat` unselected in
phase 2's campaign the url must be written into the yaml — set it to phase 1's output
RIA (`ria+file://…/derivatives/fMRIPrep-25.2.5+anat/.babs/output_ria#~data`) once phase
1 has merged. `merge_config` passes yaml `input_datasets` entries through verbatim, and
the `--fs-subjects-dir` double nesting
(`sourcedata/fMRIPrep-25.2.5+anat/fMRIPrep-25.2.5+anat/sourcedata/freesurfer`) is real:
`path_in_babs` plus the zip's own top folder.

Worth raising upstream: a per-pipeline `processing_level` override would make this one
campaign instead of two registrations plus a manual RIA hand-off.

## Patched babs is required

The campaign venv must pin **`lobennett/babs@fix/plus-regex-zipname`**, not PennLINC
main. Two commits there are load-bearing for the staged fan-out:

- `ea53eb5` — `determine_zipfilename` matched the input name with `grep -E`, so the
  `+` in `fMRIPrep-25.2.5+anat` was read as a quantifier and the zip was never found
  ("Expected exactly 1 matching … zip, found 0"). Now narrowed with `grep -F`.
- `618dcd7` — sanitizes the shell variable name derived from an input name containing
  `.`, `-` or `+` (otherwise an invalid bash identifier).

`bootstrap.sh … --babs /scratch/users/logben/babs_fork@fix/plus-regex-zipname`.
