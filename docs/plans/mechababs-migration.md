# MechaBABS migration implementation plan

**Goal:** Move MRIQC and fMRIPrep execution from the custom Slurm graph to one reproducible MechaBABS study while preserving explicit scan and surface review gates.

**Architecture:** `network_fmri` finishes and validates the canonical raw BIDS dataset. A small MechaBABS adapter creates or verifies the wrapper study, installs that raw dataset at `sourcedata/raw`, initializes a locked campaign, and advances one app at a time. The current DataLad datasets remain untouched during migration.

**Tech stack:** Python 3.13, DataLad, MechaBABS, BABS, TOML, YAML, pytest, uv

**Design:** [docs/mechababs.md](../mechababs.md)

## 1. Add strict MechaBABS configuration

**Files:** `src/network_fmri/config.py`, `tests/test_config.py`, `config/workflow.example.toml`

1. Add failing tests for a `[mechababs]` table containing absolute `study_dir`, `durable_sibling`, and `container_dataset`; a nonempty campaign label and raw slot; 40-character MechaBABS and BABS commits; and project-relative cluster and app config paths.
2. Test rejection of unknown keys, relative runtime paths, invalid commits, duplicate app names, and a study path nested inside the raw BIDS dataset.
3. Add frozen `MechaBABSConfig` and `MechaBABSAppConfig` models and parse them into `WorkflowConfig`.
4. Add reviewed example values for the Sherlock study and Oak sibling.
5. Run `uv run pytest tests/test_config.py` and commit `Add MechaBABS workflow configuration`.

## 2. Add project-owned cluster and app files

**Files:** `config/mechababs/clusters/sherlock.yaml`, `config/mechababs/apps/mriqc-24.0.2.yaml`, `config/mechababs/apps/fmriprep-25.2.5-anatomical.yaml`, `config/mechababs/apps/fmriprep-25.2.5-full.yaml`, `tests/test_mechababs_configs.py`

1. Write failing schema/reference tests that load every YAML file, verify the pinned image and version, and require the full fMRIPrep app to consume the anatomical derivative.
2. Adapt the existing Sherlock config so jobs activate the campaign environment, expose modern Git and git-annex, use node-local work space, cap threads, and read the configured FreeSurfer license.
3. Adapt the existing MRIQC and fMRIPrep app files. The anatomical app must produce reusable FreeSurfer and anatomical outputs; the full app must declare both `depends_on` and the anatomical derivative input.
4. Run `uv run pytest tests/test_mechababs_configs.py` and commit `Add Sherlock MechaBABS app configurations`.

## 3. Create and verify the wrapper study

**Files:** `src/network_fmri/study.py`, `tests/test_study.py`

1. Write failing tests with temporary Git/DataLad repositories and a recording runner for fresh creation, matching reruns, conflicting dataset IDs, wrong raw commits, dirty inputs, and pilot subject/session tables.
2. Implement `StudyManager.initialize(config, pilot_subject=None)`. It must verify a clean raw dataset, create a `DatasetType: study` root, install the raw dataset at the configured slot, write MechaBABS subject/session metadata, create the durable sibling, and initialize the campaign from project-owned configs.
3. Make reruns no-ops only when the study ID, raw subdataset URL and commit, campaign label, copied config hashes, and durable sibling match.
4. Return a typed result containing dataset IDs, commits, generated paths, and commands executed; never move, delete, or rewrite the source raw dataset.
5. Run `uv run pytest tests/test_study.py` and commit `Create idempotent MechaBABS studies`.

## 4. Add the processing adapter and gates

**Files:** `src/network_fmri/processing.py`, `tests/test_processing.py`

