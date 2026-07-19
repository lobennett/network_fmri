# Design: `fmap_link` stage — B0 field-map ↔ BOLD linkage

**Date:** 2026-07-19
**Status:** Approved (design)
**Scope:** network_fmri only. Adds a new pipeline stage that writes BIDS
`B0FieldIdentifier`/`B0FieldSource` metadata so fMRIPrep/SDCFlows can apply
susceptibility distortion correction (SDC). **Anat quality-control exclusion is
explicitly out of scope** (deferred to a separate, MRIQC-gated design).

## Problem

The Flywheel→BIDS pipeline emits, per session, one Hz field map
(`sub-X_ses-Y_run-1_fieldmap.nii.gz` + `_magnitude.nii.gz`, `Units: Hz`,
`series_description: fmap-fieldmap`) alongside multi-echo BOLD for every task.
The BOLD and field-map sidecars currently carry **no** linkage metadata
(`B0FieldIdentifier`/`B0FieldSource`/`IntendedFor` all absent). Without it,
fMRIPrep cannot associate a field map with the BOLD runs it should correct, so
**SDC is silently skipped** for the whole dataset.

## Data shape (verified on OPT trees, 2026-07-19)

Surveyed all sessions in `bids_repro/OPT/{discovery,validation,excluded}`:

| cohort | sessions | clean (1 fmap + func) | BOLD but no fmap | fmap but no func | multi-fmap |
|---|---|---|---|---|---|
| discovery | 61 | 60 | 0 | 1 | 0 |
| validation | 500 | 493 | 4 | 3 | 0 |
| excluded | 32 | 31 | 0 | 1 | 0 |

Key facts:
- **Exactly one field map per session** — never multiple. The mapping "one field
  map → all BOLD in its session" is unambiguous everywhere.
- **4 validation sessions have BOLD but no field map**
  (`s1258/ses-06`, `s1391/ses-05`, `s1399/ses-12`, `s1445/ses-01`). Those runs
  get no SDC; the stage must warn + skip, not error.
- A few sessions have a field map but no func (orphan); harmless, skipped.

## Convention decision

Use **`B0FieldIdentifier` / `B0FieldSource`** (BIDS ≥1.7), not `IntendedFor`:
path-independent (survives renames/moves), fMRIPrep/SDCFlows-preferred, and it
maps cleanly onto our session-group model. Rejected `IntendedFor` (path-based,
must be regenerated on any reorg) and "both" (redundant, two things to keep
consistent).

For a direct Hz field map, SDCFlows groups the `_fieldmap` **and** its
`_magnitude` by a shared `B0FieldIdentifier`, so **both** fmap sidecars receive
the identifier; every BOLD echo in the session receives the matching
`B0FieldSource`.

## Architecture — Approach A (dedicated stage)

One module per stage, matching the existing pattern (curate/export/merge/trim/
events/datalad/select). Rejected: folding into `merge` (couples concerns, harder
to test/re-run), and doing it in fw-heudiconv at export (per-file layer can't see
the whole session).

### §1 Core module — `network_fmri/b0link.py`

```python
def link_b0_fields(cohort_dir: Path) -> LinkSummary
```

Glob `sub-*/ses-*` under the cohort root; for each session:

1. Find `fmap/*_fieldmap.nii.gz`. If none: if the session has BOLD, record a
   `no_fmap` warning and `continue`; else skip silently (anat-only session).
2. Find `func/*_bold.nii.gz`. If none: record `orphan_fmap` and `continue`
   (do not stamp a dangling identifier).
3. Both present: `identifier = f"{sub_label}_{ses_dir}"` where `sub_label` is the
   subject directory with the `sub-` prefix stripped and `ses_dir` is the session
   directory verbatim — e.g. `sub-s1035/ses-01` → `s1035_ses-01`. (Only needs to
   be unique within a subject; including the subject label keeps it globally
   unambiguous and human-readable.) Stamp
   `B0FieldIdentifier = identifier` onto the `_fieldmap.json` **and**
   `_magnitude.json`; stamp `B0FieldSource = identifier` onto **every**
   `_bold.json` (all echoes, all tasks, including rest).

