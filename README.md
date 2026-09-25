# network_fmri

`network_fmri` creates one reviewed 46-subject BIDS dataset on Sherlock. It builds
the raw dataset, records milestones with DataLad, and hands MRIQC and fMRIPrep to
MechaBABS/BABS.

```text
Flywheel -> defaced BIDS -> behavior/events -> validation
  |-> MRIQC -> scan review -> curation ---------|
  |-> FreeSurfer 8.2.0 -> surface review -------|-> fMRIPrep
```

Copy [workflow.example.toml](config/workflow.example.toml), replace its placeholder
paths, and keep `FLYWHEEL_API_TOKEN` in the environment. Start with one subject:

```bash
uv sync --frozen
uv run --frozen network-fmri pipeline submit workflow.toml --pilot-subject s03
uv run --frozen network-fmri study init workflow.toml --pilot-subject s03
sbatch scripts/run_processing.sh workflow.toml --pilot-subject s03
```

The controller runs MRIQC and FreeSurfer independently, merges their outputs, and
prepares both reviews. FreeSurfer requires an unambiguous anatomical selection;
fMRIPrep requires approved scans and [surfaces](docs/surface-review.md). Restart the
controller after approval or interruption. Failed jobs or changed anatomy require
intervention. Do not start the full sample until the pilot passes.

See [Sherlock operations](docs/sherlock.md) for the command sequence and
[MechaBABS design](docs/mechababs.md) for ownership and review gates. The
[dashboard records](docs/dashboard-records.md) reference describes the disposable
SQLite index used by the dashboard. Fieldmaps import CNI spiral-recon outputs from Flywheel;
a BOLD session without its fieldmap blocks preparation. Surface previews retain
the exact anatomical input hashes recorded by the standalone FreeSurfer adapter.
