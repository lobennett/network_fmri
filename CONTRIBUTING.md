# Contributing

`network_fmri` owns the fixed 46-subject BIDS orchestration graph, Slurm submission,
DataLad milestones, and curation. Conversion belongs in `network_fw2bids`, behavioral
event conversion belongs in `network_events`, and scan-decision evidence belongs in
`network_qa`.

Read [README.md](README.md), [SHERLOCK.md](docs/SHERLOCK.md), and
[SCAN-NOTES.md](docs/SCAN-NOTES.md) before changing behavior. Keep runtime paths
absolute, preserve `FLYWHEEL_API_TOKEN` in the environment, and add focused tests for
failure paths that could affect BIDS data or a milestone receipt.

Use the locked environment on Sherlock:

```bash
uv sync --frozen
uv run --frozen pytest -q
uv build
git diff --check
```

When changing a pinned sibling package, commit and review that change in its repository,
update the immutable revision in `[tool.uv.sources]`, regenerate `uv.lock`, and verify
the installed revision. The relevant pins are `network-fw2bids`, `network-events`,
`network-qa`, and `global-signal-plots`.

Serial stages use explicit DataLad saves after verified output. Array workers must never
save the shared dataset. Preserve the approval gate: any new pre-fMRIPrep condition that
needs human judgment must become scan-decision evidence and block resume until approved.
