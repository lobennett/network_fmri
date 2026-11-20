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

The BOLD-reference seed audit has two verification layers. Unit tests exercise
mask geometry, coverage, exhaustive inventory, seed derivation and acquisition
filters. A compute-node calibration in the exact fMRIPrep image must also
reproduce known failed and plausible registrations; synthetic overlap tests
cannot establish that the replay matches a campaign. Keep source derivatives
read-only, record the detector checksum, and retain incomplete runs in reports.
Changes to rerun support must test that anatomy staging exposes no functional
cache or old configuration, and verify anatomical/template consistency with the
installed image before submitting repairs.
Test archived-intensity disagreement independently of replay coverage, and
exercise the selection count gate and immutable input/seed plan. The pinned-image
preflight must confirm the full graph retains original anatomical/template
inputs while rebuilding exactly one multi-echo acquisition. Validate against
actual N4 result files after the pilot; successful unit tests alone do not prove
scientific consistency. Keep cluster job state in receipts rather than docs.
Retry extensions must preserve the initial plan and consumed-seed evidence,
reject changed scope and seed collisions, and support one isolated attempt per
parallel worker. Test parent-plan receipt compatibility during integration so
successful original attempts remain usable without accepting unrelated outputs.
