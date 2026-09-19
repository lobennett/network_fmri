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
and Slurm logs. It must never share paths with the full run.
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
record records accepted job IDs, so `--resume` can recover from a scheduler failure
without resubmitting completed nodes. Validator logs remain in
`derivatives/bids-validator/`, including validation failures.

Before curation, confirm a durable DataLad annex remote has all raw content. Curation
changes the current BIDS state; its pre-curation commit is recoverable only while annex
content remains available.
