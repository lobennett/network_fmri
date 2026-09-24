# network_fmri instructions

Read [README.md](README.md) for the pipeline, [docs/sherlock.md](docs/sherlock.md) for
cluster operations, [docs/scan-notes.md](docs/scan-notes.md) for scientific decisions,
and [CONTRIBUTING.md](CONTRIBUTING.md) for development.

## Constraints

- Build one BIDS dataset from exactly 46 reviewed subjects.
- Publish participant metadata only from the clean, pinned, deidentified canonical source.
- Derive pilots from the full roster with `--pilot-subject`; give them separate paths.
- Keep `FLYWHEEL_API_TOKEN` in the worker environment and out of commands and logs.
- Keep DICOMs and undefaced anatomy in `$SLURM_TMPDIR` only.
- Let serial stages run explicit `datalad save` milestones. Array workers never save the
  shared raw dataset. MechaBABS/BABS retain their native `datalad run` provenance.
- Require committed scan approval before anatomical processing and committed surface
  approval before full fMRIPrep.
- On Sherlock, use `uv sync --frozen` and `uv run --frozen pytest`; verify pinned sibling
  revisions.
- Before editing, inspect the checkout, branch, worktree, and imported package path.

Update the README for commands and outputs, `sherlock.md` for cluster operations,
`scan-notes.md` for scientific decisions, and `CONTRIBUTING.md` for development. Delete
stale claims; job state belongs in Slurm records, receipts, and DataLad history.
