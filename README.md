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

Pass `--live` to actually curate Flywheel (**snapshot the project first**):

```bash
uv run fw2bids discovery --live
```

### 5. Verify reproduction vs. the legacy Oak dataset (migration only)

During the cutover we confirm the new generator reproduces the dataset the old
bespoke pipeline wrote to Oak. That check is a **transitional** dev tool
(`dev/replicate_vs_oak.py`, kept local / not committed — it is deleted once the
cohorts are confirmed and Oak is regenerated from here):

```bash
OAK=/oak/stanford/groups/russpold/data/network_grant/bids
uv run python dev/replicate_vs_oak.py discovery  $OAK/discovery
uv run python dev/replicate_vs_oak.py validation $OAK/validation
uv run python dev/replicate_vs_oak.py excluded   $OAK/excluded
```

Each subject prints `PASS` (zero diff) or `DIFF (n)` with the offending paths.
All-`PASS` across the three cohorts = full replication.
