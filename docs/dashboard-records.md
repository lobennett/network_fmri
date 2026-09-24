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
| `stage_attempts` | milestone receipts and current MechaBABS job states |
| `findings` | MRIQC metrics, timing metrics, and event conversion errors |
| `decisions` | preprocessing, first-level, and surface reviews |
| `artifacts` | reports, receipts, derivatives, and dataset commits |

The index stores BIDS IDs, metrics, decisions, provenance, and access-controlled
relative paths. It does not store credentials, names, image content, or raw behavioral
values. Delete and rebuild it whenever the study changes; never commit it to DataLad.

Milestone receipts are read from both the wrapper study and its raw subdataset;
defacing receipts and validator reports remain linked as artifacts. Historical
scheduler retries remain in BABS provenance rather than the current jobs table.

Behavioral evidence uses `network_events` sidecars under
`sourcedata/events_qc/`: the six trial-retention and scan-duration metrics are
stored with their producer field names. A sidecar is evidence, even when no trials
were dropped; its presence never creates an exclusion. Conversion errors come from
`conversion_errors.tsv`; the index records affected runs and the evidence path,
without copying source paths or error messages.

Reviewed analysis exclusions come from `code/network_fmri/analysis_exclusions.tsv`
in the wrapper, falling back to the raw dataset when the wrapper has no copy.
Each row supplies `subject`, `session`, `task`, `run`, `analysis_scope`,
`reason_code`, `reason_detail`, `reviewer`, and `reviewed_at`. The index preserves
that analysis scope and records the explicit exclusion independently of preprocessing
and surface decisions.
