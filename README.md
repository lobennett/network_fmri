# network_fmri

Orchestrator / runner for the **r01network** neuroimaging pipeline. It is the
single source of truth for the BIDS dataset — it builds it (and, over time,
downstream derivatives) by **importing and running open-source packages**. It is
deliberately thin: the only study-specific code here is what no package can know
— our Flywheel→BIDS heuristic + acquisition map + curation config.

## What's in the box

The BIDS-curation engine is [`fw-heudiconv`](https://github.com/lobennett/fw-heudiconv)
(our `sherlock-compat` fork, pinned to an immutable commit in `pyproject.toml`).
This repo supplies only the study-specific pieces the engine needs:

| file | role |
|------|------|
| `src/network_fmri/heuristic.py` | the fw-heudiconv heuristic (keys on the Flywheel acquisition label) |
| `src/network_fmri/curation.py` | byte-for-byte acquisition→BIDS map + config-derived aliases/overrides/skips |
| `src/network_fmri/session_map.py` | chronological `ses-01..` renumbering + per-subject curate job plan |
| `src/network_fmri/run.py` | the `fw2bids` runner |
| `config/curation_config.json` | Flywheel block (aliases/overrides/skips) + cohort rosters |

### Companion packages (imported, not vendored)

- [`fw-heudiconv`](https://github.com/lobennett/fw-heudiconv) `@sherlock-compat` — the BIDS-curation engine (multi-echo/fmap file-selection, deterministic run numbering).
- [`network_qa`](https://github.com/lobennett/network_qa) — QA metrics/decisions (short-run flagging via `nf-qa-runs`); its verdicts feed the data-selection layer.

## Documentation

- [`docs/SCAN-NOTES.md`](docs/SCAN-NOTES.md) — curation-layer facts + source-level
  corrections applied on Flywheel, with a dated changelog (the authoritative record
  of what was acquired / relabeled / removed).
- [`docs/DATA-SELECTION.md`](docs/DATA-SELECTION.md) — how exclusions partition
  across `.bidsignore` (invalid only) / `bids-filter-file` (processing selection) /
  `scans.tsv` (why), and why that replaces the legacy `.bidsignore` + symlink-farm.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — developer setup (container + uv venv
  overlay) for extending the package.

## End-to-end walkthrough (Sherlock, from scratch)

### 1. Build the runtime environment (on a compute node)

`$HOME` is NFS-quota'd, so the venv and uv cache live on `$SCRATCH`. numpy/pandas
have no glibc-2.17 (CentOS 7) wheels, so `uv sync` **compiles them from source** —
that must run on a compute node, never the login node:

```bash
sh_dev -c 8 -m 24000 -t 01:00:00 -p normal   # interactive compute shell (-m is MB)
module load uv
export UV_PROJECT_ENVIRONMENT=$SCRATCH/network_fmri_venv
export UV_CACHE_DIR=$SCRATCH/uv_cache
cd $HOME/network_fmri
uv sync                                       # ~7 min first time (compiles numpy/pandas)
```

Everything after this is light and can run on the login node (still with the two
`UV_*` exports set, so uv finds the scratch env).

### 2. Run the offline unit tests

```bash
uv run --no-sync pytest -q                # 17 pure-stdlib tests, no Flywheel needed
```

### 3. Authenticate to Flywheel (once)

```bash
fw login <YOUR_API_KEY>                    # from flywheel.stanford.edu → profile
```

The key is stored under `~/.config/flywheel/`, so it is shared across environments
and only needs doing once.

### 4. Generate BIDS (`fw2bids`)

`fw2bids` is **dry-run (read-only) by default** — it computes the intended BIDS
names without writing to the shared Flywheel project.

```bash
uv run fw2bids discovery                    # dry-run all discovery subjects
uv run fw2bids discovery --subject s03      # dry-run one subject
uv run fw2bids validation
uv run fw2bids excluded
```

### 4b. Write the BIDS directory (`--live --out`)

Materializing BIDS on disk is fw-heudiconv's two-step: `curate` persists the BIDS
naming into each file's `info.BIDS` on the Flywheel project (a **write** — snapshot
first), then `export` downloads the tagged files to a directory. `fw2bids` does
both when given `--live --out`:

```bash
# snapshot the r01network project first, then:
uv run fw2bids discovery --live --out $SCRATCH/bids_staging/discovery   # tag + export
```

Stage on `$SCRATCH` and run bids-validator before writing the canonical Oak tree.
(`--live` tags the shared project; `--out` requires `--live`.)

Curation is a one-time write per cohort; once done, **export is a pure read** and
can be split subject-by-subject with `fw2bids export`:

```bash
uv run fw2bids export validation --subject s286 --out $SCRATCH/parts/s286
```

`export` never writes to Flywheel, retries transient download drops
(`--retries N`), and takes one subject to its own dir — so a whole cohort export
becomes an embarrassingly-parallel Slurm array (one small, independent, retryable
job per subject) instead of a single fragile whole-roster download. Merge the
per-subject parts into one tree afterward (`rsync -a $SCRATCH/parts/*/ $DEST/`).

### 4c. Trim dummy volumes (`trim`)

`fw2bids export` writes un-trimmed BIDS, but fMRIPrep is run with
`--dummy-scans 0`, so the 7 non-steady-state volumes must be removed from the
staged tree first:

```bash
uv run fw2bids trim $SCRATCH/bids_staging/discovery
```

Idempotent (a sidecar `NumberOfVolumesDiscardedByUser` flag skips already-trimmed
files, so it's safe to re-run) and atomic (writes to a temp file, then renames
over the original). `--subjects s10 s19 ...` restricts to those subjects, which
lets a large cohort be sharded across a Slurm array with each task owning a
disjoint set of files (no write races):

```bash
uv run fw2bids trim $SCRATCH/bids_staging/validation --subjects s10 s19
```

### 4d. Make the staged tree a DataLad dataset (`datalad`)

Version-control the staged BIDS tree with DataLad so large NIfTIs are git-annex'd
while text sidecars (`.tsv`/`.json`) stay in plain git (`text2git`):

```bash
fw2bids datalad $SCRATCH/bids_staging/discovery
```

Idempotent — on an already-created dataset it just `datalad save`s (picking up
new/changed files). It shells out to `datalad`, so it needs git-annex; run it on
a **compute node** (`git-annex` is not on the login node):

```bash
module load system git-annex/8.20210622
srun -p normal -c 4 --mem 16G -t 02:00:00 \
  fw2bids datalad $SCRATCH/bids_staging/discovery
```
