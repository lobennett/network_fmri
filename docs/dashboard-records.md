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

The index stores current campaign paths, commits, and job attempts separately from
history. The completion checklist binds fMRIPrep reports and length/TR checks to
that campaign; an older successful job cannot satisfy a new run.

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

Publication matches restored MD5 annex files to existing SHA256 receipts only after
verifying both hashes. The index retains the checksum alias and commit; anatomical
previews still require the matching defacing receipt.

The separate `network_dashboard` project serves this index locally. It keeps
analysis exclusions independent of preprocessing and surface approval, and shows
whether the source snapshot is stale. Study content and indexes belong outside
GitHub and public hosting.

Publication verifies fMRIPrep archives and records member paths, sizes and CRCs for
the analysis manifests. Archived inputs require extraction; a pending inventory
does not mean the data are missing. The dashboard separates preprocessing,
task-model and timeseries exclusions and previews the recorded behavior and events.

Full file lineage for fieldmap edits, global-signal reports and participant
ingestion remains incomplete. Unrecorded history is shown explicitly. Use the
[surface review procedure](surface-review.md) before approving fMRIPrep; design
matrices and model approval belong to the analysis package.
