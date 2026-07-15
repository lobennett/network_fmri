# Scan Notes — r01network curation

Curation-layer facts for the Flywheel→BIDS step: what was acquired, what was
corrected at the **source** (Flywheel), and the study-specific identity remaps the
generator applies. This file reflects the **current** state of the archive; every
change is recorded in the [Changelog](#changelog) at the bottom.

Processing / quality exclusions (missing behavioral, short-but-valid runs, QC
failures, superseded anatomicals) are **not** here — those are per-scan quality
calls that live in `scans.tsv` and the pipeline `bids-filter-file`s. See
[DATA-SELECTION.md](DATA-SELECTION.md) for why the two are kept on separate channels.

## Source-level corrections (applied on Flywheel)

- **s43 / ses-08 — aborted `directedForgetting` run removed.** A single-volume
  (`dim4=1`) multi-echo acquisition. A `dim4=1` file is not a valid `_bold` (a bold
  is by definition a 4-D time series), so it was **deleted from Flywheel** rather
  than hidden from the validator. It is not curated into BIDS. This scan note is
  now its only record.
- **s29 / ses-01 — `cuedTS` → `spatialTS` relabel.** The participant performed
  spatialTS, but the acquisition had been named under the cuedTS protocol. The
  Flywheel acquisition label `task-cuedTS_bold` (acq `5faed68e…`, session accession
  `20201113`) was **renamed to `task-spatialTS_bold`** so the BIDS `task-` entity
  describes the task performed. It now pairs with the existing spatialTS behavioral
  → recovered run. Supersedes the prior "cuedTS BOLD irreconcilable" call.
- **s480 / ses-03 — aborted `goNogo` and `nBack` runs removed; re-runs promoted.**
  Each task was acquired twice this session: a scanner false-start (`goNogo`
  `dim4=1`, `nBack` `dim4=3`) followed by a complete re-run. The aborted
  acquisitions (`task-goNogo_bold` `6435a383…`, `task-nBack_bold` `64358a7a…`)
  were **deleted from Flywheel**; the complete re-runs (formerly
  `task-goNogo_bold_1` / `task-nBack_bold_1`, i.e. BIDS `run-2`) were **renamed to
  `task-goNogo_bold` / `task-nBack_bold`** so each curates as the sole run (no
  `run-` entity). These notes are now the aborts' only record.
- **s394 / ses-04 — aborted `cuedTS` run removed; re-run promoted.** A 2-volume
  (`dim4=2`) false-start (`task-cuedTaskSwitching_bold` `6413b83c…`) preceded a
  complete re-run. The abort was **deleted from Flywheel**; the re-run (formerly
  `task-cuedTaskSwitching_bold_1`) was **renamed to `task-cuedTaskSwitching_bold`**
  (sole run). This note is now the abort's only record.
- **Short false-start BOLDs removed (dim4 15/17/19 raw).** Three acquisitions
  survived the 7-dummy-volume trim (raw dim4 > 7) but were still far-too-short
  false-starts — well under 5 % of the task's expected length — so they were
  **deleted from Flywheel** rather than left to curate as junk `run-1` scans.
  Each abort's own `gephysio` gear analyses (input scoped to that series) were
  deleted first to clear the delete. These notes are now the aborts' only record:
  - **s29 / ses-03 `spatialTS`** — abort acq `5fc7cedf…` (SeriesNumber 5, raw
    `dim4=15`) deleted. The complete re-run acq `5fc802cb…` (SeriesNumber 8,
    `dim4=332`) survives and, as the sole remaining `task-spatialTS_bold`,
    curates as the single run.
  - **s373 / ses-02 `spatialTS`** — abort acq `63e4ce94…` (label
    `task-spatialTaskSwitching_bold_1`, SeriesNumber 6, raw `dim4=19`) deleted.
    The complete re-run acq `63e2a08e…` (plain `task-spatialTaskSwitching_bold`,
    SeriesNumber 7, `dim4=336`) survives as the sole run.
  - **s43 / ses-02 `goNogo`** — abort acq `5fb6c912…` (SeriesNumber 7, raw
    `dim4=17`) deleted. There was **no re-run**, so `goNogo` is not acquired for
    this session (no usable data existed to keep).

## Generator skips & injected metadata (src/network_fmri)

- **`T1w MPRAGE PROMO` skipped** (`curation.SKIP_ACQUISITIONS`). The legacy PROMO
  structural is superseded by the `NEW Sag_MPRAGE_T1` protocol scan
  (`acq-SagMPRAGE`); its NIfTI is 4-D (a PROMO motion-nav series), invalid as a
  `_T1w` (validator `T1W_FILE_WITH_TOO_MANY_DIMENSIONS`). Every canonical
  discovery subject retains ≥1 SagMPRAGE, so this leaves no subject without a
  T1w. SagMPRAGE is the sole canonical T1w.
- **Fieldmap `Units: "Hz"` injected** (`heuristic.MetadataExtras`). The single
  `_fieldmap` image is BIDS "case 3" and requires `Units` (validator
  `UNITS_MUST_DEFINE`); these are scanner-computed B0 maps in Hz. The old Oak
  dataset omitted this field.

## Curation remaps (config/curation_config.json → `flywheel`)

The generator applies these deterministically; they are the single source of truth.

- **Subject aliases:** `s19-2→s19`, `s29-2→s29`, `s43-2→s43`, `ex26207→s297`.
- **Session reassign:** `s03/22752 → s10` (session mislabeled under s03 on Flywheel).
- **Session exclude:** `s29/22424` (fmap-only test session, 2020-11-11 — not curated).
- **Skip subject:** `n01`.
- **Session numbering:** chronological `ses-01…` by acquisition timestamp (e.g. the
  s03 rescue T1w `25210` lands at `ses-13`).

## Changelog

Newest first. Each entry: date — subject/session — what changed, why, where.

- **2026-07-15** — s29/ses-03 (`spatialTS`), s373/ses-02 (`spatialTS`),
  s43/ses-02 (`goNogo`) — deleted three short false-start acquisitions from
  Flywheel (raw `dim4` 15/17/19, i.e. 8/10/12 after the 7-dummy trim — each
  < 5 % of its task's expected length). s29 and s373 each had a complete re-run
  that survives as the sole run; s43 `goNogo` had none. Found via the post-trim
  `.bidsignore` staging audit (scans just over 7 volumes that trimmed without
  error but are unusable). Each abort's own series-scoped `gephysio` analyses
  were deleted to clear the acquisition delete. Downstream follow-up: re-export
  these sessions (the surviving re-runs then curate as single runs) and drop the
  now-stale `dest_run=2` rows for these scans from the reconciliation manifests.
- **2026-07-15** — s480/ses-03 (`goNogo`, `nBack`), s394/ses-04 (`cuedTS`) —
  deleted aborted false-start acquisitions from Flywheel (dim4 1/3 for s480, 2 for
  s394) and renamed each session's complete re-run `task-*_bold_1` → `task-*_bold`,
  collapsing these multi-run (run-1-abort / run-2-good) cases to a single clean run.
  Downstream follow-up: re-export those two sessions and drop the corresponding
  `dest_run=2` rows from `config/manifests/reconciliation_validation.tsv`.
- **2026-07-13** — s29/ses-01 — relabeled Flywheel acquisition `task-cuedTS_bold`
  → `task-spatialTS_bold` (spatialTS was performed under a cuedTS protocol name).
  Recovers a matched run; replaces the earlier "irreconcilable" determination.
- **2026-07-13** — s43/ses-08 — deleted the aborted single-volume (`dim4=1`)
  `directedForgetting` acquisition from Flywheel (non-conformant as a `_bold`).
