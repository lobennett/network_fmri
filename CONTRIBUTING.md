# Contributing to network_fmri

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
