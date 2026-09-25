# Dashboard records

DataLad holds the evidence; SQLite is a disposable, read-only dashboard index.

```bash
uv run --frozen network-fmri records build workflow.toml \
  --output /path/outside/study/records.sqlite
```

`processing run` refreshes the index at
`<study-parent>/.network-fmri-cache/<study-name>/records.sqlite` on each poll and
at review boundaries. An unsuccessful rebuild preserves the last good index and
its timestamp. The dashboard marks snapshots older than 15 minutes as stale.

| Records | Contents |
|---|---|
| Entities and findings | BIDS IDs, MRIQC metrics, behavioral timing evidence |
| Decisions | Separate preprocessing, analysis exclusion, and surface review scopes |
| Stage attempts | Milestones, current jobs, and saved job observations |
| Artifact versions | Dataset ID, relative path, and content hash |
| Lineage | Recorded input/output links and processing details |

The controller saves observed scheduler transitions under
`code/network_fmri/processing-history/` using DataLad. Rebuilding SQLite preserves
this history. Transitions before observation began remain unknown.

Flywheel audits use the newest capture time across the raw and study datasets.
Selections recorded during conversion take precedence over later inventory audits.

Current file lineage covers conversion, defacing, trimming, and events when their
producer receipts exist. Trimming stores before/after image hashes in the sidecar's
`NetworkFMRITrim` object as part of the existing recoverable file publication.
Unavailable annex objects remain identifiable without downloading them. Historical
files without receipts have unrecorded ancestry; filename similarity creates no link.
Missing images retain SHA256 or MD5 identities from their annex keys so the dashboard
can verify downloads. New conversion, trimming, and event receipts record software
versions; older receipts are not assigned today's versions retroactively.

The separate `network_dashboard` project serves this index locally. It keeps
analysis exclusions independent of preprocessing and surface approval, and shows
whether the source snapshot is stale. Study content and indexes belong outside
GitHub and public hosting.

Still pending: full provenance for fieldmap edits, global-signal reports,
participant ingestion, and archived derivative members; real-study acceptance on
Sherlock. Use the [surface review procedure](surface-review.md) for ribbon inspection,
surface checks, and correction provenance before approving fMRIPrep.

Later: add task-level event timelines and design-matrix views tied to the exact
events file, model specification, and analysis exclusions. Reconstruction and
preprocessing review take priority.
