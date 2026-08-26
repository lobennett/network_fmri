# Agent onboarding

Read this before touching anything. It is the context that is *not* recoverable from the
code: which checkout is real, which paths are load-bearing, and which plausible-looking
approaches have already been tried and failed.

For what the pipeline *does* — stages, exclusion points, which scans are dropped where —
read [../README.md](../README.md) then [SCAN-NOTES.md](SCAN-NOTES.md). This file is about
working *on* it.

**Keep this file current.** Every change to the pipeline, the campaign config, or the
vendored patches must land here in the same commit. See [Maintaining this file](#maintaining-this-file).

---

## 1. What this is

`network_fmri` orchestrates Flywheel → BIDS → derivatives → models for the r01network
study on Sherlock. It owns *curation and submission*; the science lives in four sibling
packages it pins by commit. Preprocessing (MRIQC, fMRIPrep, XCP-D) runs through a
[mechababs](https://github.com/lobennett/mechababs) campaign that wraps
[BABS](https://github.com/PennLINC/babs), which wraps DataLad.

Two cohorts: **discovery** (5 subjects) and **validation** (41). Plus `excluded`.

Layers, outermost first — a bug is almost always in the layer you did not suspect:

```
network_fmri  (this repo)      verbs, Slurm submission, DataLad provenance
  mechababs   (vendored)       campaign ledger, per-cell scaffold/submit/merge
    babs      (vendored)       generates the job script, owns the RIA + zip protocol
      datalad / git-annex      content addressing, RIA stores
        singularity            MRIQC / fMRIPrep / XCP-D containers
```

## 2. Paths that matter

### Repo checkouts

| Package | Canonical path | Notes |
|---|---|---|
| `network_fmri` | `~/noslop/network_fmri` | **this repo**; the editable install resolves here |
| `network_events` | `~/noslop/network_events` | events.tsv, truncation QC, behaviour-driven trimming |
| `network_qa` | `~/network_qa` | exclusion decisions → lockfile |
| `network_glm` | `~/network_glm` | lev1/lev2 GLMs |

> **Trap: stale duplicate checkouts exist.** `~/network_fmri` and `$SCRATCH/network_events`
> are *old* copies — editing them changes nothing. Confirm before you edit:
> ```bash
> python -c "import network_fmri, pathlib; print(pathlib.Path(network_fmri.__file__).parent)"
> ```
> Note `network_qa` and `network_glm` are in `~`, not `~/noslop`. There is no single parent
> directory holding all four.

### Data and campaign

| What | Path |
|---|---|
| Cohort BIDS trees (DataLad datasets) | `$SCRATCH/network_fmri/<cohort>/bids` |
| Job logs | `$SCRATCH/network_fmri/logs/<cohort>/`, `.../logs/campaign/` |
| Campaign | `$SCRATCH/mechababs_campaigns/r01network` |
| Vendored mechababs / babs | `<campaign>/code/mechababs`, `<campaign>/code/babs` |
| Container shims | `<campaign>/code/<name>-shim`, built from `$SCRATCH/mechababs_campaigns/<name>-shim` |
| babs projects | `<campaign>/studies/study-<cohort>/derivatives/<Pipeline>` |
| Retired attempts (archive, **not** resumable) | `<campaign>/derivative-attempts/` |
| Ledger | `<campaign>/desc-mechababs_datasets.tsv` |

### Environment

```bash
export UV_PROJECT_ENVIRONMENT=$SCRATCH/venvs/network_fmri_dev
export UV_CACHE_DIR=$SCRATCH/.uv
export PATH="$SCRATCH/git-annex/usr/bin:$PATH"          # git-annex
# git-annex-remote-ora ships in the venv bin — needed for ANY RIA read/drop
# p7zip: /share/software/user/open/p7zip/16.02/bin
```

| Resource | Path |
|---|---|
| FreeSurfer license | `~/license.txt` — **not** `/home/groups/russpold/license.txt`, which is the jsPsych MIT license |
| Containers | split across two dirs, inconsistently named — see below |
| TemplateFlow | `/home/groups/russpold/templateflow` |
| Lab reference for XCP-D flags | `/oak/stanford/groups/russpold/users/grimsrud/projects/pfm_compare/code/fmriprep_xcpd/xcpd/` |

The three images the campaign uses are **not** in one place, and the naming alternates
between `_` and `-`. Do not guess a path; these are the verified ones:

| Pipeline | Image |
|---|---|
| MRIQC 24.0.2 | `/home/groups/russpold/singularity_images/mriqc_24.0.2.sif` |
| fMRIPrep 25.2.5 | `/oak/stanford/groups/russpold/shared/containers/fmriprep-25.2.5.sif` |
| XCP-D 26.0.2 | `/oak/stanford/groups/russpold/shared/containers/xcp_d-26.0.2.sif` |

The shims already hold these; the paths matter only when rebuilding one with
`network_fmri shim`. `/oak/.../shared/containers/` also holds ~60 historical fMRIPrep and
MRIQC builds, so a loose glob will match the wrong decade.

## 3. Before you trust any test run

**The venv drifts from `pyproject.toml`.** It has been out of sync in *both* directions at
once — one package newer than its pin, another older. A green test suite against a stale
install means nothing; this has already produced a false "400 passed". Check first, then
sync:

```bash
python - <<'EOF'
import json, pathlib, importlib.metadata as md
for p in ("network_events", "network_qa", "network_glm"):
    d = md.distribution(p)._path / "direct_url.json"
    print(p, json.loads(d.read_text())["vcs_info"]["commit_id"][:8])
EOF
uv sync --frozen     # never `uv pip install`
```

`uv sync` needs a compute node (`sh_dev`) — it builds wheels. And `ml load devel gcc/12.4.0`
first, or numpy import fails with `CXXABI_1.3.9 not found` (host glibc is 2.17 / CentOS 7,
so `manylinux_2_28` wheels are unusable; only `manylinux2014` == `manylinux_2_17` works).

## 4. Sherlock rules that bite

Read `/etc/claude-code/CLAUDE.md` — it is authoritative. The ones that have actually cost
time here:

- **Nothing heavy on the login node.** Every verb that touches data submits a job; some
  (`fmriprep-derivs`, `campaign`) run in the *foreground* and must be wrapped in `sbatch`
  yourself.
- **`datalad save -r` on the campaign times out** from the login node — it walks every
  derivative subdataset. Commit the specific subdataset with plain `git`, then the parent
  pointer, or do the save in a job.
- **A dirty dataset blocks everything downstream.** `datalad run` refuses to start, and
  mechababs refuses to `iterate`. Editing a vendored patch dirties the campaign; so did
  `qa-motion` writing an unsaved lockfile (fixed).
- **`$SCRATCH` is purged after 90 days of no *content* writes.** `touch` does not count.
- **Lustre flakes.** `Cannot send after transport endpoint shutdown` / `OSError [Errno 5]`
  clustered on one chassis is infrastructure, not your bug. Retry.

## 5. Errors already diagnosed — do not re-derive these

Ordered roughly by how much time they cost.

### fMRIPrep license failure (×10 jobs)
`/home/groups/russpold/license.txt` is the **jsPsych MIT license**, found by a `find` and
assumed to be FreeSurfer's. Use `~/license.txt`. Verify with
`check_valid_fs_license() -> True` before submitting a fleet.

### Submillimetre recon burning the wall clock
All T1w are 0.5×0.5×0.8 mm, so fMRIPrep defaulted to `-hires -cm` at 0.5 mm iso against a
24 h wall. `--no-submm-recon` is required. Verified it does not degrade surfaces: hole
counts match 25.2.4 (s19 reproduced exactly, 16/11).

### The session-level anat→full fMRIPrep chain (abandoned)
Broke three ways on longitudinal one-anat-session data, and would have re-run `recon-all`
per session. Also `+full` selection required `anat` in-session, which matched 4 of 61
discovery sessions. **fMRIPrep is subject-level.** BABS chaining requires producer and
consumer to share a processing level — which is why XCP-D is subject-level too.

### XCP-D, three separate failures
1. **`babs init` cannot clone the container.** babs clones a *shim dataset*, not a `.sif`
   path. Worse, a shim can hold the image with no `datalad.containers.<name>` registration
   — that clones fine and fails later with a much vaguer error. `network_fmri shim` guards
   on the registration, not the directory.
2. **Wrong positional input.** babs passes `input_datasets[0]` as the app's input dir
   ("The input dataset is always the first one in the list"), and merge_config forced raw
   BIDS first unconditionally. XCP-D was handed `sourcedata/raw` instead of the fMRIPrep
   derivatives it unzips one line earlier. Fixed by `mechababs.primary_input`, which
   defaults to `BIDS` so MRIQC/fMRIPrep are unchanged.
3. **Phantom anatomical session.** XCP-D counts a session as anatomical if it holds a T1w
   **or** T2w, and accepts only one-anat-per-func-session or one-anat-for-all
   (`parser.py:1080`). This study has the T2w in a different session from the T1w for 9 of
   46 subjects, so fMRIPrep writes a T2w-only `anat/` that reads as a second anatomical
   session. Fixed by `pre_app_commands` pruning anat dirs with no T1w.
   **Already ruled out:** `--bids-filter-file` (the filter→session path is commented out
   in XCP-D), `.bidsignore` (does not hide files from the layout query) — both tested
   empirically — and `--session-id`, which would drop the T2w session's functional runs.

Plus `--abcc-qc n`: the ABCC executive summary cannot build in this container at all (the
brainsprite node dies on `_warn() got an unexpected keyword argument 'skip_file_prefixes'`,
then `generate_reports` raises `KeyError: 'task'`), and `--stop-on-first-crash` turns
either into a failed subject. Surface processing is unaffected — it is gated on
`process_surfaces OR abcc_qc` and `--warp-surfaces-native2std` sets the former.

### `fmriprep-derivs` OOM that was not a failure
Reported `OUT_OF_MEMORY` at 32 GB after 9 h — but the OOM hit datalad's post-run check
*after* the save had committed, so the result was intact and the **exit status was
misleading**. Always check `git log` in the cohort tree before re-running a failed stage.
Cost: ~230 GB/subject unpacked; discovery's 5 took 4 h to extract and 5 h to annex. Give
validation `--mem=128G -t 48:00:00`.

### The whole model tail was wired wrong
`glm-outliers`, `qa-lev1` and `glm-lev2` had never run, and two of them *could not* have:
each failed to pass a required input path to the `network_glm` / `network_qa` subprocess it
drives (`--lev1-dir`, `--lev1-outliers-csv`, `--level1-dirs`). Discovering the contrasts in
the wrapper does not tell the subprocess where the maps are. If you add a verb that shells
out to a sibling package, run it once for real — `--print` will not catch this.

### randomise silently produced no corrected inference
FSL's parser rejects a space-separated `--seed 0` ("Missing non-optional argument!") and
exits 1; `--seed=0` works. The permutation/TFCE pass therefore never ran. It was invisible
because the generated script issues several `randomise` calls and sets no errexit, so bash
returned the *last* call's status, `check=True` never fired, and all 44 discovery contrasts
reported "✓ FSL randomise completed successfully" with only `uncorrected_tstat1.nii.gz` on
disk. Fixed in network_glm, which now runs `bash -e` and asserts a `*corrp*` map exists
before claiming success. Available FSL modules: 5.0.10 (the `ml biology fsl` default),
6.0.4, 6.0.6.2, 6.0.7.10.

### VIF exclusions that removed everything
`qa-lev1` with stock thresholds excluded **all 40** discovery subject × task cells, on
`strict_vif` for `task-baseline` (VIF 59) and `response_time` (VIF 23). Both are high by
construction — RT is collinear with the task regressors it derives from, and task-baseline
sums every condition — so this was a property of the design, not data quality. network_qa
now skips those two contrasts by default (`--vif-ignore-contrasts`), which takes discovery
to 49 exclusions over 15 cells. They remain in `lev1_outliers.csv` as evidence, the same
split as `dvars_std`. The `go` regressor still flags at VIF 21–35 in stop/go designs — an
open question rather than a settled one.

### A fourth exclusion mechanism you may not expect
`network_glm` has its own *run-level* QA, separate from `qa-motion` (MRIQC IQMs) and
`qa-lev1` (lev1 outliers): `QA FAIL: High junk percentage: >30%` skips a run before the GLM
is fit. In discovery this hit goNogo in 4 of 5 subjects at 30.6–33.3%, just over threshold.
The cell still fits the remaining runs and writes fixed effects, but **exits nonzero** — so
a partial success is indistinguishable from a real failure, which matters because the DAG
uses `--dependency=afterok`. Check `Analysis complete: N/M runs successful` before
concluding a cell failed.

### RIA drops failing cryptically
`external special remote protocol error, unexpectedly received "<EOF>"` means
`git-annex-remote-ora` is not on PATH. It ships in the venv bin.

### DVARS (measured, then removed)
The study's old *proportion of std_dvars > 1.5* criterion is not implemented: MRIQC
publishes mean `dvars_std`, not a proportion. A mean-based substitute was measured on both
cohorts and excluded **nothing** FD had not already caught. `dvars_std` is still recorded
as evidence. Do not reintroduce without per-frame data.

### Silent-failure bugs worth remembering as a *class*
- `network_qa/behavioral.py` imported a deleted module; the `ImportError` branch fired
  every run, blamed pandas, and silently produced 0 accuracy/RT exclusions.
- `array_throttle` leaked into the babs config as an unknown key.
- My own preflight script gave false negatives twice — heredoc `$f` escaping, then a regex
  that only matched hyphens.

Pattern: **verify the thing that decides the outcome, and say what your check does not
cover.** A passing check on the wrong artefact is worse than no check.

## 6. The campaign: how to operate it safely

```bash
network_fmri campaign -- iterate --dry-run     # ALWAYS first — shows every cell it would advance
network_fmri campaign -- iterate --batch 1     # advance ONE cell
network_fmri campaign -- status
```

- **`mechababs configure` REWRITES the ledger.** Never run it on a campaign with in-flight
  cells. Prior additions were done by hand instead.
- **`iterate` advances one transition per cell per tick**, across *all* cohorts. One tick
  will happily scaffold validation's 41-subject fMRIPrep. Use `--batch`.
- **`iterate`'s `fail` action only reports.** Retry with
  `babs submit <project> [--count N | --select sub-XX]`. `--count 1` is the canary idiom.
- **`retire-derivative`** moves a cell to `derivative-attempts/` and resets its ledger
  cell. The archive is **not resumable** — babs bakes absolute RIA paths at init.
- **Flags are baked into the job script at `babs init`.** Changing `bids_app_args` means
  retire + re-scaffold, not just resubmit.
- Scaffolding takes 15 min–2 h depending on Lustre contention. Not hung.

### The vendored patches

Both `code/mechababs` and `code/babs` carry local patches, snapshotted in
[campaign/](campaign/) as `mechababs-local-patches.diff` and `babs-local-patches.diff`.
Currently: per-pipeline `processing_level`; `cluster_resources_override`;
`primary_input`; and babs's `pre_app_commands`. **Refresh the snapshot whenever you patch
either** — a stale diff has already shipped once.

## 7. Where the pipeline actually is

Update this section as work lands.

| Stage | discovery (5) | validation (41) |
|---|---|---|
| Curation, 12 stages through `check` | done | done |
| MRIQC | merged | merged (497/497) |
| `qa-motion` lockfile | 2 exclusions (both behavioural) | 18 (13 rest) |
| fMRIPrep | merged 5/5 | **held** pending canaries |
| `fmriprep-derivs` | done, zips dropped | not run |
| XCP-D | scaffolded; 1-subject canary (sub-s03) in flight, past all three bugs — no errors, one-to-all grouping confirmed | not scaffolded |
| `glm-lev1` | done, 40 cells (36 clean, 4 goNogo partial) | not run |
| `glm-outliers` | done, 1052 rows scored | not run |
| `qa-lev1` | done, 49 exclusions over 15 cells | not run |
| `glm-lev2` | done, 44 contrasts with corrected TFCE maps | not run |

The chain is verified end to end on discovery. Caveat: lev1 ran with the **default
`--confounds-mode full`**, which is still an open decision (below) — that run tests the
machinery, not a chosen configuration, and will need redoing once the arm is picked.

Verified interop, so do not re-test blind: `network_glm`'s `FileFinder` resolves
`space-MNI152NLin2009cAsym_res-2_desc-preproc_bold.nii.gz` (matches real output), and its
`load_exclusions` reads `network_qa`'s `{"_meta", "exclusions"}` lockfile correctly (the
flat set is what gates runs; `exclusions_by_type` groups them under `'exclusions'`).
Exclusions demonstrably bite: `Skipping excluded run: ses-11/run-1`, after which the cell
is tagged `_desc-belowMinRuns` when fewer than `min_runs=2` survive.

### Open decisions (science, not plumbing)
- confounds-mode arm(s) for the NSI experiment (`full` / `no-motion` / `no-cosine` / `task-only`)
- lev2 contrast set and permutation count
- accuracy / RT / omission QC never reinstated after `network_events.qc` was removed;
  recovered code at `~/docs/reference/network_events_qc_removed.py`, must run *post*-clip
- a second XCP-D pass on task-GLM residuals (NSI task-FC arm), downstream of lev1
- `--me-output-echos` is kept by explicit decision: 33% of fMRIPrep output (76 GB/subject)
  that nothing downstream reads

### Capacity
`$SCRATCH` is 100 TB. Validation fMRIPrep is ~230 GB/subject unpacked; with echoes kept
that lands around 85/100 TB once it unpacks. `fmriprep-derivs` drops the fetched zips after
unpacking (`--keep-zips` to opt out) because the output RIA already holds a copy — that was
worth ~950 GB on discovery alone.

## 8. TODO: a bootstrap document

**Not written yet.** Write `docs/BOOTSTRAP.md` that takes a fresh agent or a fresh machine
from nothing to a working environment, so this file can stop describing setup and just
describe *decisions*. It should cover:

1. Clone all four packages to their canonical paths (§2), and warn about the stale
   duplicates that already exist on this host.
2. `ml load devel gcc/12.4.0`, set `UV_PROJECT_ENVIRONMENT` / `UV_CACHE_DIR`, `uv sync
   --frozen` on a compute node, then *verify installed commits against the pins* (§3).
3. Provision git-annex to `$SCRATCH/git-annex`, and check `git-annex-remote-ora` is on PATH.
4. Recreate the campaign: mechababs `bootstrap.sh` + `configure`, `add-dataset` per cohort,
   apply both patch diffs, then `network_fmri shim` once per pipeline. See
   [campaign/README.md](campaign/README.md).
5. Verify: `uv run pytest -q` in each repo, `network_fmri pipeline --cohort discovery
   --print`, `network_fmri campaign -- iterate --dry-run`.

Ideally it is a script plus prose, not prose alone — the shim build was lost precisely
because it lived only in a comment.

## Maintaining this file

This document is only worth reading if it is true. When you change the pipeline:

- **Config or vendored patch** → update §6 and refresh the diffs in `docs/campaign/`.
- **New failure diagnosed** → add it to §5, including the approaches you *ruled out* and
  how you tested them. That is the expensive half and the part nobody can reconstruct.
- **A stage runs or a cohort advances** → update the table in §7.
- **Paths, pins, or env change** → §2 and §3.
- **Something here turns out wrong** → delete it. A confidently stale line costs more than
  a missing one.

Keep it in the same commit as the change.
