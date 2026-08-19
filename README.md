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

Submits all 13 stages as dependent Slurm jobs and returns immediately. Slurm is the DAG
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

Then three stages from `network_events`, a pinned dependency so `uv sync` provisions it:

```bash
network-events create --sourcedata sourcedata --bids-dir .   # _events.tsv
network-events qc     --sourcedata sourcedata --bids-dir .   # -> sourcedata/behavioral_qc/trim_list.json
network-events trim   --bids-dir .                           # -> derivatives/trimmed/
```

`create` applies the −10.43 s onset shift caused by dummy-volume trimming (see
[docs/SCAN-NOTES.md](docs/SCAN-NOTES.md)) — get it wrong and nothing fails validation, only the
first-level models. `qc` writes the truncation record, and `trim` uses it to truncate runs where
the task itself ran short. That is a different operation from `network_fmri trim`, which removes
non-steady-state volumes; this one is driven by the behavioural data.

### 10. Validate again

```bash
network_fmri validate --cohort discovery -- --ignoreWarnings
```

Confirms the tree is still BIDS-compatible after trimming, field-map linking and behavioural
ingestion.

### 11. MRIQC / fMRIPrep

Run through a [mechababs](https://github.com/lobennett/mechababs) campaign pointed at
`<cohort>/bids`, not through this package — BABS owns its own `datalad run` provenance, so
wrapping it again would only nest records. It records the input dataset's id and commit,
continuing the chain.

### Progress and failures

```bash
squeue --me | grep nf-
grep -rh 'failed after' $SCRATCH/network_fmri/logs/*/*.err
```

Resubmit failures with the same `--cohort` plus explicit subjects, so paths stay cohort-scoped:
`network_fmri submit fw-heudiconv --cohort validation --subject s180 s247 --live`.

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
  qa/container.py          pull-and-run Apptainer images, cached
  qa/globalsignal.py       global-signal traces into derivatives/
```

`fw-heudiconv` loads `heuristic.py` **by path**, which is why `curate.py` and `heuristic.py` must
stay in the same package (`fw2bids/`). External tooling is provisioned, not assumed: the validator
is pulled as a container, git-annex by `datalad-installer` — neither needs host setup or an image
to share.
