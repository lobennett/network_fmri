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

## Developing the package (container + uv venv overlay)

**Recommended contributor setup** for extending a feature, adding a dependency,
or hacking on the pipeline — it avoids the slow "compile numpy/pandas on a compute
node" cycle above. The technique is the STAMPED *container venv overlay*
([example](https://examples.stamped-principles.org/examples/container-venv-overlay-development/)):
a **pinned container** provides the frozen heavy environment (numpy/pandas +
fw-heudiconv's deps), and a **uv venv created with `--system-site-packages`**
overlays your *editable* checkout on top of it. You edit code on the host, it's
live inside the container immediately, and the container is never rebuilt — so the
environment stays reproducible while iteration is instant.

```bash
# One-time: create the overlay venv INSIDE the container (venv lives on $SCRATCH,
# so it persists across runs; the container image stays immutable).
apptainer exec --cleanenv \
  -B "$PWD":/work -B "$SCRATCH":"$SCRATCH" --pwd /work \
  <base_container.sif> \
  bash -lc '
    uv venv --system-site-packages "$SCRATCH/nf_dev_venv"   # overlay = container site-packages + our editable pkg
    . "$SCRATCH/nf_dev_venv/bin/activate"
    uv pip install -e .                                     # editable: host edits are live in the container
  '

# Each dev run thereafter (no reinstall, no rebuild):
apptainer exec --cleanenv -B "$PWD":/work -B "$SCRATCH":"$SCRATCH" --pwd /work \
  <base_container.sif> \
  bash -lc '. "$SCRATCH/nf_dev_venv/bin/activate" && python -m pytest -q && fw2bids discovery --subject s03'
```

`<base_container.sif>` should be a container carrying the pinned heavy deps
(a scientific-python base, or a purpose-built network_fmri image — building one is
the reproducible ideal). Edit → the editable install reflects it instantly →
re-run in the container. This same overlay pattern applies to the other pipeline
packages (`network_qa`, `network_events`, …) developed against their tool
containers.

Stage on `$SCRATCH` and run bids-validator before writing the canonical Oak tree.
(`--live` tags the shared project; `--out` requires `--live`.)

### 4c. Make the staged tree a DataLad dataset (`datalad`)

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
