# network_fmri

`network_fmri` creates one reviewed 46-subject BIDS dataset on Sherlock. It builds
the raw dataset, records milestones with DataLad, and hands MRIQC and fMRIPrep to
MechaBABS/BABS.

```text
Flywheel -> defaced BIDS -> behavior/events -> validation
         -> MRIQC -> scan review -> curation
         -> anatomical fMRIPrep/FreeSurfer -> surface review -> full fMRIPrep
```

Copy [workflow.example.toml](config/workflow.example.toml), replace its placeholder
paths, and keep `FLYWHEEL_API_TOKEN` in the environment. Start with one subject:

```bash
uv sync --frozen
uv run --frozen network-fmri pipeline submit workflow.toml --pilot-subject s03
uv run --frozen network-fmri study init workflow.toml --pilot-subject s03
uv run --frozen network-fmri processing advance workflow.toml --stage mriqc
```

Call `processing advance` again after jobs finish until the stage reports complete.
Then generate and approve scan decisions, curate the raw data, advance the anatomical
stage, inspect and approve surfaces, and advance full fMRIPrep. Do not start the full
sample until this pilot passes.

See [Sherlock operations](docs/sherlock.md) for the command sequence and
[MechaBABS design](docs/mechababs.md) for ownership and review gates. The
[dashboard records](docs/dashboard-records.md) reference describes the disposable
SQLite index used by the future dashboard.
