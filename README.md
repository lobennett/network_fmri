# network_fmri

`network_fmri` creates one reviewed 46-subject BIDS dataset on Sherlock. It builds
the raw dataset, records milestones with DataLad, and hands MRIQC and fMRIPrep to
MechaBABS/BABS.

```text
Flywheel -> defaced BIDS -> behavior/events -> validation
         -> MRIQC -> scan review -> curation
         -> FreeSurfer 8.2.0 -> surface review -> full fMRIPrep
```

Copy [workflow.example.toml](config/workflow.example.toml), replace its placeholder
paths, and keep `FLYWHEEL_API_TOKEN` in the environment. Start with one subject:

```bash
uv sync --frozen
uv run --frozen network-fmri pipeline submit workflow.toml --pilot-subject s03
uv run --frozen network-fmri study init workflow.toml --pilot-subject s03
sbatch scripts/run_mriqc.sh workflow.toml --pilot-subject s03
```

The controller submits, monitors, merges, extracts MRIQC evidence, and generates scan
decisions, then stops for review. Restart the same command after interruptions;
existing decisions are preserved. Failed jobs or changed evidence require intervention.
After approving scan decisions, curate the raw data and advance the anatomical
stage, follow the [surface review procedure](docs/surface-review.md), and advance full fMRIPrep. Do not start the full
sample until this pilot passes.

See [Sherlock operations](docs/sherlock.md) for the command sequence and
[MechaBABS design](docs/mechababs.md) for ownership and review gates. The
[dashboard records](docs/dashboard-records.md) reference describes the disposable
SQLite index used by the future dashboard.
