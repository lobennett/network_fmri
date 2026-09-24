# File provenance and dashboard implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` for native execution
> or `superpowers:subagent-driven-development` if the user selects delegation.

**Goal:** Trace published imaging and behavioral files from their sources through
processing and review, using a separate interactive dashboard.

**Architecture:** Producers save provenance beside their outputs. `network_fmri`
rebuilds a local SQLite index; a separate `network_dashboard` project serves it
and allowlisted artifacts. DataLad and existing review commands remain authoritative.

**Stack:** existing Python/uv packages; FastAPI read-only service; TypeScript/Vite
frontend with NiiVue; pytest and browser tests. Resolve and lock dependencies during
implementation. UI components stay small; no additional workflow engine or database.

**Spec:** [Approved design](freesurfer-dashboard-design.md).
Dependency for real surface evidence: [FreeSurfer handoff](freesurfer-handoff.md).
Dashboard work can use clearly labeled fixtures while reconstruction is running.

## Global constraints

- One canonical DataLad study; the SQLite index is disposable and outside it.
- File-level lineage covers published pipeline-stage outputs, not every temporary file.
- Historical gaps remain unrecorded; never fabricate links from filename similarity.
- No credentials, identifying DICOM headers, or nondefaced preview content.
- Preprocessing, surface approval, and first-level eligibility remain separate.
- Localhost or authenticated SSH tunnel; no public deployment in this plan.

## Review focus

- One acquisition producing several echoes must preserve all output links (tasks 1–2).
- Identical filenames at different commits must have different artifact versions (task 1).
- Missing annex content and historical gaps must not appear as failed jobs (tasks 3–5).
- Encoded traversal and symlinks must not expose files outside the allowed roots (task 4).
- Stale status or an interrupted index rebuild must be visible without losing the last good index (tasks 3–5).

### Task 1: Define the versioned provenance receipt

**Files:** create `src/network_fmri/records/lineage.py`,
`tests/records/test_lineage.py`; modify `records/models.py`, `records/schema.sql`,
`records/collect.py`, `records/database.py`, `docs/dashboard-records.md`.

**Interface:** `artifact_id(dataset_id: str, path: str, content_id: str) -> str`
hashes a canonical JSON array of those strings. `read_receipt(path: Path) -> dict`
validates schema 1. Receipts contain `artifacts`, `attempts`, and `links` arrays.
Artifact records have `id`, `dataset_id`, `path`, `content_id`, `commit`,
`source_ids`, and `availability`; links contain `input`, `attempt`, `output`,
and `relation`. Attempts carry stage/scope, software, parameters, status, commits,
timestamps, job ID, and logs. Unknown optional historical fields are null.

- [ ] Add identity and schema tests, including:
  ```python
  def test_artifact_versions_are_distinct():
      from network_fmri.records.lineage import artifact_id
      assert artifact_id("ds", "x.nii.gz", "sha256:a") != artifact_id("ds", "x.nii.gz", "sha256:b")
  ```
- [ ] Run `uv run pytest tests/records/test_lineage.py`; confirm missing behavior.
- [ ] Implement canonical serialization and validation; reject dangling links,
  contradictory identities, invalid relative paths, and unknown schema versions.
  Store commits as observations of content identity, so unchanged content across
  commits retains its identity. Add explicit source objects for remote Flywheel files.
- [ ] Extend SQLite with artifact versions, observations, attempt identities, and
  lineage links. Keep existing entity/finding/decision consumers working. Bump the
  index schema and rebuild old caches rather than migrate disposable databases.
- [ ] Rerun all `tests/records`; commit `Define file provenance records`.

### Task 2: Record links where outputs are produced

**Files:** create `network_fw2bids/src/network_fw2bids/provenance.py` and tests;
modify its `planning.py`, `conversion.py`, `_assembly.py`; modify
`network_events/src/network_events/create.py` with producer tests;
modify fmri `prepare/trim.py`, `stages/events.py`, `milestones.py`, and
`pipeline.py`, adding `tests/records/test_producer_lineage.py`.

**Interface:** producers write the schema-1 receipt beside their own outputs.
Do not make `network_fw2bids` or `network_events` depend on `network_fmri`;
shared JSON fixtures test compatibility without a new shared library.

- [ ] Test source acquisition/file IDs survive multi-echo conversion, defacing,
  assembly, and run naming; skipped acquisitions carry reasons and no fabricated
  output. Test behavior-to-events links, trimming input/output hashes, and fieldmap
  sidecar edits. Assert a three-echo fixture has three output links to its source.
- [ ] Run each repository's focused producer tests and verify failures.
- [ ] Persist source identifiers and checksums before temporary DICOM cleanup.
  Link defacing to its existing input/output hashes. Write receipts only after
  successful publication; preserve failures as attempts without claiming outputs.
- [ ] Record canonical behavioral dataset/path/content identity at event conversion.
  Record participant ingestion and global-signal report inputs in fmri stage receipts.
  Account for in-place trimming/JSON edits using distinct before/after versions.
- [ ] Validate shared receipt fixtures in all affected repos; rerun their focused
  suites, commit each producer, and update fmri's locked Git dependencies only to
  tested published commits. Do not reorganize canonical behavior again.

### Task 3: Collect derivatives and attempt history

