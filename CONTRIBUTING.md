# Contributing

This repository orchestrates a production research pipeline on Sherlock. Keep changes small,
preserve unrelated work, and verify the exact environment that will run on the cluster.

## Before you edit

1. Read [README.md](README.md) and
   [docs/AGENT-ONBOARDING.md](docs/AGENT-ONBOARDING.md).
2. Read [docs/SCAN-NOTES.md](docs/SCAN-NOTES.md) before changing curation, timing,
   exclusions, preprocessing flags, or model inputs.
3. Run `git status --short`; the checkout may contain unrelated local work.
4. Confirm that `network_fmri` imports from the checkout you intend to edit:
   `uv run --frozen python -c "import network_fmri; print(network_fmri.__file__)"`.

## Environment

Run setup and tests on a Sherlock compute node (`sh_dev` or Slurm), not a login node:

```bash
ml load devel gcc/12.4.0
export UV_PROJECT_ENVIRONMENT="$SCRATCH/venvs/network_fmri_dev"
export UV_CACHE_DIR="$SCRATCH/.uv"
uv sync --frozen
```

Use `uv sync`, never `uv pip install`: the latter can resolve outside `uv.lock`.
The GCC module is required because the host's default libstdc++ is too old for current
NumPy wheels.

Before trusting tests, confirm that installed sibling packages match the immutable pins:

```bash
uv run --frozen python - <<'PY'
import json
import tomllib
from importlib.metadata import distribution
from pathlib import Path

pins = tomllib.loads(Path("pyproject.toml").read_text())["tool"]["uv"]["sources"]
for name in ("network_events", "network_glm", "network_qa"):
    direct = distribution(name)._path / "direct_url.json"
    got = json.loads(direct.read_text())["vcs_info"]["commit_id"]
    want = pins[name]["rev"]
    print(f"{name}: {got[:8]} (pin {want})")
    assert got.startswith(want)
PY
```

## Verification

Use the working tree through `uv`; bare `pytest` may import an older installed wheel.

```bash
uv run --frozen pytest -q
ruff check src tests
git diff --check
uv run --frozen network_fmri integration validate --check-installed
uv run --frozen network_fmri pipeline --cohort discovery --print --no-extensions
```

When Ruff is available, format-check files you changed rather than reformatting unrelated
legacy files:

```bash
ruff format --check path/to/changed.py tests/test_changed.py
```

Tests should cover behavior and failure modes. Any change affecting a BIDS tree,
`events.tsv`, exclusion lockfile, model fan-out, dependency edge, or provenance record
needs a focused regression test because many failures otherwise produce plausible outputs.

## Package boundaries

`network_fmri` owns orchestration, Slurm submission, and DataLad recording. Scientific
implementations live in pinned sibling packages:

- `network_events`: events and behavioral timing;
- `network_qa`: exclusion decisions;
- `network_glm`: first- and second-level models.

For a sibling-package change:

1. commit and push the change in that repository;
2. update its `rev` under `[tool.uv.sources]`;
3. run `uv lock`, then `uv sync --frozen`;
4. verify installed revisions and run the full suite.

Do not broaden the pipeline into a general workflow engine. New packages use the bounded,
versioned lifecycle contract in [docs/EXTENDING.md](docs/EXTENDING.md): add a pinned
dependency and a disabled manifest, declare inputs/outputs/resources, activate it
explicitly, and add focused tests. New contributions must not depend on internal
`StageSpec` details or add another execution backend.

## Documentation and commits

Update the focused document in the same commit as the behavior:

- commands and outputs: `README.md`;
- setup, paths, operations, and failures: `docs/AGENT-ONBOARDING.md`;
- scientific or exclusion decisions: `docs/SCAN-NOTES.md`;
- first-level diagnostic evidence: `docs/GLM-DIAGNOSTICS.md`;
- campaign config and patches: `docs/campaign/`;
- lifecycle integration API and manifest schema: `docs/EXTENDING.md`.

Before committing, review the complete diff, confirm the worktree contains no unrelated
files, and record what was tested. Commands should remain idempotent where practical so
operators can resume safely and wrap data-writing work in `datalad run`.