1. Write failing tests for the stage map `mriqc -> anatomical -> fmriprep`, plan output, refreshed status, and one reconciliation step per `advance` call.
2. Test gates: prepared-BIDS validation for MRIQC; committed scan approval plus curated validation for anatomical processing; committed surface approval for full fMRIPrep.
3. Implement `ProcessingManager.plan`, `status`, and `advance` with an injected runner. Parse command output into typed campaign/app/cell records and surface intervention states without hiding failed jobs.
4. Require a clean study, raw subdataset, nested behavioral subdatasets, and campaign before submission. Require campaign lock files to pin the configured MechaBABS and BABS commits.
5. Reuse the existing committed review validators. Change surface review lookup to the anatomical derivative installed in the wrapper study.
6. Run `uv run pytest tests/test_processing.py tests/test_decisions_stage.py tests/test_freesurfer_stage.py` and commit `Gate MechaBABS processing stages`.

## 5. Expose the study and processing commands

**Files:** `src/network_fmri/cli.py`, `tests/test_cli.py`

1. Add failing CLI tests for `study init`, `processing plan`, `processing status`, and `processing advance --stage` including pilot selection and nonzero failures.
2. Add the commands with the exact interface in the approved design. Keep command handlers thin and return adapter exit codes.
3. Print stable tabular or JSON-safe output suitable for both operators and the future record collector.
4. Run `uv run pytest tests/test_cli.py` and commit `Expose MechaBABS orchestration commands`.

## 6. Shorten the raw-data Slurm graph

**Files:** `src/network_fmri/pipeline.py`, `tests/test_stage_graph.py`, `tests/test_pipeline_e2e.py`; delete `src/network_fmri/qa/mriqc.py`, `src/network_fmri/qa/fmriprep.py` and their direct execution tests after equivalent MechaBABS coverage exists

1. Change graph tests first so the custom graph ends after prepared raw-BIDS validation and no longer contains MRIQC, FreeSurfer, or fMRIPrep jobs.
2. Preserve Flywheel conversion, immediate defacing, behavior/participant ingestion, trimming, events, fieldmap links, global-signal derivatives, and raw BIDS validation.
3. Remove custom app submission and receipt code that MechaBABS/BABS now owns. Retain review generation, validation, and curation functions used between apps.
4. Replace the synthetic end-to-end test with two flows: raw preparation through validation, and adapter-driven processing through both approval gates.
5. Run `uv run pytest tests/test_stage_graph.py tests/test_pipeline_e2e.py` and commit `Hand BIDS app execution to MechaBABS`.

## 7. Preserve reviewed decisions during additive migration

**Files:** `src/network_fmri/reviews.py`, `tests/test_review_migration.py`

1. Write failing tests that match rows only by normalized BIDS entity key plus evidence values, reject missing/extra/changed evidence, preserve reviewer initials and decisions, and never copy absolute source paths.
2. Implement regeneration of scan and surface manifests against the installed raw and anatomical derivative.
3. Reapply reviewed values only for exact matches, validate each regenerated manifest with the existing package-owned validator, and write a new committed approval receipt in the wrapper study.
4. Produce a mismatch report and leave the destination unsealed when any row cannot be proven equivalent.
5. Run `uv run pytest tests/test_review_migration.py` and commit `Migrate review decisions by verified BIDS identity`.

## 8. Validate locally and on a Sherlock pilot

**Files:** `README.md`, `docs/sherlock.md`, `uv.lock`

1. Replace the old README processing commands with the short study-init and stage-advance sequence; keep operational detail in `docs/sherlock.md`.
2. Run `uv lock --check`, `uv run pytest`, and `uv build` locally.
3. On Sherlock, first verify the FreeSurfer license with the pinned container, because the previous pilot failed that check.
4. Create a fresh one-subject study and campaign. Complete MRIQC, regenerate/reseal scan review, curate and validate raw BIDS, complete anatomical processing, regenerate/reseal surface review, then complete full fMRIPrep.
5. Verify DataLad identities/commits, derivative subdatasets, campaign locks, BABS job status, and a clean study. Do not create the 46-subject campaign until the pilot passes.
6. Commit `Document the MechaBABS workflow`, run the full verification suite once more, and push `main`.

