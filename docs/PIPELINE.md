# Pipeline

## Why curate and export are separate

Producing BIDS is two steps of the fw-heudiconv engine, and they are not
interchangeable:

1. **curate** applies the heuristic and writes the BIDS naming into each file's
   `info.BIDS` **on the Flywheel server**. A remote write.
2. **export** downloads the tagged files into a BIDS tree on disk. Reads what
   curate wrote.

`network_fmri curate` runs curate always, export only with `--live --out`. Without
`--live` it is a dry run: the engine computes intended names and writes nothing.

## What this repo adds

**Session numbering.** The engine's `ReplaceSession` hook receives one raw
accession at a time, so it cannot renumber anything. `curate.py` queries Flywheel,
sorts a subject's sessions by timestamp, and passes `{accession: "NN"}` via
`FWBIDS_SESSION_MAP`. Values are bare (`"01"`); the engine rewrites `{session}` to
`ses-{session}` itself.

**Cross-subject sessions.** `ReplaceSubject` sees a subject label and never the
session, so it cannot know that `s03/22752` belongs to `s10`. Such a session gets
its own curate invocation with `FWBIDS_FORCE_SUBJECT` set — that is why
`sessions.jobs()` groups by `(fw_subject, force_subject)` and why one subject can
produce two engine calls.

**Run numbering needs nothing.** The fork sorts acquisitions by timestamp before
assigning `{seqitem}`, so a repeated task in one session is `run-1`/`run-2` in
acquisition order for free.

## Provenance

Every writing step is wrapped in `datalad run`, so the dataset history records the
command, its outputs and the exit code. Run messages pin the pipeline commit
(`network_fmri@<sha>`), because `datalad run` records a command string and not the
`heuristic.py` behind it.

**One dataset per subject.** 40+ array tasks doing `datalad run` in a single dataset
contend on `.git/index.lock` — the problem BABS exists to solve. Instead each
`parts/<subject>` is its own dataset and its task records into that. The merge then
records the source dataset commits in its own message
(`... from s297@68366e6 s1320@...`), which is the traceable link between tiers.

**What is not reproducible.** `curate --live` writes `info.BIDS` on a shared remote
and has no filesystem output. Re-running it re-tags Flywheel rather than
reproducing a result, and its input — Flywheel project state — is not versionable.
The record proves which command and which code version ran, not that the result can
be replayed. That is the hard boundary of this pipeline; downstream of the exported
BIDS tree, everything is replayable.

BABS records the input dataset's id and commit, so pointing mechababs at
`<cohort>/bids` continues the chain without extra work.

## Stage layout on disk

```
$SCRATCH/network_fmri/
├── logs/<cohort>/           sbatch .out/.err, subject list
└── <cohort>/
    ├── parts/<subject>/     one dataset per array task; export lands in bids/
    └── bids/                cohort dataset, merged from the parts
```

Merging uses `rsync -aL`. Without `-L` it copies the parts' annex symlinks, giving a
dataset with correct pointers but no content and no sibling to fetch from.

Logs sit outside the BIDS tree on purpose: sbatch `.out`/`.err` inside a dataset
trip bids-validator (`NOT_INCLUDED`, and `EMPTY_FILE` on an empty log).

Each task exports to a directory it owns because the engine `rmtree`s its output
root the moment a file it wants to write already exists. `export()` also wipes its
target first, so a partial export cannot poison a retry.

Merging is idempotent and safe to re-run.

## External tooling

Nothing is assumed to be configured on the host.

- **BIDS validator** — `container.py` pulls `docker://bids/validator:3.0.1` into
  `$SCRATCH/containers` once and reuses it. `--image` uses an existing `.sif`.
- **git-annex** — `dataset.py` provisions it with `datalad-installer` into
  `$SCRATCH/git-annex` on first use. pip cannot ship the binary, and Sherlock's
  `system/git-annex` module (8.x) is below the >= 10.20230126 datalad requires.
