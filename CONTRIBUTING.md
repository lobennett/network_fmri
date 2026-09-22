# Contributing

`network_fmri` owns orchestration, Slurm submission, DataLad milestones, and curation.
Conversion belongs in `network_fw2bids`, events in `network_events`, and scan evidence
in `network_qa`.

Read the [README](README.md), [Sherlock guide](docs/sherlock.md), and
[scan notes](docs/scan-notes.md) before changing behavior. Keep paths absolute and the
Flywheel token in the environment. Test failures that could corrupt BIDS data or a
milestone receipt.

```bash
uv sync --frozen
uv run --frozen pytest -q
uv build
git diff --check
```

When changing a sibling package, commit it first, update its immutable revision in
`[tool.uv.sources]`, regenerate `uv.lock`, and verify the installed revision.

Serial stages save verified outputs with DataLad; array workers never save the shared
dataset. Any new condition requiring human judgment must block curation through the
scan-decision approval gate.
