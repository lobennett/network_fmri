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

## Stage layout on disk

```
$SCRATCH/network_fmri/
├── logs/<cohort>/           sbatch .out/.err, subject list
└── <cohort>/
    ├── parts/<subject>/     one export per array task
    └── bids/                merged tree; validated and DataLad-versioned
```

Logs sit outside the BIDS tree on purpose: sbatch `.out`/`.err` inside a dataset
trip bids-validator (`NOT_INCLUDED`, and `EMPTY_FILE` on an empty log).

Each task exports to a directory it owns because the engine `rmtree`s its output
root the moment a file it wants to write already exists. `export()` also wipes its
target first, so a partial export cannot poison a retry.

Merging is `rsync -a` per subject into the cohort tree, and is idempotent.

## External tooling

Nothing is assumed to be configured on the host.

- **BIDS validator** — `container.py` pulls `docker://bids/validator:3.0.1` into
  `$SCRATCH/containers` once and reuses it. `--image` uses an existing `.sif`.
- **git-annex** — `dataset.py` provisions it with `datalad-installer` into
  `$SCRATCH/git-annex` on first use. pip cannot ship the binary, and Sherlock's
  `system/git-annex` module (8.x) is below the >= 10.20230126 datalad requires.
