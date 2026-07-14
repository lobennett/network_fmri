# network_fmri

Orchestrator / runner for the **r01network** neuroimaging pipeline. It builds the
full BIDS datasets (and, over time, downstream derivatives) by **importing and
running open-source packages** — it is deliberately thin. The only study-specific
code here is what no package can know: our Flywheel→BIDS heuristic + acquisition
map, and the curation config.

## Flywheel → BIDS (`fw2bids`)

The BIDS-curation engine is [`fw-heudiconv`](https://github.com/lobennett/fw-heudiconv)
(our `sherlock-compat` fork, pinned to an immutable commit in `pyproject.toml`).
This repo supplies the heuristic (`src/network_fmri/heuristic.py`), the byte-for-byte
acquisition→BIDS map (`curation.py`), the chronological session renumbering
(`session_map.py`), and the acceptance test vs. the canonical Oak tree
(`validate.py`). Curation config (subject aliases, session overrides, cohort
rosters) is `config/curation_config.json`.

```bash
fw2bids discovery                                   # dry-run all discovery subjects
fw2bids discovery --subject s03 --diff-oak /oak/stanford/groups/russpold/data/network_grant/bids/discovery
fw2bids validation --diff-oak /oak/.../bids/validation
```

`fw2bids` is **dry-run (read-only) by default** — it computes intended BIDS names
without writing to the shared Flywheel project. `--diff-oak` diffs the predicted
tree against Oak (zero diff = replication). Pass `--live` to actually curate
Flywheel (snapshot the project first).

## Setup (Sherlock)

`$HOME` is NFS-quota'd — keep the venv and uv cache on `$SCRATCH`:

```bash
module load uv
export UV_PROJECT_ENVIRONMENT=$SCRATCH/network_fmri_venv
export UV_CACHE_DIR=$SCRATCH/uv_cache
uv sync
uv run pytest            # offline unit tests
```

Flywheel access requires `fw login` (an API key) once per environment.