Behaviour:
- **Idempotent** — if the target key already equals the computed value it's a
  no-op; if present with a different value it's overwritten (re-runs converge).
- **Multi-fmap** is asserted-never; if encountered, **raise** rather than guess.

`LinkSummary` holds counts `{sessions_linked, bolds_stamped, no_fmap,
orphan_fmap}`, printed at stage end and asserted by tests.

### §2 Sidecar writing, determinism, CLI, stage wiring

**Sidecar writes (determinism-critical):** `json.load` → assign key →
`json.dump(..., indent=2, ensure_ascii=False)` + trailing newline → atomic
temp-file + rename (same pattern as `network_events/trim.py`). Keys are appended
via dict insertion order, so output is a pure function of input → byte-identical
across runs. This **adds** `B0Field*` keys to every bold/fmap sidecar, so the new
canonical tree intentionally differs from the current OPT baseline — that is the
feature. Match fw-heudiconv's existing sidecar indent so the diff is *only* the
added keys.

**CLI:** `fw2bids fmap-link <cohort_dir>` — auto-registered like `fw2bids
datalad`; thin wrapper calls `link_b0_fields`, prints the summary, exits
non-zero only on the assert-never multi-fmap case.

**Stage wiring:** new `submit/fmap_link.py` (single, non-array; `nthreads=2,
mem_gb=8, time=00:20:00`) + `templates/fmap_link.sbatch.tmpl` running
`{run_prefix} fw2bids fmap-link {cohort_dir}`. Added to `pipeline.py` `_STAGES`
as:

```
curate → export → merge → trim → events → fmap_link → datalad → select
```

**Not** in `_COHORT_GATED` (runs for all three cohorts, so excluded BIDS is also
valid). Must precede `datalad` (edits git-tracked JSON). **Container rebuild
required** — new code runs inside the container.

### §3 Testing & validation

**Unit tests (TDD, `tests/test_b0link.py`)** on synthetic mini-trees in
`tmp_path`:

1. **Happy path** — session with `_fieldmap`+`_magnitude` + multi-echo BOLD
   across 2 tasks + rest → fmap & magnitude get
   `B0FieldIdentifier="s1035_ses-01"` (subject label sans `sub-` + session dir);
   every bold echo gets matching `B0FieldSource`; summary counts correct.
2. **No field map** — session with BOLD, no fmap → bolds untouched, `no_fmap=1`,
   no crash.
3. **Orphan field map** — fmap, no func → not stamped, `orphan_fmap=1`.
4. **Idempotency** — run twice → second run is a no-op, files byte-identical.
5. **Determinism** — two independent trees with identical inputs → byte-identical
   sidecars.
6. **Multi-fmap** — assert it raises.

**Operational validation** (executed in the plan, on real OPT data):
- Run `fw2bids fmap-link` on all three OPT cohorts; confirm the summary matches
  the survey (discovery 60 linked; validation 493 linked + 4 `no_fmap`; excluded
  31 linked).
- Spot-check one linked session's sidecars (fmap+magnitude identifier, all bold
  sources).
- **Re-run the discovery A≡B repro check *with* `fmap_link` in the DAG** — prove
  the new stage is byte-deterministic before the tree becomes canonical.
- `bids-validator` still 0 errors (`B0Field*` are valid BIDS).

## Out of scope

- Automated anat QC (MRIQC IQMs to reproduce/relitigate manual `.bidsignore`
  keep/remove decisions) — separate design, gated on the MRIQC/BABS campaign.
- Encoding the existing manual anat exclusions as a pipeline channel — deferred
  with the anat-QC design.

## Re-executability

After this lands, the full DAG (`fw2bids pipeline --cohort <c> --container
--staging <dir>`) produces SDC-ready BIDS with B0 linkage baked in, from
Flywheel to final tree, for a new user with no manual steps.
