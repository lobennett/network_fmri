# network_fmri

`network_fmri` builds and preprocesses one reviewed 46-subject BIDS dataset. It
orchestrates pinned sibling packages and Slurm jobs; it does not contain Flywheel
conversion rules, behavioral remapping rules, GLM code, or a second workflow engine.

```text
Flywheel parts → one BIDS dataset → canonical behavioral sourcedata
→ global signal (pretrim) → trim 7 volumes → events → global signal (posttrim)
→ B0 links → validator → MRIQC → scan decisions → human approval
→ curation → validator → fMRIPrep
```

Each serial milestone is committed with `datalad save` and receives a receipt in
`code/network_fmri/milestones/`. Array workers never write the shared DataLad history.
The receipt records package/container identities, scheduler job IDs, inputs, outputs,
and validation evidence without credentials.

## Configure and inspect

Copy [workflow.example.toml](config/workflow.example.toml) to a reviewed location and
replace every placeholder with an absolute Sherlock path. The roster file must contain
exactly the 46 selected subject IDs. Keep `FLYWHEEL_API_TOKEN` only in the environment.

```bash
uv sync --frozen
uv run --frozen network-fmri pipeline plan /path/to/workflow.toml
uv run --frozen network-fmri pipeline submit /path/to/workflow.toml --dry-run
```

The dry run has no filesystem or scheduler effects. Review the printed commands,
container paths, dataset path, roster, and Slurm resources before the pilot.

## Pilot, approval, and resume

Run a small operational pilot in a separate configuration and dataset location before
the full 46-subject submission. Confirm Flywheel access, container binds, DataLad annex
content, validator diagnostics, and Slurm logs.

The initial submission ends at `scan-decisions-generated`. Inspect and resolve every row
requiring review in `code/network_fmri/scan_decisions.tsv`, then seal it with:

```bash
uv run --frozen network-fmri decisions validate /path/to/bids
uv run --frozen network-fmri pipeline submit /path/to/workflow.toml --resume
```

Resume verifies the committed approval receipt before curation. If submission partly
fails, its record remains under the configured log directory; correct the cause and use
`--resume` to submit only missing stages.

## DataLad and diagnostics

Configure a durable annex remote and verify it retains content before curation. The
pre-curation commit remains recoverable only while the annexed content is available from
a remote. Do not execute multiple serial stages against the same dataset concurrently.

Validator reports and logs are written under `derivatives/bids-validator/`, including
failed runs. Global-signal outputs live in `derivatives/gs-pretrim/` and
`derivatives/gs-posttrim/`; MRIQC and fMRIPrep use their own derivative datasets.

The scientific source history and known exceptions are in
[SCAN-NOTES.md](docs/SCAN-NOTES.md). Sherlock operations are in
[SHERLOCK.md](docs/SHERLOCK.md).
