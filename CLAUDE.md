# network_fmri — working instructions

## Start here

Read [README.md](README.md) for the fixed pipeline and
[docs/SHERLOCK.md](docs/SHERLOCK.md) for cluster operations. Read
[docs/SCAN-NOTES.md](docs/SCAN-NOTES.md) before changing scientific decisions or source
data handling, and [CONTRIBUTING.md](CONTRIBUTING.md) before changing dependencies or
development workflow.

## Non-negotiables

- The reviewed runtime configuration always names exactly 46 unique subjects and builds
  one BIDS dataset. Do not add discovery, validation, or excluded cohort layouts.
- A one-subject operational pilot must start from a copied, validated 46-subject TOML,
  use distinct pilot paths, and select one roster member with `--pilot-subject`.
- Keep `FLYWHEEL_API_TOKEN` in the worker environment. Never put it in a config, command,
  receipt, or log.
- Array workers never save the shared DataLad dataset. Serial stages use explicit
  `datalad save` milestones; do not wrap stages in DataLad command recording.
- The initial graph stops at `scan-decisions-generated`. Resume only after the reviewed
  manifest is sealed and its approval receipt is committed.
- Do not assume a checkout location. Inspect `git status`, the branch/worktree, and the
  imported `network_fmri.__file__` before editing or testing.
- On Sherlock, use a scratch environment with `uv sync --frozen` and
  `uv run --frozen pytest`. Verify installed sibling revisions against `pyproject.toml`.

## Keep documentation current

| Change | Update |
|---|---|
| User-facing command, stage, or output | `README.md` |
| Sherlock setup, pilot, submission, or recovery | `docs/SHERLOCK.md` |
| Scan, behavioral, or preprocessing decision | `docs/SCAN-NOTES.md` |
| Dependency or development workflow | `CONTRIBUTING.md` |

Delete stale claims. Live job state belongs in Slurm, submission records, milestone
receipts, and DataLad history.
