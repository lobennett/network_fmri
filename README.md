# network_fmri

`network_fmri` builds and preprocesses one reviewed 46-subject BIDS dataset on Slurm.
Sibling packages own Flywheel conversion, behavioral events, scan evidence, and global
signal analysis.

```text
Flywheel → BIDS → pinned behavioral subdatasets → participant metadata
→ global signal (pretrim)
→ trim 7 volumes → events → global signal (posttrim) → B0 links
→ validator → MRIQC → human-approved scan decisions
→ curation → validator → fMRIPrep
```

Serial milestones use `datalad save` and write receipts under
`code/network_fmri/milestones/`. Array workers never save the shared dataset.

## Configure

Copy [workflow.example.toml](config/workflow.example.toml) and replace every placeholder
with an absolute Sherlock path. The roster must contain exactly 46 unique subjects. Keep
`FLYWHEEL_API_TOKEN` only in the environment.

```bash
uv sync --frozen
uv run --frozen network-fmri pipeline plan /path/to/workflow.toml
uv run --frozen network-fmri pipeline submit /path/to/workflow.toml --dry-run
```

The participant source must be a clean Git/DataLad repository at the configured commit
with deidentified, BIDS-ready `participants.tsv` and `participants.json`. Review the
configured paths, roster, source revisions, and containers; the dry run prints the
commands, dependencies, and Slurm resources.

## Pilot and submit

Copy the reviewed configuration for a one-subject pilot. Keep the 46-subject roster,
give the pilot separate BIDS, parts, work, and log paths, and select one roster member:

```bash
uv run --frozen network-fmri pipeline submit /path/to/pilot.toml \
  --pilot-subject s03 --dry-run
uv run --frozen network-fmri pipeline submit /path/to/pilot.toml \
  --pilot-subject s03
```

The pinned PyDeface container must match the version and SHA-256 digest in `[pydeface]`.
DICOMs, undefaced NIfTIs, and defacing intermediates may exist only in `$SLURM_TMPDIR`.
Inspect the receipt, confirm each anatomical sidecar contains `Defaced: true`, and view
the T1w and T2w images before running all subjects:

```bash
jq . /path/to/pilot-bids/code/network_fw2bids/defacing/sub-s03.json
find /path/to/pilot-bids/sub-s03 -path '*/anat/*_T?w.json' \
  -print -exec jq '.Defaced' {} \;
```

Submit the full configuration after the pilot passes:

```bash
uv run --frozen network-fmri pipeline submit /path/to/workflow.toml
```

The initial graph stops at `scan-decisions-generated`. Resolve every review row in
`code/network_fmri/scan_decisions.tsv`, then validate and resume:

```bash
uv run --frozen network-fmri decisions validate /path/to/bids
uv run --frozen network-fmri pipeline submit /path/to/workflow.toml --resume
```

`--resume` checks approval and completed stages before submitting missing work. Repeat
`--pilot-subject s03` when resuming a pilot.

## Outputs and recovery

Configure a durable DataLad annex remote before curation. The pre-curation state is
recoverable only while that remote retains the annexed content. Do not run serial stages
against the same dataset concurrently.

Validator reports are stored in `derivatives/bids-validator/`; global-signal outputs in
`derivatives/gs-pretrim/` and `derivatives/gs-posttrim/`; MRIQC and fMRIPrep each use a
separate derivative dataset.

See the [canonical dataset design](docs/canonical-dataset.md),
[Sherlock operations](docs/sherlock.md), and
[scan notes](docs/scan-notes.md) for durable design and operating details.
