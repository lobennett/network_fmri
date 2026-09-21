# Sherlock operations

## Prepare

Use a Sherlock allocation, a pinned `uv` environment, a readable behavioral DataLad
checkout, configured annex remotes, and the containers named in the reviewed TOML.

```bash
cd /path/to/network_fmri
uv sync --frozen
export FLYWHEEL_API_TOKEN="$(< /secure/path/flywheel-token)"
uv run --frozen network-fmri pipeline plan /path/to/workflow.toml
uv run --frozen network-fmri pipeline submit /path/to/workflow.toml --dry-run
```

Keep the token out of TOML files, shell history, Slurm commands, receipts, and logs.

## Run the privacy pilot

Verify that the PyDeface image digest matches `[pydeface]`:

```bash
sha256sum /absolute/path/to/pydeface-2.1.0-fsl-6.0.7.18.sif
```

Slurm must provide `$SLURM_TMPDIR`. Flywheel archives, DICOMs, undefaced NIfTIs, and
PyDeface temporary files may exist only there. Persistent subject parts contain defaced
anatomy only.

Copy the reviewed TOML, give the pilot separate BIDS, parts, work, and log paths, keep
the 46-subject roster, and select one subject:

```bash
uv run --frozen network-fmri pipeline submit /path/to/pilot.toml \
  --pilot-subject s03 --dry-run
uv run --frozen network-fmri pipeline submit /path/to/pilot.toml \
  --pilot-subject s03
```

Check Flywheel access, container binds, DataLad saves, validator output, Slurm logs,
defacing receipts, and sidecars:

```bash
jq . /path/to/pilot-bids/code/network_fw2bids/defacing/sub-s03.json
find /path/to/pilot-bids/sub-s03 -path '*/anat/*_T?w.json' \
  -print -exec jq '.Defaced' {} \;
```

View every pilot T1w and T2w image. Automated checks cannot judge defacing quality.

## Submit and resume

```bash
uv run --frozen network-fmri pipeline submit /path/to/workflow.toml
uv run --frozen network-fmri pipeline status /path/to/workflow.toml
```

The first graph stops at `scan-decisions-generated`. Review MRIQC evidence, resolve all
`review` rows in `code/network_fmri/scan_decisions.tsv`, then continue:

```bash
uv run --frozen network-fmri decisions validate /path/to/bids
uv run --frozen network-fmri pipeline submit /path/to/workflow.toml --resume
```

Repeat `--pilot-subject s03` when resuming a pilot. Use the configured log directory and
`squeue --me` to monitor jobs. `--resume` verifies milestones, array receipts, and Slurm
completion before submitting missing stages. Validator reports remain in
`derivatives/bids-validator/`, including failed runs.

Before curation, confirm that a durable DataLad annex remote has all raw content. The
pre-curation state depends on that remote.
