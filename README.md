# network_fmri

Flywheel → BIDS curation for the r01network study, run as Slurm array jobs on Sherlock with full
DataLad provenance — wrapping a pinned fork of
[fw-heudiconv](https://github.com/lobennett/fw-heudiconv) (`e7509a4`) that does the Flywheel work,
while this repo owns the heuristic, session numbering and job submission.

See [docs/SCAN-NOTES.md](docs/SCAN-NOTES.md) for what gets curated, what is deliberately skipped,
and which source records are wrong.

## Setup

```bash
export UV_PROJECT_ENVIRONMENT="$SCRATCH/venvs/network_fmri"
uv sync
```

The venv goes on `$SCRATCH`, not `$HOME` (15 GB, NFS) — export that variable in every shell or
`uv` builds a second venv at `./.venv`. Flywheel credentials come from
`~/.config/flywheel/user.json` (`fw login <key>`). Run everything on a compute node (`sh_dev`) or
via `sbatch` — never a login node.

## Steps

Cohorts are `discovery` (5 subjects), `validation` (41), `excluded` (11). Output lands under
`$SCRATCH/network_fmri/<cohort>/`.

### The whole chain in one command

```bash
network_fmri pipeline --cohort discovery --live          # see the plan first with --print
```

Submits all 12 stages as dependent Slurm jobs and returns immediately. Slurm is the DAG
engine: each stage carries `--dependency=afterok` on the one before it, so a failure stops the
rest instead of corrupting the tree, and nothing polls or blocks. Resume after fixing a failure
with `--from <stage>`; stage names are in the `--print` output.

```bash
for c in discovery validation excluded; do
  network_fmri pipeline --cohort $c --live
done
```

Deliberately not Make or Snakemake: the chain is a straight line per cohort, Slurm already
provides the dependency graph, and job arrays already provide the fan-out those tools would be
brought in for.

The rest of this section documents each stage individually — useful for re-running one, and for
understanding what the chain does.

### 0. Mark QA-rejected scans (once per Flywheel project)

```bash
network_fmri qa-reject                 # dry run: prints the plan
network_fmri qa-reject --apply --rollback $SCRATCH/qa-reject.json
```

Not part of the chain, because it mutates Flywheel rather than the tree — but it is a
*precondition* of a correct export, so it comes first. With no `--target` it replays
`qa_reject.REJECTS`, the ten anatomicals dropped on MRIQC evidence (which scan won, and why, is
in [docs/SCAN-NOTES.md](docs/SCAN-NOTES.md)). Marking is idempotent, so re-running a
project already in the right state prints the plan and changes nothing.

### 1-2. Curate + export, then merge

```bash
# dry run one subject first: read-only, writes nothing
network_fmri curate --project r01network --subject s10

# curate + export a cohort, one array task per subject
network_fmri submit fw-heudiconv --cohort discovery --live \
  --partition russpold,normal --throttle 3

# per-subject parts -> one cohort dataset (submit it; ~1 TB of rsync)
network_fmri merge --cohort discovery
```

`submit fw-heudiconv` renders one array task per subject; each calls `import-subject`, which
creates `parts/<subject>` as a dataset and records the curate+export command in it (git-annex
installs itself on first use). To redo one subject with its record intact: `network_fmri
import-subject --cohort discovery --subject s10 --live`. To read what was recorded:

```bash
git -C $SCRATCH/network_fmri/discovery/parts/s10 log --oneline
git -C $SCRATCH/network_fmri/discovery/bids log -1 --format=%B   # cmd, exit, outputs
```

`merge` takes hours at full scale and does not submit itself, so submit it directly:

```bash
NF=$SCRATCH/venvs/network_fmri/bin/network_fmri
for c in discovery excluded validation; do
  sbatch -J nf-merge-$c -p russpold,normal -c 2 --mem=8G -t 12:00:00 \
    -o $SCRATCH/network_fmri/logs/$c/merge-%j.out \
    -e $SCRATCH/network_fmri/logs/$c/merge-%j.err \
    --wrap "$NF merge --cohort $c"
done
```

`--live` writes to the **shared** Flywheel project; snapshot it first if you have changed the
heuristic.

### 3-4. Fix sidecars, then validate

```bash
network_fmri fix-sidecars --cohort discovery
network_fmri validate --cohort discovery -- --ignoreWarnings
```

`fix-sidecars` coerces multi-valued DICOM tags into the strings BIDS expects, so the validator can
run at all; `validate` (the official validator, pulled as a container on first use) is quick
enough for an interactive node.

### 5-8. Global-signal QA, trim, link field maps, global-signal QA again

```bash
network_fmri global-signal --cohort discovery --label pre-trim --tr-marker 7
network_fmri trim --cohort discovery --jobs 16
network_fmri b0link --cohort discovery
network_fmri global-signal --cohort discovery --label post-trim
```

`global-signal` writes `derivatives/global_signal/<label>/`; `--tr-marker 7` marks where trim will
cut, so the two PDFs are comparable. `trim` removes 7 dummy volumes from every BOLD in place.
`b0link` stamps `B0FieldIdentifier`/`B0FieldSource` so SDCFlows can group each field map with the
runs it corrects. Chain them so a failure can't trim data with no baseline, or link field maps
before they exist:

```bash
NF=$SCRATCH/venvs/network_fmri/bin/network_fmri; L=$SCRATCH/network_fmri/logs/discovery
GS1=$(sbatch -J nf-gs-pre -p russpold,normal -c 2 --mem=8G -t 06:00:00 \
  -o $L/gs-pre-%j.out -e $L/gs-pre-%j.err \
  --wrap "$NF global-signal --cohort discovery --label pre-trim --tr-marker 7" | grep -oP '\d+$')
TRIM=$(sbatch -J nf-trim -p russpold,normal -c 16 --mem=32G -t 06:00:00 \
  --dependency=afterok:$GS1 -o $L/trim-%j.out -e $L/trim-%j.err \
  --wrap "$NF trim --cohort discovery --jobs 16" | grep -oP '\d+$')
B0LINK=$(sbatch -J nf-b0link -p russpold,normal -c 2 --mem=8G -t 01:00:00 \
  --dependency=afterok:$TRIM -o $L/b0link-%j.out -e $L/b0link-%j.err \
  --wrap "$NF b0link --cohort discovery" | grep -oP '\d+$')
sbatch -J nf-gs-post -p russpold,normal -c 2 --mem=8G -t 06:00:00 \
  --dependency=afterok:$B0LINK -o $L/gs-post-%j.out -e $L/gs-post-%j.err \
  --wrap "$NF global-signal --cohort discovery --label post-trim"
```

Trim is per-file parallel, so give it cores; going wider than one node isn't possible since
parallel array tasks would contend on the dataset's git index.

### 9. Behavioral data, then events

```bash
network_fmri ingest-beh --cohort discovery
```

Copies the cohort's subjects from the canonical behavioural dataset at
`$OAK/.../behavioral_data/canonical` into `sourcedata/sub-X/ses-YY/beh/`.

That dataset is already reconciled: one CSV per BOLD run, named for the run it belongs to.
Working out which run each raw file belonged to needed session alignment and volume-count
comparison, because the raw filenames encode no run index — but that answer only changes if the
*functional* side changes, so it is derived once and frozen there with its own provenance record
and the code that produced it. This repo no longer reads the raw tree, which is being archived.

Then events, from `network_events` — a pinned dependency, so `uv sync` provisions it:

```bash
network-events create --sourcedata sourcedata --bids-dir .   # _events.tsv
```

`create` applies the −10.43 s onset shift caused by dummy-volume trimming (see
[docs/SCAN-NOTES.md](docs/SCAN-NOTES.md)) — get it wrong and nothing fails validation, only the
first-level models. It also applies two truncations: at the first backward onset step, a clock
glitch past which behavioural time no longer maps to the scanner; and at the end of the acquired
scan, since a run aborted at the scanner leaves the behavioural session running and the CSV
then describes trials that were never imaged. Both trial costs go into a sidecar that
`qa-motion` later reads.

### 10-11. Validate, then check

```bash
network_fmri validate --cohort discovery -- --ignoreWarnings
network_fmri check --cohort discovery
```

`validate` confirms the tree is still BIDS-compatible after trimming, field-map linking and
behavioural ingestion. `check` confirms it is *right*: every defect found in this dataset so far
passed validation silently, so each stage's intended outcome is asserted rather than assumed.

| check | asserts | the bug it catches |
|---|---|---|
| `events` | onsets fall inside the acquired scan | aborted run, events describing volumes that don't exist |
| `anat` | at most one T1w and one T2w per subject | a `_qa-reject` mark that didn't take effect |
| `trim` | every BOLD stamped `NumberOfVolumesDiscardedByUser` | an untrimmed run against a −10.43 s shift |
| `b0link` | field maps and their BOLDs carry `B0FieldIdentifier`/`B0FieldSource` | SDCFlows silently pairing nothing |

Run one with `--only events`. Exits non-zero on any problem, so as the chain's last stage it
fails the rebuild rather than handing on a plausible-looking tree.

### 12. MRIQC / fMRIPrep

Run through a [mechababs](https://github.com/lobennett/mechababs) campaign pointed at
`<cohort>/bids`, not through this package — BABS owns its own `datalad run` provenance, so
wrapping it again would only nest records. It records the input dataset's id and commit,
continuing the chain.

MRIQC and fMRIPrep are independent consumers of the same tree and can run **concurrently**:
the only reason to serialise them was choosing between duplicate anatomicals, and that
decision now lives on Flywheel as `_qa-reject` marks (see
[docs/SCAN-NOTES.md](docs/SCAN-NOTES.md)) rather than something a downstream stage consults.

### 13. Motion exclusions

```bash
network_fmri qa-motion --cohort discovery --dependency <fmriprep-job>
```

Compiles [network_qa](https://github.com/lobennett/network_qa)'s `motion` and `behavioral`
generators into a lockfile. FD/DVARS only exist once fMRIPrep has run, which is why this
cannot happen earlier. The lockfile is what `glm-lev1 --exclusions-file` consumes.

The full BIDS tree goes through MRIQC and fMRIPrep unfiltered — no bids-filter reshapes what
they see. Scans that are simply bad are excluded at the Flywheel source instead
(`network_fmri qa-reject`); exclusions that need preprocessing evidence happen here.

### 14-16. GLMs

First and second level fits plus cohort outlier QC, from
[network_glm](https://github.com/lobennett/network_glm) — a pinned dependency, so `uv sync`
provides it and these run in the same venv as everything else.

```bash
# one array task per subject x task
network_fmri glm-lev1 --cohort discovery --base-tasks --results-dir <lev1_out> -- \
    --bids-dir <cohort>/bids --fmriprep-dir <fmriprep> \
    --exclusions-file <lock.json> --residuals

# one array task per contrast, discovered from the lev1 tree
network_fmri glm-lev2 --lev1-dirs <lev1_out> --all --results-dir <lev2_out> -- \
    --num-permutations 5000

# a single job over the finished lev1 maps
network_fmri glm-outliers --results-dir <lev1_out> --
```

These need fMRIPrep derivatives, so they are **not** part of the `pipeline` chain, which
ends at the second `validate`. Chain them onto a finished fMRIPrep with `--dependency`.

Everything after `--` is passed to `network-glm` untouched: this repo owns the fan-out,
the resources and the host modules; `network_glm` owns what the modelling flags mean. Per
level defaults match what it documents — lev1 1 CPU / 64 GB / 2 days, lev2 2 CPUs / 4 GB /
4 h, outliers 2 CPUs / 16 GB / 1 h.

Host modules are loaded only where a run can actually reach them: FreeSurfer for
`mri_surf2surf` when the space is a surface space **and** `--smoothing-fwhm` is given, FSL
for `lev2` volume randomise. Neither tool is bundled anywhere; both are licensed and
resolved from Sherlock's module system.

### 17. Lev1 outliers, then reports

```bash
network_fmri qa-lev1 --cohort discovery --dependency <glm-outliers-job>
```

Adds `network_qa`'s `lev1_outlier` generator, which reads `glm-outliers`' `lev1_outliers.csv`
and gates what enters the second level.

### Progress and failures

```bash
squeue --me | grep nf-
grep -rh 'failed after' $SCRATCH/network_fmri/logs/*/*.err
```

Resubmit failures with the same `--cohort` plus explicit subjects, so paths stay cohort-scoped:
`network_fmri submit fw-heudiconv --cohort validation --subject s180 s247 --live`.

## Reproducing the corrected dataset from scratch

Every data decision is either code in this repo, a pinned dependency commit, or a mark on
Flywheel that `qa-reject` replays — nothing is a hand edit. To rebuild all three cohorts:

```bash
export UV_PROJECT_ENVIRONMENT="$SCRATCH/venvs/network_fmri"
uv sync                                   # never `uv pip install` -- it resolves outside the lock
network_fmri qa-reject --apply             # idempotent; no-ops if the project is already marked
for c in discovery validation excluded; do
  network_fmri pipeline --cohort $c --live
done
```

The chain ends in `check`, so a cohort that reaches the end has asserted its own correctness.
`squeue --me | grep nf-` to watch; a `check` failure names the file and the invariant.

Three pieces of state live outside this repo and must be in place first:

| State | Where | Recreate with |
|---|---|---|
| `_qa-reject` marks + `BIDS.ignore` | the Flywheel project | `network_fmri qa-reject --apply` |
| Reconciled behavioural CSVs | `$OAK/.../behavioral_data/canonical` | DataLad dataset, has its own provenance |
| Dependency versions | `uv.lock` + `[tool.uv.sources]` | `uv sync` |

`curate --live` is the one step that is not reproducible in the strict sense: it mutates a
shared remote and produces no filesystem output, so re-running re-tags Flywheel rather than
reproducing a result. Everything downstream of the exported tree is replayable.

## Design rationale

**Why curate and export are separate.** `curate` applies the heuristic and writes the BIDS naming
into each file's `info.BIDS` on the Flywheel server — a remote write. `export` then downloads what
curate tagged. Without `--live` it is a dry run: names are computed, nothing is written.

**What this repo adds over the fork.** Session numbering: the engine's `ReplaceSession` hook
receives one accession at a time and so cannot renumber anything, so `curate.py` sorts a subject's
sessions by timestamp and passes the map via `FWBIDS_SESSION_MAP`. Cross-subject sessions:
`ReplaceSubject` never sees the session, so one filed under the wrong subject needs its own curate
invocation with `FWBIDS_FORCE_SUBJECT`. Run numbering needs nothing — the fork sorts acquisitions
by timestamp before assigning `{seqitem}`, so a repeated task becomes run-1/run-2 in acquisition
order for free.

**Provenance.** Every writing step is wrapped in `datalad run`, so history records the command,
its outputs and the exit code. One dataset per subject, because many array tasks doing `datalad
run` in one dataset contend on `.git/index.lock` — the problem BABS exists to solve. Run messages
pin the pipeline commit (`network_fmri@<sha>`) since `datalad run` records a command string, not
`heuristic.py` itself. **Not reproducible:** `curate --live` mutates a shared remote with no
filesystem output, so re-running re-tags Flywheel rather than reproducing a result; everything
downstream of the exported tree is replayable.

**Why trim declares no outputs.** `datalad run` unlocks declared outputs, which for annexed NIfTIs
means copying ~100 GB out of the annex. Trim replaces each file by rename instead, so the default
save-everything behaviour suffices — the same reasoning applies to `b0link` and `fix-sidecars`.

## Layout

```
src/network_fmri/
  cli.py                   verb dispatch only
  provenance.py            git-annex provisioning, datalad run
  cohorts.py               rosters, DEFAULT_STAGING, cohort_dataset
  fw2bids/sessions.py      chronological numbering, aliases, session overrides
  fw2bids/acquisitions.py  task rule, allowlist, skip lists
  fw2bids/heuristic.py     acquisition label -> BIDS filename (fw-heudiconv hooks)
  fw2bids/curate.py        the payload one array task runs
  fw2bids/jobs.py          submit / import-subject / merge
  fw2bids/template.sbatch  per-subject Slurm array
  prepare/sidecar.py       shared atomic sidecar read/update
  prepare/trim.py          drop dummy volumes in place, stamp the sidecar
  prepare/b0link.py        link field maps to their BOLD runs
  prepare/sidecars.py      coerce multi-valued DICOM tags to BIDS strings
  behavior/ingest.py       canonical behavioural data -> sourcedata
  qa/validate.py           BIDS validator, via container
  qa/check.py              assert each stage's outcome; the validator can't see these
  qa/container.py          pull-and-run Apptainer images, cached
  qa/globalsignal.py       global-signal traces into derivatives/
  glm/submit.py            fan network_glm's levels out over Slurm arrays
```

External packages are pinned dependencies, not assumed installs: a pinned fork of
fw-heudiconv does the Flywheel work, `global_signal_plots` the traces, `network_events` the
events/QC/truncation stages, and `network_glm` the models. One
`uv sync` provisions the whole pipeline.

`fw-heudiconv` loads `heuristic.py` **by path**, which is why `curate.py` and `heuristic.py` must
stay in the same package (`fw2bids/`). External tooling is provisioned, not assumed: the validator
is pulled as a container, git-annex by `datalad-installer` — neither needs host setup or an image
to share.
