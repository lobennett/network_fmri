# network_fmri — agent instructions

## Read first

**[docs/AGENT-ONBOARDING.md](docs/AGENT-ONBOARDING.md)** before doing anything else. It
carries the context that is not recoverable from the code: which of the duplicate checkouts
is real, which paths are load-bearing, and the failures already diagnosed — including
approaches that were tested and ruled out. Re-deriving those is the main way to waste a
session here.

Then [README.md](README.md) for what the pipeline does, and
[docs/SCAN-NOTES.md](docs/SCAN-NOTES.md) for which scans are excluded where.

## Keep the onboarding doc current

`docs/AGENT-ONBOARDING.md` is only worth reading if it is true. **Update it in the same
commit as the change**, per its own "Maintaining this file" section:

- Config or vendored patch changed → §6, and refresh the diffs in `docs/campaign/`.
- New failure diagnosed → §5, including what you ruled out and how you tested it.
- A stage ran or a cohort advanced → the table in §7.
- Paths, pins, or environment changed → §2 and §3.
- Something there is now wrong → delete it. A confidently stale line costs more than a
  missing one.

This is not optional bookkeeping — the container-shim build step was lost precisely because
it existed only in a comment, and that cost a full debugging cycle.

## Working here

- This host is Sherlock. `/etc/claude-code/CLAUDE.md` is authoritative and overrides
  anything below: no heavy work on the login node, everything through Slurm, verify module
  versions and partitions rather than guessing.
- `uv run pytest`, never bare `pytest` — bare pytest can import an installed wheel instead
  of your working tree. See [CONTRIBUTING.md](CONTRIBUTING.md).
- **Verify the installed commits against the pins before trusting a test run.** The venv
  drifts from `pyproject.toml`; a green suite against a stale install has already produced
  a false "400 passed" here. Recipe in onboarding §3.
- A failed job is not always failed work — `fmriprep-derivs` reported `OUT_OF_MEMORY` after
  its save had committed. Check `git log` in the target dataset before re-running anything
  expensive.
- `network_fmri campaign -- iterate --dry-run` before any `iterate`. One tick advances a
  cell in *every* cohort, including validation's 41 subjects.
- Prefer fixing at the source over compensating downstream, and keep comments to current
  behaviour — history belongs in commit messages.
