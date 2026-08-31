# network_fmri

`network_fmri` orchestrates the r01network study from Flywheel curation through BIDS,
preprocessing handoff, quality gates, and model submission on Stanford's Sherlock cluster.
Slurm executes the work; DataLad records data-writing steps; pinned sibling packages own
events, exclusions, and GLMs. Versioned lifecycle integrations let another package join
at a supported boundary without changing the central pipeline.

The built dataset contains **57 subjects, 590 sessions, 2,738 BOLD acquisitions, and
2,111 `events.tsv` files** across three cohorts: `discovery` (5), `validation` (41),
and `excluded` (11). Cohort outputs live under
`$SCRATCH/network_fmri/<cohort>/`.

## Start here

| If you need to… | Read |
|---|---|
| Understand or run the main workflow | This README |
| Set up on Sherlock or diagnose operations | [Onboarding and operations](docs/AGENT-ONBOARDING.md) |
| Understand exclusions and scientific preprocessing choices | [Scan and scientific decisions](docs/SCAN-NOTES.md) |
| Interpret sparse first-level maps, RT arms, or reliability | [First-level GLM diagnostics](docs/GLM-DIAGNOSTICS.md) |
| Operate or recreate MRIQC/fMRIPrep/XCP-D | [Preprocessing campaign](docs/campaign/README.md) |
| Add a Python package before or after preprocessing | [Adding a package](docs/EXTENDING.md) |
| Change code or dependency pins | [Contributing](CONTRIBUTING.md) |

## System overview

```text
Flywheel
  └─ BIDS profile (12 dependent Slurm stages)
       export → merge → prepare → events → validate → check
                    ↑ pre-trim                 ↓ pre-fMRIPrep integrations
         ├─ MRIQC campaign ──→ IQMs ──→ motion/behavior lockfile
         └─ fMRIPrep campaign → verified derivatives → post-fMRIPrep integrations
                                      └─ verified exclusions → analysis integrations
                                                                     └─ GLMs
```

Responsibility is intentionally split:

| Package/system | Owns |
|---|---|
| `network_fmri` | Curation rules, Slurm orchestration, campaign handoff, DataLad recording |
| `network_events` | Behavioral timing, `events.tsv`, and truncation QC |
| `network_qa` | Motion, behavioral, and level-1 exclusion decisions |
| `network_glm` | First- and second-level model computations |
| mechababs/BABS | Containerized MRIQC, fMRIPrep, and XCP-D campaign execution |

## Sherlock setup

Use a compute node, not a login node:

```bash
ml load devel gcc/12.4.0
export UV_PROJECT_ENVIRONMENT="$SCRATCH/venvs/network_fmri_dev"
export UV_CACHE_DIR="$SCRATCH/.uv"
uv sync --frozen
uv run --frozen network_fmri --help
```

