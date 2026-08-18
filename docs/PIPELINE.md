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

## Layout

```
$SCRATCH/network_fmri/
├── logs/<cohort>/           sbatch .out/.err, subject list
└── <cohort>/
    ├── parts/<subject>/     one dataset per array task; export lands in bids/
    └── bids/                cohort dataset, merged from the parts
```

Logs sit outside the BIDS tree because sbatch `.out`/`.err` inside a dataset trip
bids-validator.
