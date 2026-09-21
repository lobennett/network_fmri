# Sherlock operations

Run this pipeline from a Sherlock allocation with a pinned `uv` environment, a readable
canonical behavioral DataLad checkout, configured annex remotes, and the MRIQC and
fMRIPrep Apptainer images named in the reviewed workflow TOML.

```bash
cd /path/to/network_fmri
uv sync --frozen
export FLYWHEEL_API_TOKEN="$(< /secure/path/flywheel-token)"
uv run --frozen network-fmri pipeline plan /path/to/workflow.toml
uv run --frozen network-fmri pipeline submit /path/to/workflow.toml --dry-run
```

Do not put the token in a TOML file, shell history, Slurm command, receipt, or log.
`network-fw2bids` reads it from the worker environment.

## Anatomical privacy pilot

The workflow's `[pydeface]` section must name the absolute PyDeface 2.1.0/FSL
Apptainer image and its SHA-256 digest. Verify the file on Sherlock before starting:

```bash
sha256sum /absolute/path/to/pydeface-2.1.0-fsl-6.0.7.18.sif
```

The reported digest must match `pydeface.sha256` in the reviewed TOML. The worker uses
`dcm2niix -ba y` to remove identifying BIDS metadata, but that option cannot remove
facial voxels. It then runs the verified PyDeface image before copying anything to a
persistent subject part.

Slurm must supply `$SLURM_TMPDIR`. Flywheel archives, extracted DICOMs, undefaced
NIfTIs, and PyDeface temporary files may exist only below that node-local directory.
They are removed before publication. Do not use persistent scratch, DataLad, or
`sourcedata` for original anatomy.

Before the full run, create a copy of the reviewed TOML with a separate pilot BIDS,
parts, work, and log location. Keep its reviewed 46-subject roster; the pilot selector
derives one allowed subject from that roster:

```bash
uv run --frozen network-fmri pipeline submit /path/to/pilot-workflow.toml \
  --pilot-subject s03 --dry-run
uv run --frozen network-fmri pipeline submit /path/to/pilot-workflow.toml \
  --pilot-subject s03
```

Inspect the pilot's Flywheel access, container binds, DataLad saves, validator output,
and Slurm logs. It must never share paths with the full run. Confirm that every T1w and
T2w has a receipt entry and a `Defaced: true` sidecar:

```bash
jq . /path/to/pilot-bids/code/network_fw2bids/defacing/sub-s03.json
find /path/to/pilot-bids/sub-s03 -path '*/anat/*_T?w.json' -print -exec jq '.Defaced' {} \;
```

Open the pilot T1w and T2w images and review them visually before submitting the
46-subject run. A matching receipt, checksum, and sidecar confirm the automated privacy
boundary, but they do not establish that facial anatomy was adequately removed.
The pilot selector is persisted in its submission record; include the same selector on
the approval resume command:

```bash
uv run --frozen network-fmri pipeline submit /path/to/pilot-workflow.toml \
  --pilot-subject s03 --resume
```

For the full run, submit the initial graph after that pilot:

```bash
uv run --frozen network-fmri pipeline submit /path/to/workflow.toml
uv run --frozen network-fmri pipeline status /path/to/workflow.toml
```

The first graph ends at `scan-decisions-generated`. Inspect MRIQC evidence and
`code/network_fmri/scan_decisions.tsv`, resolve every `review` decision, then seal it:

```bash
uv run --frozen network-fmri decisions validate /path/to/bids
uv run --frozen network-fmri pipeline submit /path/to/workflow.toml --resume
```

Use the configured log directory and `squeue --me` to monitor Slurm. The submission
record records accepted job IDs, while `--resume` verifies serial milestones, array
receipts, and Slurm completion before recovering from a scheduler failure. Validator logs remain in
`derivatives/bids-validator/`, including validation failures.

Before curation, confirm a durable DataLad annex remote has all raw content. Curation
changes the current BIDS state; its pre-curation commit is recoverable only while annex
content remains available.
