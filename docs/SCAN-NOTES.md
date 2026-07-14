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

- **2026-07-13** — s29/ses-01 — relabeled Flywheel acquisition `task-cuedTS_bold`
  → `task-spatialTS_bold` (spatialTS was performed under a cuedTS protocol name).
  Recovers a matched run; replaces the earlier "irreconcilable" determination.
- **2026-07-13** — s43/ses-08 — deleted the aborted single-volume (`dim4=1`)
  `directedForgetting` acquisition from Flywheel (non-conformant as a `_bold`).