**Files:** modify `records/collect.py`, `records/mechababs.py`, `records/database.py`,
`records/exports.py`; create `records/derivatives.py`,
`tests/records/test_derivative_lineage.py`; extend database/collector tests.

**Interface:** `collect_derivative_receipts(root: Path) -> list[dict]` reads
receipt records plus verified upstream source metadata. Unknown exact ancestry
is explicitly unrecorded, while subject-level dependencies remain labeled as such.

- [ ] Test archive member identity, repeated attempts, changed output at the same
  path, missing annex content, and ambiguous derivative source metadata. Test a
  failed index build leaves the previous database readable and byte-identical.
- [ ] Run `uv run pytest tests/records`; confirm new contracts fail before changes.
- [ ] Read BABS/DataLad provenance and derivative `Sources`/`RawSources` metadata
  when available. Bind reconstruction inventory and review decisions to the same
  artifact IDs. Persist observed attempt transitions in the canonical study so
  history survives index deletion; deduplicate by campaign/app/job identity.
- [ ] Inventory every published file, including reports and archive members;
  distinguish a processing dependency from a verified exact-input link. Enforce
  complete stage-boundary receipts for new runs and display old missing links.
- [ ] Publish the index atomically with source commits and refresh times. Add
  JSON exports for artifact lineage and attempt history; rerun tests and commit
  `Index file lineage and durable processing history`.

### Task 4: Serve a read-only local dashboard API

**Files in new `network_dashboard`:** `pyproject.toml`, `uv.lock`,
`src/network_dashboard/{api,records,artifacts,cli}.py`,
`tests/{test_records,test_artifacts,test_api}.py`, short `README.md` and CI.

**Interface:** `create_app(index: Path, study: Path)` provides `GET /api/subjects`,
`/api/subjects/{subject}`, `/api/artifacts/{id}/lineage`, `/api/attempts/{id}`,
`/api/artifacts/{id}/content`, and `/api/metadata`. Queries are parameterized;
artifact requests accept opaque IDs, never caller-supplied filesystem paths.

- [ ] Add tests for unknown IDs, missing content, stale indexes, SQL injection,
  encoded traversal, symlink escape, and disallowed original DICOM content. Use
  `client.get('/api/artifacts/unknown/content').status_code == 404` as the basic
  missing-artifact contract; forbidden registered content returns 403.
- [ ] Run `uv run pytest` and confirm API contracts fail before implementation.
- [ ] Implement read-only SQLite access and streamed artifact responses with range
  support. Resolve allowlisted content against verified study/subdataset roots;
  allow annex storage only through validated dataset membership. Require defacing
  evidence for anatomical previews. Serve reports in a sandboxed viewer context.
- [ ] Bind localhost by default, reject unexpected origins/hosts, and supply no
  review-write or arbitrary command endpoints. Document SSH tunneling, index
  refresh, missing-content retrieval by the operator, and startup with uv.
- [ ] Rerun API tests, build the Python package, and commit the service.

### Task 5: Build overview, file lineage, and image views

**Files in `network_dashboard/web`:** `package.json`, lockfile, `src/api.ts`,
`src/main.ts`, `src/views/{subjects,lineage,attempts,review}.ts`,
`src/viewer.ts`, `src/styles.css`, and browser tests under `tests/`.

**Interface:** typed API responses feed focused view modules. URL state identifies
the selected subject/artifact; NiiVue fetches only registered content URLs.

- [ ] Add browser fixtures for a completed stage, failed attempt, pending surface
  review, missing content, and a first-level exclusion with preprocessing retained.
  Assertions must distinguish these states, not merely check that cards render.
- [ ] Run browser tests to establish failing behavior before implementing views.
- [ ] Implement searchable subject/stage overview, artifact input/output traversal,
  attempt logs and reports, and distinct review queues. Show source commits, software,
  settings, reviewer decisions, timestamps, and the index refresh time on demand.
- [ ] Integrate NiiVue for defaced volumes and supported surface formats. Provide
  report links and local Freeview handoff instructions where browser inspection is
  insufficient. No surface acceptance criteria or automatic approvals are invented.
- [ ] Add keyword/BIDS-entity filters and scoped exclusion display. Show historical
  gaps and stale data explicitly; never substitute fixture data when real data fail.
- [ ] Run browser tests, type checks, production build, and narrow/desktop visual
  checks; commit the UI. Adopt a restrained, accessible design with no heavy UI framework.

### Task 6: Verify end-to-end against sub-s03

- [ ] Rebuild the index from the real study and launch the dashboard through a local
  connection. Trace one BOLD file, one surface, and one events file against original
  receipts and commits. Record historical gaps that need a future conversion run.
- [ ] Verify the known timing-related task-model exclusion is visible while that
  scan remains eligible for preprocessing and time-series analyses.
- [ ] Delete/rebuild only the disposable index; confirm identical lineage and
  attempt history. Check content allowlisting against real annex symlinks.
- [ ] Verify pending surface review and unfinished fMRIPrep appear honestly. Full
  success cannot be claimed until the human-approved pilot handoff finishes.
- [ ] Run all changed-repository checks, commit concise operating instructions,
  and push code to feature branches. Keep images, behavioral data, and the live
  index out of GitHub and public hosting.
