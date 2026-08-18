# Pipeline

## Why curate and export are separate

Producing BIDS is two steps of the fw-heudiconv engine:

1. **curate** applies the heuristic and writes the BIDS naming into each file's
   `info.BIDS` **on the Flywheel server**. A remote write.
2. **export** downloads the tagged files into a BIDS tree on disk. Reads what
   curate wrote.

`network_fmri curate` runs curate always, export only with `--live --out`. Without
`--live` it is a dry run: intended names are computed, nothing is written.

## What this repo adds

**Session numbering.** The engine's `ReplaceSession` hook receives one raw
accession at a time, so it cannot renumber anything. `curate.py` queries Flywheel,
sorts a subject's sessions by timestamp, and passes `{accession: "NN"}` via
`FWBIDS_SESSION_MAP`. Values are bare (`"01"`); the engine turns `{session}` into
`ses-{session}` itself.

**Cross-subject sessions.** `ReplaceSubject` sees a subject label and never the
session, so it cannot know `s03/22752` belongs to `s10`. Such a session gets its own
curate invocation with `FWBIDS_FORCE_SUBJECT` set — hence `sessions.jobs()` grouping
by `(fw_subject, force_subject)`, and why one subject can produce two engine calls.

**Run numbering needs nothing.** The fork sorts acquisitions by timestamp before
assigning `{seqitem}`, so a repeated task in a session becomes `run-1`/`run-2` in
acquisition order for free.

## Provenance

Every writing step is wrapped in `datalad run`, so history records the command, its
outputs and the exit code. Run messages pin the pipeline commit
(`network_fmri@<sha>`) because `datalad run` records a command string, not the
`heuristic.py` behind it.

`import-subject` is the wrapped entry point the array tasks call: it creates the
subject's dataset and `datalad run`s the payload inside it. `curate` on its own is
unwrapped, for dry runs.

**One dataset per subject.** Many array tasks doing `datalad run` in a single
dataset contend on `.git/index.lock` — the problem BABS exists to solve. Each
`parts/<subject>` is its own dataset instead. The merge records the source dataset
commits in its message (`... from s297@68366e6 ...`), linking the two tiers.

**What is not reproducible.** `curate --live` mutates a shared remote and has no
filesystem output. Re-running re-tags Flywheel rather than reproducing a result, and
its input — Flywheel project state — is not versionable. The record proves which
command and code version ran, not that the result can be replayed. Downstream of the
exported tree everything is replayable.

BABS records its input dataset's id and commit, so pointing mechababs at
`<cohort>/bids` continues the chain.

## Trimming

`trim` removes the first 7 volumes from every BOLD — **all three echoes**, 873 files
for discovery — because fMRIPrep runs with `--dummy-scans 0`. The sidecar's
`NumberOfVolumesDiscardedByUser` records the count and doubles as the idempotency
check, so re-running is safe.

Files are replaced in place by writing a temp file and renaming, which is why each
one is independent and `--jobs` is safe. It also means `datalad run` needs no
`--output`: declaring outputs would unlock them, copying ~100 GB out of the annex
for no reason.

**The untrimmed data stays recoverable without a copy in `derivatives/`.** The
pre-trim commit holds the original annex keys, so any original comes back with:

```bash
git -C <cohort>/bids checkout <pre-trim-sha> -- path/to/_bold.nii.gz
datalad get path/to/_bold.nii.gz
```

That holds only while the old annex objects are retained. A `datalad drop` of
unreferenced content would make the originals unrecoverable unless a sibling has
them — decide that before any cleanup pass.

## Global-signal QA

`global-signal --label <pre-trim|post-trim>` runs `nf-global-signal` from the pinned
`global_signal_plots` and writes `gs_metrics.tsv` plus `gs_traces.pdf` under
`derivatives/global_signal/<label>/`. It reads **echo-2 only** (291 files, 30 GB for
discovery) — that is the tool's default and one echo suffices for a global-signal
trace, so it does not mirror trim's coverage.

`--tr-marker 7` on the pre-trim pass draws a line where the trim will cut, making
the two PDFs directly comparable. The tool is a pure producer: no thresholds, no
exclusion decisions. Those belong to `network_qa`, which consumes `gs_metrics.tsv`.

## Behavioral alignment

`behavior-inventory` audits raw behavioral against the BIDS tree; `behavior-clean`
materialises a 1:1 tree at `sourcedata/behavioral/` inside the cohort dataset,
named `sub-X_ses-YY_task-Z_run-N_beh.csv`. That tree is canonical, so no mapping
table back to the raw tree is kept — it would go stale once the raw tree is archived.
Decisions not recoverable from the result are in
[SCAN-NOTES.md](SCAN-NOTES.md).

Two things are resolved up front so downstream needs no reconciliation manifest:

**Session alignment.** The Nth behavioral session is the Nth BIDS session that
contains functional runs. Five validation subjects have a BIDS session number that
produced no func (anat-only or fully excluded), which shifts every later session by
one — s1445 ses-02, s1326 ses-03, s1391 ses-06, s1258 ses-07. Sequence alignment on
task sets and session-number arithmetic agree independently on each. s321 is an
override: its first visit was split across two scans, so BIDS ses-01+ses-02 together
equal behavioral ses-01.

**Run selection.** Where a session has more than one run of a task, the behavioral
file belongs to the run closest to that task's cohort median volume count; the
others are false starts.

## Layout

```
$SCRATCH/network_fmri/
├── logs/<cohort>/           sbatch .out/.err, subject list
└── <cohort>/
    ├── parts/<subject>/     one dataset per array task; export lands in bids/
    └── bids/                cohort dataset, merged from the parts
        └── derivatives/global_signal/{pre,post}-trim/
```

Logs sit outside the BIDS tree because sbatch `.out`/`.err` inside a dataset trip
bids-validator.
