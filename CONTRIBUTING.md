# Contributing to network_fmri

## Building the runtime container (`network_fmri.def` → `.sif`)

The pipeline runs through a container that bakes the pinned stack **plus a modern
`git-annex` (≥ 10)** — DataLad 1.6 (used by the `datalad`/`select` stages) rejects
Sherlock's host `git-annex` 8, so the image is how those stages get a compatible
one. `network_fmri.def` is the recipe (astral/uv py3.11 base → git-annex-standalone
10 tarball → `uv pip install .` of the pinned deps); its `%post` self-verifies
git-annex ≥ 10 and that the stack imports.

```bash
# compute node, ~35 min; keeps build tmp/cache on $SCRATCH (HOME is quota-tight)
sbatch -p normal -c 4 --mem 16G -t 01:00:00 --wrap="
  export APPTAINER_TMPDIR=\$SCRATCH/apptainer_tmp APPTAINER_CACHEDIR=\$SCRATCH/apptainer_cache
  cd \$HOME/network_fmri
  apptainer build --fakeroot --force \
    /home/groups/russpold/singularity_images/network_fmri.sif network_fmri.def"
```

Rebuild after bumping any pin in `pyproject.toml` (fw-heudiconv / network_qa /
network_events), since the image freezes those commits. That built image is the
`<base_container.sif>` referenced below and the default `--container` target in the
README quickstart.

## Development setup: container + uv venv overlay

**Recommended setup** for extending a feature, adding a dependency, or otherwise
hacking on the package — it avoids the slow "compile numpy/pandas on a compute
node" cycle from the README's environment step. The technique is the STAMPED
*container venv overlay*
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

## Tests

Offline unit tests need no Flywheel and no heavy deps beyond what the overlay
provides:

```bash
python -m pytest -q
```

Keep changes small and test-first (TDD). The BIDS-naming behavior is pinned by
`config/curation_config.json` + `src/network_fmri/curation.py`; changing curation
output should come with a test and a `docs/SCAN-NOTES.md` changelog entry.
