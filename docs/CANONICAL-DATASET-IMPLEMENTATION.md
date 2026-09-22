# Canonical dataset implementation plan

**Goal:** Make one reproducible DataLad-managed BIDS dataset reference both finalized
behavioral repositories and generate BIDS events from the in-scanner source.

**Architecture:** The root BIDS repository installs each pinned behavioral repository as
a DataLad subdataset. The behavioral stage verifies source commits before changing the
root dataset, refuses conflicts, and supports a matching rerun. The events stage reads
the fixed in-scanner subdataset path.

## Tasks

- [x] Extend workflow configuration with `behavior.in_scanner` and
  `behavior.out_of_scanner`, each containing an absolute source and full commit hash.
- [x] Replace copied behavioral ingestion with pinned DataLad subdataset
  installation and matching-rerun validation.
- [x] Point behavioral audit and event generation at
  `sourcedata/behavioral/in_scanner`.
- [x] Update milestone provenance, examples, and concise package documentation.
- [x] Update `network_events` documentation for the canonical in-scanner path.
- [x] Run focused red/green tests, full test suites, builds, and repository checks.
