# Dashboard record index implementation plan

**Goal:** Build a local SQLite index that lets a future dashboard show each subject, scan, pipeline stage, finding, decision, and artifact without creating a second source of truth.

**Architecture:** Collectors read immutable or reviewed files from the DataLad study plus freshly queried MechaBABS/BABS state. They normalize records into one versioned SQLite schema, build a replacement database locally, validate it, and atomically publish it. The database is disposable and never committed.

**Tech stack:** Python 3.13 standard library (`sqlite3`, `json`, `csv`), MechaBABS/BABS CLI, pytest, uv

**Design:** [docs/mechababs.md](../mechababs.md#dashboard-record-contract)

## 1. Define stable records and identity rules

**Files:** `src/network_fmri/records/models.py`, `src/network_fmri/records/entities.py`, `tests/records/test_entities.py`

1. Write failing tests for BIDS paths containing subject, session, task, run, acquisition, echo, suffix, and derivative namespace.
2. Add frozen record types for entities, stage attempts, findings, decisions, and artifacts. Use normalized relative POSIX paths and nullable BIDS entities; never store participant data beyond BIDS IDs.
3. Make entity keys deterministic across raw data, MRIQC, fMRIPrep, scan review, surface review, and behavioral QC.
4. Run `uv run pytest tests/records/test_entities.py` and commit `Define dashboard record identities`.

## 2. Collect durable study evidence

**Files:** `src/network_fmri/records/collect.py`, `tests/records/test_collect.py`

1. Add fixture-driven failing tests for milestone and defacing receipts, validator reports, MRIQC metrics, scan decisions, analysis exclusions, surface review, event errors, and `*_desc-truncation.json` sidecars.
2. Implement one small collector per source. A malformed source must yield a source-specific error instead of a partial silent record.
3. Represent nonmonotonic timing with total, kept, and dropped trial counts and the evidence path. Keep its preprocessing decision separate from its first-level-analysis exclusion.
4. Record DataLad dataset IDs and commits as stage inputs/outputs. Store paths only, never imaging or raw behavioral content.
5. Run `uv run pytest tests/records/test_collect.py` and commit `Collect canonical pipeline evidence`.

## 3. Collect live MechaBABS and BABS state

**Files:** `src/network_fmri/records/mechababs.py`, `tests/records/test_mechababs.py`

1. Write failing tests with recorded command output for planned, submitted, running, merged, failed, cancelled, and intervention-required cells.
2. Implement a runner-injected collector that refreshes status before parsing campaign, app, cell, job ID, timestamps, result branch, output commit, log paths, and error text.
3. Preserve every attempt rather than overwriting failed attempts with the latest state.
4. Run `uv run pytest tests/records/test_mechababs.py` and commit `Index MechaBABS processing attempts`.

## 4. Build and validate SQLite atomically

**Files:** `src/network_fmri/records/database.py`, `src/network_fmri/records/schema.sql`, `tests/records/test_database.py`

1. Write failing tests for schema versioning, foreign keys, uniqueness, full replacement of stale rows, rollback on collector errors, and atomic publication.
2. Create the five approved tables: `entities`, `stage_attempts`, `findings`, `decisions`, and `artifacts`, plus a small metadata table for schema version, build time, study ID, and study commit.
3. Build in a temporary file on the output filesystem, enable foreign-key checks, run `integrity_check`, then replace the requested output path.
4. Reject output paths inside the study or its subdatasets so the cache cannot enter DataLad history.
5. Run `uv run pytest tests/records/test_database.py` and commit `Build the local dashboard index`.

## 5. Add command and machine-readable exports

**Files:** `src/network_fmri/cli.py`, `src/network_fmri/records/__init__.py`, `tests/test_cli.py`, `tests/records/test_exports.py`

1. Add failing tests for `network-fmri records build CONFIG --output PATH` and deterministic TSV/JSON exports of entities, stage attempts, findings, decisions, and artifacts.
2. Implement `records build`; print record counts, source commit, schema version, and output path.
3. Add an export function used by the future dashboard API and operator queries without mutating the study.
4. Run `uv run pytest tests/test_cli.py tests/records/test_exports.py` and commit `Expose dashboard record builds`.

## 6. Verify the index against the pilot study

**Files:** `docs/dashboard-records.md`, `README.md`

1. Document the schema and one build command in a short reference; link it from the README.
2. Run `uv run pytest` and `uv build` locally.
3. Build the index from the completed Sherlock pilot onto node-local or workstation storage.
4. Query one subject end to end and verify raw preparation, MRIQC findings, scan decisions, anatomical processing, surface review, full fMRIPrep, and behavioral truncation evidence against their source files.
5. Confirm the database contains no credentials, names, raw behavioral values, image content, or paths outside the access-controlled study.
6. Commit `Document dashboard records`, rerun the full suite, and push `main`.