Keep the environment on `$SCRATCH`; `$HOME` is small NFS storage. Use `uv sync`, not
`uv pip install`, and verify installed sibling-package revisions before trusting tests.
The exact check and canonical paths are in
[Onboarding and operations](docs/AGENT-ONBOARDING.md#first-time-setup).

Flywheel credentials come from `~/.config/flywheel/user.json` and can be created with
`fw login <key>`.

## Run the cohort pipeline

Inspect before submitting:

```bash
uv run --frozen network_fmri pipeline --cohort discovery --print
uv run --frozen network_fmri pipeline --cohort discovery --live
uv run --frozen network_fmri pipeline --cohort discovery --from trim --live

# Inspect explicitly activated package integrations before submitting.
uv run --frozen network_fmri integration list
uv run --frozen network_fmri pipeline --cohort discovery \
    --enable-integration package-name --print
```

`--print` has no filesystem side effects unless `--plan-json PATH` is supplied.
A live submission returns after queuing the dependency graph and writes an atomic
`pipeline-plan-*.json` under `$SCRATCH/network_fmri/logs/<cohort>/`. The record includes
the code revision, dirty state, subjects, commands, resources, artifacts, dependencies,
providers, job IDs, and any partial-submission failure.

Monitor with:

```bash
squeue --me
grep -rh "failed after" "$SCRATCH/network_fmri/logs"/*/*.err
```

### Built-in stages

| # | Stage | Result |
|---:|---|---|
| 1 | `export` | Curate and download one BIDS DataLad dataset per subject |
| 2 | `merge` | Merge subject datasets into one cohort tree |
| 3 | `fix-sidecars` | Coerce known invalid DICOM-derived JSON values |
| 4 | `validate-pre` | Run the BIDS validator before preparation |
| 5 | `gs-pre` | Save pre-trim global-signal QA |
| 6 | `trim` | Remove seven dummy volumes and stamp the sidecars |
| 7 | `b0link` | Link field maps and BOLD runs for distortion correction |
| 8 | `gs-post` | Save post-trim global-signal QA |
| 9 | `ingest-beh` | Add canonical behavioral files under `sourcedata/` |
| 10 | `events` | Build scan-aligned `events.tsv` files |
| 11 | `validate-post` | Validate the prepared dataset |
| 12 | `check` | Assert study-specific invariants the validator cannot detect |

The final checks cover event bounds, duplicate anatomy, dummy-volume stamps, and field-map
links. Each stage is also available as a standalone command for targeted recovery.

Before a clean rebuild, replay the idempotent Flywheel QA marks:

```bash
uv run --frozen network_fmri qa-reject --apply
```

This is intentionally outside the cohort chain because it mutates the shared Flywheel
project.

### Package lifecycle integrations

New packages use versioned manifests and one of four stable slots: `pre-trim`,
`pre-fmriprep`, `post-fmriprep`, or `analysis`. Installation alone never activates a v1
integration. The post-fMRIPrep and analysis profiles verify external derivatives and the
compiled exclusion lockfile before submitting the package job.

```bash
uv run --frozen network_fmri integration validate --check-installed
uv run --frozen network_fmri pipeline --cohort discovery \
    --profile analysis --fmriprep-dir <fmriprep> \
    --exclusions-file <motion-lock.json> --analysis-dir <results> \
    --enable-integration package-analysis --print
```

Every integration gets an atomic execution receipt with the package version, exact argv,
inputs, outputs, timestamps, and status. See [Adding a package](docs/EXTENDING.md) for the
manifest schema, effect semantics, resume safeguard, and contributor checklist.

## Preprocessing and models

MRIQC and fMRIPrep are independent consumers of the checked BIDS tree and may run
concurrently through the campaign. Always dry-run a campaign advance:

```bash
uv run --frozen network_fmri campaign -- iterate --dry-run
uv run --frozen network_fmri campaign -- iterate --batch 1
uv run --frozen network_fmri campaign -- status
```

After campaign cells merge, the normal downstream order is:

```text
MRIQC → mriqc-iqms → qa-motion ───────────────┐
                                               ├→ glm-lev1
fMRIPrep → fmriprep-derivs ──────────────────┘
glm-lev1 → glm-outliers → qa-lev1 → glm-lev2
```

Representative commands, with paths replaced for the analysis:

```bash
# Foreground DataLad operation: run in an allocation or enclosing Slurm job.
uv run --frozen network_fmri mriqc-iqms --cohort discovery
uv run --frozen network_fmri qa-motion --cohort discovery

# Foreground DataLad operation: submit this wrapper with enough memory.
sbatch -p russpold,normal -c 8 --mem=128G -t 48:00:00 \
    --wrap "uv run --frozen network_fmri fmriprep-derivs --cohort discovery"

uv run --frozen network_fmri glm-lev1 --cohort discovery --base-tasks \
    --results-dir <lev1> -- \
    --bids-dir <bids> --fmriprep-dir <fmriprep> \
    --exclusions-file <motion-lock.json> --residuals

uv run --frozen network_fmri glm-outliers --lev1-dirs <lev1> \
    --results-dir <lev1>/cohort_qa
uv run --frozen network_fmri qa-lev1 --cohort discovery --lev1-dir <lev1> \
    --dependency <glm-outliers-job-id>
uv run --frozen network_fmri glm-lev2 --lev1-dirs <lev1> --all \
    --results-dir <lev2> -- --num-permutations 5000
```

Arguments after `--` pass unchanged to the owning sibling package. This repository owns
fan-out, Slurm resources, dependencies, and host modules; the sibling package defines the
scientific meaning of those arguments.

The campaign configuration, container locations, shim requirement, and XCP-D adaptations
are documented in [docs/campaign/](docs/campaign/).

## Data exclusions at a glance

Source curation removes non-analysis acquisitions and ten rejected duplicate anatomicals.
Functional runs otherwise proceed through preprocessing; motion and behavioral exclusions
are applied when models consume the data rather than by deleting preprocessed output.

| Decision point | Effect |
|---|---|
| Curation allowlists | Skip localizers, shims, SBRefs, PROMO navigators, and an unused second field map |
| Flywheel `qa-reject` | Exclude 10 duplicate anatomical scans |
| Behavioral reconciliation | Leave 5 false starts and 8 runs with no source file without events |
| Event creation | Clip 22 behavioral records to the acquired scan |
| `qa-motion` | Gate level-1 runs using MRIQC motion and behavioral evidence |
| `qa-lev1` | Gate level-2 inputs using level-1 outlier evidence |

Exact subjects, sessions, evidence, and known limitations are in
[docs/SCAN-NOTES.md](docs/SCAN-NOTES.md).

## Design and provenance

- Versioned lifecycle manifests are the supported package boundary. They compile to the
  existing typed registry, which validates commands, resources, dependencies, and logical
  artifacts before submission. Slurm remains the only backend; see
  [docs/EXTENDING.md](docs/EXTENDING.md).
- Subject exports are separate DataLad datasets so array tasks never contend on one Git
  index.
- In-place preparation commands are designed to resume safely and are recorded by DataLad.
- The lockfile and immutable dependency pins define the software environment.
- Reconciled behavioral data is a separate DataLad dataset on `$OAK`.
- Flywheel curation is a remote mutation, so `curate --live` re-tags shared source state
  rather than reproducing a filesystem output.

## Repository layout

```text
src/network_fmri/
  registry.py          CLI and internal typed stage contracts
  pipeline.py          plan, record, and submit the Slurm DAG
  integrations/        public v1 contracts, manifests, profiles, and receipts
  cohorts.py           rosters and staging locations
  provenance.py        DataLad and Git provenance helpers
  fw2bids/             Flywheel curation, export, merge, and source QA marks
  prepare/             sidecar fixes, dummy-volume trimming, and field-map links
  behavior/            canonical behavioral-data ingestion
  qa/                  validation, invariants, campaign handoff, and exclusions
  glm/                 Slurm fan-out for network_glm
tests/                 unit and orchestration contract tests
docs/                  operational, scientific, extension, and campaign references
```
