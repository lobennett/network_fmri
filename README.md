# network_fmri

Flywheel → BIDS for the r01network study, then the QA gates and models that run off it. One
command per cohort, Slurm as the DAG engine, DataLad provenance on every writing step.

Wraps a pinned fork of [fw-heudiconv](https://github.com/lobennett/fw-heudiconv) (`e7509a4`) that
does the Flywheel work; this repo owns the heuristic, session numbering, job submission and the
exclusion gates. `network_events`, `network_glm` and `network_qa` are pinned dependencies, so one
`uv sync` provisions everything and the same venv runs every stage.

As built: **57 subjects, 590 sessions, 2738 BOLD acquisitions, 2111 `events.tsv`.** Cohorts are
`discovery` (5 subjects), `validation` (41), `excluded` (11). Output lands in
`$SCRATCH/network_fmri/<cohort>/`.

Which scan was dropped and why is in [docs/SCAN-NOTES.md](docs/SCAN-NOTES.md); the summary is
[below](#what-gets-dropped-and-where).

## Setup

```bash
export UV_PROJECT_ENVIRONMENT="$SCRATCH/venvs/network_fmri"
uv sync
```

Export that variable in every shell or `uv` builds a second venv at `./.venv`; it goes on
`$SCRATCH` because `$HOME` is 15 GB of NFS. Use `uv sync`, not `uv pip install` — the latter
resolves outside the lock. Flywheel credentials come from `~/.config/flywheel/user.json`
(`fw login <key>`). Run everything on a compute node or via `sbatch`, never a login node.

## Run it

```bash
network_fmri pipeline --cohort discovery --print    # the plan, no submission
network_fmri pipeline --cohort discovery --live     # submit all 12 stages
network_fmri pipeline --cohort discovery --from trim --live   # resume after a fix
```

Each stage carries `--dependency=afterok` on the one before, so a failure stops the rest instead
of corrupting the tree, and the call returns immediately without polling. The chain ends in
`check`, so a cohort that reaches the end has asserted its own correctness.

Not Make or Snakemake: the chain is a straight line per cohort, and Slurm already provides both
the dependency graph and the array fan-out those tools would be brought in for.

```bash
squeue --me | grep nf-                                    # progress
grep -rh 'failed after' $SCRATCH/network_fmri/logs/*/*.err # failures
```

## Stages

Every stage is also a standalone verb (`network_fmri <stage> --cohort C`), which is how you
intervene at one point without re-running the rest.

| | Stage | What it does |
|---|---|---|
| 1 | `export` | One array task per subject: `curate` writes BIDS names into Flywheel's `info.BIDS`, `export` downloads what it tagged. Each subject becomes its own dataset under `parts/`. |
| 2 | `merge` | rsync the per-subject parts into one cohort tree. Hours at full scale. |
| 3 | `fix-sidecars` | Coerce multi-valued DICOM tags to the strings BIDS wants, so the validator can run at all. |
| 4 | `validate-pre` | Official BIDS validator, in a container pulled on first use. |
| 5 | `gs-pre` | Global-signal traces → `derivatives/global_signal/pre-trim/`. `--tr-marker 7` marks where trim will cut so the two PDFs compare. |
| 6 | `trim` | Drop the first 7 volumes of every BOLD in place; stamp `NumberOfVolumesDiscardedByUser`. Per-file parallel, so give it cores. |
| 7 | `b0link` | Stamp `B0FieldIdentifier`/`B0FieldSource` so SDCFlows pairs each field map with the runs it corrects. |
| 8 | `gs-post` | Global-signal traces again, for comparison. |
| 9 | `ingest-beh` | Copy the cohort's subjects from the canonical behavioural dataset on `$OAK` into `sourcedata/`. |
| 10 | `events` | `network-events create` writes `_events.tsv`: shifts onsets by −10.43 s for the trim, then truncates twice (see below). |
| 11 | `validate-post` | Validator again, after trim, linking and ingestion. |
| 12 | `check` | Assert what the validator can't see. Fails the rebuild rather than handing on a plausible tree. |

`check` asserts one invariant per class of defect this dataset has actually produced — each of
which passed BIDS validation silently. Run one with `--only events`.

| check | asserts | catches |
|---|---|---|
| `events` | onsets fall inside the acquired scan | events describing volumes that were never imaged |
| `anat` | ≤1 T1w and ≤1 T2w per subject | a `_qa-reject` mark that didn't take effect |
| `trim` | every BOLD stamped `NumberOfVolumesDiscardedByUser` | an untrimmed run against a −10.43 s shift |
| `b0link` | field maps and their BOLDs cross-referenced | SDCFlows silently pairing nothing |

### After the chain

These need fMRIPrep derivatives, so they are not part of `pipeline`. Chain them onto a finished
job with `--dependency`.

| Stage | What it does |
|---|---|
| MRIQC / fMRIPrep | Run via a [mechababs](https://github.com/lobennett/mechababs) campaign pointed at `<cohort>/bids`, not through this package — BABS owns its own `datalad run` provenance. They are independent consumers of the same tree and can run **concurrently**. |
| `qa-motion` | Compile `network_qa`'s `motion` + `behavioral` generators into the lockfile `glm-lev1 --exclusions-file` reads. FD/DVARS only exist after fMRIPrep. |
| `glm-lev1` | First-level fits, one array task per subject × task. |
| `glm-lev2` | Second level, one array task per contrast, discovered from the lev1 tree. |
| `glm-outliers` | Cohort outlier QC over the finished lev1 maps. |
| `qa-lev1` | Add `network_qa`'s `lev1_outlier` generator, gating what enters lev2. |

```bash
network_fmri qa-motion --cohort discovery --dependency <fmriprep-job>
network_fmri glm-lev1 --cohort discovery --base-tasks --results-dir <lev1> -- \
    --bids-dir <bids> --fmriprep-dir <fmriprep> --exclusions-file <lock.json> --residuals
network_fmri glm-lev2 --lev1-dirs <lev1> --all --results-dir <lev2> -- --num-permutations 5000
network_fmri glm-outliers --results-dir <lev1>
network_fmri qa-lev1 --cohort discovery --dependency <glm-outliers-job>
```

Everything after `--` passes to `network-glm` untouched: this repo owns the fan-out, the resources
and the host modules; `network_glm` owns what the modelling flags mean. Host modules load only
where a run can reach them — FreeSurfer for `mri_surf2surf` when the space is a surface **and**
`--smoothing-fwhm` is given, FSL for `lev2` volume randomise.

## What gets dropped, and where

Nothing is filtered before preprocessing: the full tree goes through MRIQC and fMRIPrep, and
exclusion happens at the point of use. Bad scans are dropped at the Flywheel source instead, so
they never re-appear on a fresh pull.

| Where | Mechanism | What it drops | Count |
|---|---|---|---|
| `curate` | `acquisitions.py` allowlists | localizers, shims, sbref, the PROMO motion-nav series, a second fieldmap | — |
| `curate` | subject/session skips | the `n01` pilot; `s29/22424`, an fmap-only test session | 2 |
| `curate` | `_qa-reject` label on Flywheel | duplicate anatomicals losing on MRIQC IQMs | **10 scans** |
| `ingest-beh` | false-start rule (volume count vs task median) | the aborted run of a repeated pair gets no CSV | **5 runs** |
| `ingest-beh` | no behavioural file exists anywhere | run gets no `events.tsv`, so no model | **8 runs** |
| `events` | non-monotonic onset truncation | trials after a backward clock jump | varies |
| `events` | scan-length clip | trials the scanner never imaged | **22 runs** |
| `qa-motion` | FD/DVARS thresholds | runs excluded from lev1 | lockfile |
| `qa-lev1` | lev1 outlier statistics | runs excluded from lev2 | lockfile |

`events.tsv` counts: 2111 written, 502 `rest` runs never expect one, and 125 non-rest runs have
none — 7 in discovery and 6 in validation (the false starts and absent files above), plus all 112
in `excluded`, which has no behavioural data at all.

The two `events` truncations are both data-integrity fixes, not policy: a backward
`time_elapsed` jump means behavioural time no longer maps to the scanner, and a run aborted at the
scanner leaves the behavioural session running so the CSV describes trials that do not exist. Each
records its trial cost in a sidecar `qa-motion` later reads, so the decision to *keep or drop* the
surviving run stays with `network_qa`.

## Reproduce from scratch

Every data decision is code here, a pinned dependency commit, or a Flywheel mark `qa-reject`
replays — none is a hand edit.

```bash
export UV_PROJECT_ENVIRONMENT="$SCRATCH/venvs/network_fmri"
uv sync
network_fmri qa-reject --apply          # idempotent; no-ops on an already-marked project
for c in discovery validation excluded; do
  network_fmri pipeline --cohort $c --live
done
```

`qa-reject` with no `--target` replays `qa_reject.REJECTS`, the ten anatomicals dropped on MRIQC
evidence. It is not a pipeline stage because it mutates Flywheel rather than the tree, but it is a
precondition of a correct export.

Three pieces of state live outside this repo:

| State | Where | Recreate with |
|---|---|---|
| `_qa-reject` labels + `BIDS.ignore` flags | the Flywheel project | `network_fmri qa-reject --apply` |
| Reconciled behavioural CSVs | `$OAK/.../behavioral_data/canonical` | a DataLad dataset with its own provenance |
| Dependency versions | `uv.lock` + `[tool.uv.sources]` | `uv sync` |

`curate --live` is the one step not reproducible in the strict sense: it mutates a shared remote
and produces no filesystem output, so re-running re-tags Flywheel rather than reproducing a
result. Everything downstream of the exported tree replays.

## Design notes

**Curate and export are separate** because `curate` is a remote write — it puts BIDS naming into
each file's `info.BIDS` on the Flywheel server — and `export` then downloads what it tagged.
Without `--live` names are computed and nothing is written.

**A reject needs both halves.** Renaming an acquisition stops `curate` tagging it again, but
`curate` only ever *adds* tags, and `export` downloads anything whose `info.BIDS.ignore` is falsy.
So `qa-reject` sets `ignore` as well; the label alone let every rejected scan back into a rebuild.

**What this repo adds over the fork.** Session numbering: the engine's `ReplaceSession` hook sees
one accession at a time and so cannot renumber, so `curate.py` sorts a subject's sessions by
timestamp and passes the map via `FWBIDS_SESSION_MAP`. Cross-subject sessions: `ReplaceSubject`
never sees the session, so one filed under the wrong subject needs its own invocation with
`FWBIDS_FORCE_SUBJECT`. Run numbering needs nothing — the fork sorts acquisitions by timestamp
before assigning `{seqitem}`.

**One dataset per subject** because many array tasks doing `datalad run` in one dataset contend on
`.git/index.lock` — the problem BABS exists to solve. Run messages pin the pipeline commit, since
`datalad run` records a command string rather than `heuristic.py` itself.

**Trim declares no outputs.** `datalad run` unlocks declared outputs, which for annexed NIfTIs
means copying ~100 GB out of the annex. Trim replaces each file by rename instead, so the default
save-everything behaviour suffices — same for `b0link` and `fix-sidecars`.

**`fw-heudiconv` loads `heuristic.py` by path**, which is why it and `curate.py` must stay in the
same package. External tooling is provisioned, not assumed: the validator arrives as a container,
git-annex via `datalad-installer`.

## Layout

```
src/network_fmri/
  cli.py                   verb dispatch only
  pipeline.py              the 12-stage chain, as dependent Slurm jobs
  cohorts.py               rosters, staging paths
  provenance.py            git-annex provisioning, datalad run
  fw2bids/sessions.py      chronological numbering, aliases, session overrides
  fw2bids/acquisitions.py  acquisition label -> task, allowlists, skips
  fw2bids/heuristic.py     label -> BIDS filename (fw-heudiconv hooks)
  fw2bids/curate.py        what one array task runs
  fw2bids/jobs.py          submit / import-subject / merge
  fw2bids/qa_reject.py     mark QA-failed scans on Flywheel; REJECTS is the applied set
  prepare/trim.py          drop dummy volumes in place
  prepare/b0link.py        link field maps to their BOLD runs
  prepare/sidecars.py      coerce multi-valued DICOM tags
  prepare/sidecar.py       shared atomic sidecar read/update
  behavior/ingest.py       canonical behavioural data -> sourcedata
  qa/validate.py           BIDS validator, via container
  qa/check.py              assert each stage's outcome
  qa/exclusions.py         compile network_qa lockfiles (qa-motion, qa-lev1)
  qa/globalsignal.py       global-signal traces
  qa/container.py          pull-and-run Apptainer images, cached
  glm/submit.py            fan network_glm's levels over Slurm arrays
```
