# Dashboard records

The dashboard index is a disposable SQLite cache built from the canonical DataLad
study and refreshed MechaBABS status. Create it on node-local or workstation storage,
outside the study:

```bash
uv run --frozen network-fmri records build workflow.toml \
  --output "$SLURM_TMPDIR/network-dashboard.sqlite"
```

The command prints JSON containing the output path, schema version, study commit,
and row counts. Five tables support the future dashboard:

| Table | Contents |
|---|---|
| `entities` | normalized BIDS identities and derivative namespace |
| `stage_attempts` | every pipeline and scheduler attempt |
| `findings` | MRIQC, validation, defacing, and behavioral evidence |
| `decisions` | preprocessing, first-level, and surface reviews |
| `artifacts` | reports, receipts, derivatives, and dataset commits |

The index stores BIDS IDs, metrics, decisions, provenance, and access-controlled
relative paths. It does not store credentials, names, image content, or raw behavioral
values. Delete and rebuild it whenever the study changes; never commit it to DataLad.
