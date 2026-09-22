# network_fmri instructions

Read [README.md](README.md) for the pipeline, [docs/SHERLOCK.md](docs/SHERLOCK.md) for
cluster operations, [docs/SCAN-NOTES.md](docs/SCAN-NOTES.md) for scientific decisions,
and [CONTRIBUTING.md](CONTRIBUTING.md) for development.

## Constraints

- Build one BIDS dataset from exactly 46 reviewed subjects.
- Derive pilots from the full roster with `--pilot-subject`; give them separate paths.
- Keep `FLYWHEEL_API_TOKEN` in the worker environment and out of commands and logs.
- Keep DICOMs and undefaced anatomy in `$SLURM_TMPDIR` only.
- Let serial stages run explicit `datalad save` milestones. Array workers never save the
  shared dataset, and no stage uses `datalad run`.
- Stop at `scan-decisions-generated` until the reviewed manifest and approval receipt
  are committed.
- On Sherlock, use `uv sync --frozen` and `uv run --frozen pytest`; verify pinned sibling
  revisions.
- Before editing, inspect the checkout, branch, worktree, and imported package path.

Update the README for commands and outputs, `SHERLOCK.md` for cluster operations,
`SCAN-NOTES.md` for scientific decisions, and `CONTRIBUTING.md` for development. Delete
stale claims; job state belongs in Slurm records, receipts, and DataLad history.
