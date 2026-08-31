# Onboarding and operations

This guide gets a new contributor from a Sherlock login to a trustworthy development
environment and explains how to operate the pipeline safely. Read [the README](../README.md)
first for the pipeline itself and [SCAN-NOTES.md](SCAN-NOTES.md) for scientific decisions.

Live job and cohort status does not belong in this document. Query Slurm, campaign records,
and DataLad history as described in [Checking live state](#checking-live-state).

## Five-minute orientation

1. Work on Sherlock; the package intentionally supports one execution backend: Slurm.
2. Confirm the checkout and branch before editing: `pwd`, `git status --short`, and
   `git branch -vv`.
3. Use the pinned environment. A green test run against stale sibling packages is not
   evidence.
4. Print a pipeline plan before submission.
5. Treat campaign datasets and execution records as provenance, not disposable output.

`network_fmri` owns curation, orchestration, Slurm submission, and DataLad provenance.
Scientific computation belongs to the pinned sibling packages:

| Package | Responsibility |
|---|---|
| `network_events` | event files, truncation QC, and behavior-driven trimming |
| `network_qa` | motion and first-level exclusion decisions |
| `network_glm` | first- and second-level GLMs |
| `network_fmri` | cohort workflow, preprocessing campaign, and handoffs |

Preprocessing has additional layers:

```text
network_fmri
└── mechababs campaign
    └── BABS
        └── DataLad / git-annex
            └── Singularity containers
```

Diagnose failures at the layer that owns the failed action. For example, a RIA transfer is
not a GLM problem, and a container report failure is not a Slurm submission problem.

## First-time setup

Use a compute node for environment creation and wheel builds:

```bash
sh_dev
ml load devel gcc/12.4.0
export UV_PROJECT_ENVIRONMENT=$SCRATCH/venvs/network_fmri_dev
export UV_CACHE_DIR=$SCRATCH/.uv
export PATH="$SCRATCH/git-annex/usr/bin:$PATH"
cd ~/noslop/network_fmri
uv sync --frozen
```

Python is constrained to 3.13. Loading GCC 12.4.0 is required on this CentOS 7 host;
otherwise NumPy may fail with `CXXABI_1.3.9 not found`. Use `uv sync --frozen`, never an
ad hoc `uv pip install`, so the installed sibling commits match `uv.lock`.

Verify what Python will actually import:

```bash
uv run --frozen python -c \
  "import pathlib, network_fmri; print(pathlib.Path(network_fmri.__file__).resolve())"
uv run --frozen network_fmri --help
uv run --frozen pytest -q
```

The complete dependency-pin check and contributor test sequence are in
[CONTRIBUTING.md](../CONTRIBUTING.md).

### Checkouts

Several stale duplicates exist. These are the current working locations; still verify the
installed source and Git SHA rather than trusting a path:

| Package | Working checkout |
|---|---|
| `network_fmri` | `~/noslop/network_fmri` |
| `network_events` | `~/noslop/network_events` |
| `network_qa` | `~/network_qa` |
| `network_glm` | `/oak/stanford/groups/russpold/users/logben/network_glm` |

In particular, `~/network_glm`, `~/network_fmri`, and `$SCRATCH/network_events` are
duplicates. The dependency revisions in `pyproject.toml` and `uv.lock` are authoritative
for reproducible runs.

### Data, campaign, and containers

| Resource | Path |
|---|---|
| Cohort BIDS datasets | `$SCRATCH/network_fmri/<cohort>/bids` |
| Cohort and campaign logs | `$SCRATCH/network_fmri/logs/<cohort>/` (profile plans may be one level below), `.../logs/campaign/` |
| Campaign | `$SCRATCH/mechababs_campaigns/r01network` |
| Campaign ledger | `<campaign>/desc-mechababs_datasets.tsv` |
| BABS projects | `<campaign>/studies/study-<cohort>/derivatives/<Pipeline>` |
| Retired attempts | `<campaign>/derivative-attempts/` |
| TemplateFlow | `/home/groups/russpold/templateflow` |
| FreeSurfer license | `~/license.txt` |
| p7zip | `/share/software/user/open/p7zip/16.02/bin` |

Do not use `/home/groups/russpold/license.txt`; it is the jsPsych MIT license, not a
FreeSurfer license.

The verified container images are:

| Pipeline | Image |
|---|---|
| MRIQC 24.0.2 | `/home/groups/russpold/singularity_images/mriqc_24.0.2.sif` |
| fMRIPrep 25.2.5 | `/oak/stanford/groups/russpold/shared/containers/fmriprep-25.2.5.sif` |
| XCP-D 26.0.2 | `/oak/stanford/groups/russpold/shared/containers/xcp_d-26.0.2.sif` |

The campaign uses DataLad container shims, not the image paths directly. See
[campaign/README.md](campaign/README.md) before rebuilding or modifying a campaign cell.

## Running the workflow

Print the cohort DAG and resolved commands first:

```bash
uv run --frozen network_fmri pipeline --cohort discovery --print
```

Submit only after checking cohort, paths, resources, and exclusions:

```bash
uv run --frozen network_fmri pipeline --cohort discovery --live
```

Each submitted stage writes an incremental JSON execution record in the cohort log
directory. The built-in registry owns stage order, resources, and artifact handoffs.
External packages use versioned lifecycle manifests; they do not edit the central DAG.

```bash
uv run --frozen network_fmri integration validate --check-installed
uv run --frozen network_fmri integration list
uv run --frozen network_fmri pipeline --cohort discovery \
    --enable-integration <name> --print
```

Use the `post-fmriprep` profile for packages that only require verified fMRIPrep output,
and `analysis` when the package also needs the compiled exclusion lockfile. Always pass
explicit `/oak` result paths for large derivatives. Integration receipts live under
`<staging>/logs/<cohort>/integrations/`; a resume cannot bypass an enabled integration
without a receipt unless `--assume-complete` is explicitly supplied. The full contract
and examples are in [EXTENDING.md](EXTENDING.md).

Operate the preprocessing campaign in small, observable steps:

```bash
uv run --frozen network_fmri campaign -- iterate --dry-run
uv run --frozen network_fmri campaign -- iterate --batch 1
uv run --frozen network_fmri campaign -- status
```

The `campaign` wrapper submits its work through Slurm. A campaign iteration advances one
transition per selected cell, potentially across cohorts, so always dry-run and use a
small batch first. `mechababs configure` rewrites the ledger; never run it while cells are
in flight. Changing baked BIDS-app arguments requires retiring and re-scaffolding that
derivative, not merely resubmitting it.

Some DataLad record operations, including `mriqc-iqms` and `fmriprep-derivs`, perform their
work in the foreground. Run them in an allocation or submit an enclosing Slurm job; do not
do heavy data work on the login node. Use each command's `--help` and `--print` behavior to
confirm whether a verb submits work or performs it directly.

## Checking live state

Use the system of record instead of a copied status table:

```bash
squeue --me
uv run --frozen network_fmri campaign -- status
find "$SCRATCH/network_fmri/logs" -name '*.json' -o -name 'slurm-*.out'
git -C "$SCRATCH/network_fmri/discovery/bids" log --oneline -5
```

For a failed stage, check all of the following before rerunning:

1. Slurm state and resource use (`sacct -j <jobid> -X --format=...`).
2. The Slurm log and the pipeline execution record.
3. Expected output files and the relevant DataLad commit.
4. Partial-success summaries such as `Analysis complete: N/M runs successful`.
5. The campaign ledger or BABS project status, when preprocessing is involved.

An OOM can occur after DataLad commits a valid result. Conversely, a wrapper can exit zero
after a nested shell command silently failed. Verify the decisive artifact.

## Known failure modes

| Symptom | Cause and response |
|---|---|
| NumPy reports `CXXABI_1.3.9` | Load `devel` and `gcc/12.4.0`, then resync the frozen environment. |
| FreeSurfer rejects the license | Use `~/license.txt`; the similarly named group file is unrelated. |
| fMRIPrep exceeds its wall time | Keep `--no-submm-recon`; 0.5 mm reconstruction was too expensive and did not improve tested surfaces. |
| XCP-D receives raw BIDS | Preserve campaign `primary_input`; XCP-D must receive fMRIPrep derivatives first. |
| XCP-D sees a phantom anatomical session | Preserve `pre_app_commands` that remove T2w-only `anat/` directories. Filters and `.bidsignore` did not fix XCP-D's query. |
| A lev1 array stays pending on resources | The 64 GB default exceeded discovery's observed 17.9 GB peak. A 32 GB request scheduled that run; right-size future fan-outs from measured RSS. |
| XCP-D report crashes | `--abcc-qc n` is intentional for this container; surface processing remains enabled. |
| `fmriprep-derivs` reports OOM | Check the DataLad commit and files before rerunning. Use 128 GB and 48 hours for validation. |
| A model-tail command lacks input | Pass lev1 directories explicitly. Contrast discovery does not tell a sibling process where its maps live. |
| FSL claims success without corrected maps | Require `*corrp*` output. The fixed GLM code uses `bash -e` and `--seed=0`. |
| VIF excludes nearly everything | `task-baseline` and `response_time` are ignored by default because their construction is collinear; inspect the recorded evidence. |
| A GLM cell exits nonzero but has maps | Run-level junk QA may skip only some runs. Inspect the successful-run count and minimum-runs tag. |
| RIA reports unexpected EOF | Put the venv's `git-annex-remote-ora` on `PATH`. |
| DataLad refuses to run | Clean and save the exact dataset or subdataset first; do not recursively save the whole campaign on a login node. |
| An integration is absent from the plan | Installation is not activation. Check `integration list`, the manifest directory, profile/slot compatibility, and `--enable-integration`. |
| `--from` refuses to skip an integration | Resume at the named integration, restore its receipt, or use `--assume-complete` only after verifying its output independently. |
| Lustre reports transport or I/O errors | Check whether failures cluster on a node/chassis, then retry as infrastructure permits. |

The old proportion-of-frames DVARS rule is not implemented because MRIQC exposes mean
`dvars_std`, not that proportion. A measured mean-based substitute excluded nothing
beyond FD; `dvars_std` remains recorded evidence. Do not silently reinterpret the rule.

`network_glm` also has run-level junk QA (`>30%`) independent of motion and lev1 exclusion
steps. A subject-task cell can therefore produce fixed effects from surviving runs yet
return nonzero. Downstream `afterok` dependencies make that distinction operationally
important.

## Campaign-specific cautions

The campaign vendors patched mechababs and BABS checkouts. Their patch snapshots live in
[docs/campaign](campaign/). Refresh the snapshots whenever those checkouts change.
Current local behavior includes per-pipeline processing levels, resource overrides,
`primary_input`, and `pre_app_commands`.

A campaign cell that is retired is archived under `derivative-attempts/` and is not
resumable because BABS embeds absolute RIA paths. Flags are baked into generated job
scripts at initialization. Scaffolding can take 15 minutes to two hours under Lustre load.

## Scientific choices still requiring a decision

These are analysis decisions, not pipeline defects:

- which confound arm to use (`full`, `no-motion`, `no-cosine`, or `task-only`);
- the final second-level contrasts and permutation count;
- whether and how to restore accuracy, RT, and omission QC after clipping;
- whether to run XCP-D on task-GLM residuals for the task-connectivity arm; and
- whether to retain multi-echo outputs, currently about one third of fMRIPrep storage;
- which response-time arm to use for headline analyses; and
- which first-level smoothing kernel to use (the completed discovery run used none).

The evidence behind the last two choices is in [GLM-DIAGNOSTICS.md](GLM-DIAGNOSTICS.md).

Do not present the completed discovery engineering run as the selected scientific model
until these choices are resolved.

## Storage and retention

An unpacked fMRIPrep subject is about 230 GB. Validation can approach 85 TB of the 100 TB
scratch allocation when echoes are retained. `fmriprep-derivs` drops fetched zip files
after extraction unless `--keep-zips` is set; the output RIA remains the durable copy.
Sherlock scratch is subject to purge based on content writes, and `touch` does not reset
that policy.

## Maintaining the documentation

Update the document that owns the fact:

| Change | Update |
|---|---|
| user workflow, stage summary, or common command | `README.md` |
| environment, paths, operations, or diagnosed failures | this guide |
| scientific, acquisition, exclusion, or preprocessing decision | `SCAN-NOTES.md` |
| response-time arm, map sparsity, design checks, or reliability evidence | `GLM-DIAGNOSTICS.md` |
| lifecycle integration contract or manifest schema | `EXTENDING.md` |
| campaign patch or reconstruction detail | `campaign/README.md` and patch snapshots |
| contributor checks or dependency workflow | `CONTRIBUTING.md` |

Keep documentation in the same commit as the behavior it describes. Replace stale live
status with a query, record ruled-out approaches for expensive diagnoses, and remove facts
that are no longer true.
