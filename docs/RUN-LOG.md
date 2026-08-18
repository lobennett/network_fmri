# Run log

## 2026-08-18 — first full curate + export

**Flywheel normalization.** 744 acquisitions renamed to canonical
`task-<bidsTask>_bold`, verified beforehand as output-neutral (no collisions
created, no acquisition's BIDS mapping changed, no label left uncovered). Rollback
record: `$SCRATCH/normalize-r01network.json`.

The 5 session reparentings in the same pass were rejected —
`403 Admin privileges are required to move a session to a Core edition project`.
None applied.

**Curate + export.** 57 subjects, 51 array tasks, all `COMPLETED`. 975 GB. 60 curate
invocations for 57 subjects: the extra three are forced-subject groups for s10, s19,
s29 and s43.

### Verification

Every subject's NIfTI filenames were diffed against the previous pipeline's
per-subject exports at `$SCRATCH/bids_staging/parts/<cohort>/<subject>` — **not** the
merged `bids_staging/<cohort>` trees, which have been pruned and renumbered and so
legitimately differ.

| Cohort | Subjects | Result |
|---|---|---|
| discovery | 5 | identical |
| validation | 41 | identical |
| excluded | 11 | identical |

This covers subject, session number, task, run, echo and suffix — not file contents.
Sidecars were spot-checked: `Units: Hz` present on fieldmaps, confirming
`MetadataExtras` applied.

### Validation

`docker://bids/validator:3.0.1`.

| Cohort | Result |
|---|---|
| discovery | `rc=0`, BIDS compatible — 2065 files / 98.5 GB |
| excluded | `rc=16` — 3 invalid sidecars ([SCAN-NOTES.md](SCAN-NOTES.md)) |
| validation | pending merge |

This run predated the `datalad run` wrapping, so its exports carry no provenance
records. It was moved to `$SCRATCH/network_fmri_pre-provenance` and kept as a
verification baseline for the re-run below.

## 2026-08-18 — re-run with provenance

Same 57 subjects, now through `import-subject` so each `parts/<subject>` is a dataset
whose history carries a `[DATALAD RUNCMD]` commit pinning `network_fmri@d3b2b1e`.

One transient Flywheel HTTP 500 during curate (s76), recovered by retry. No hard
failures.

Verify against the baseline once merged — filenames should be identical to
`$SCRATCH/network_fmri_pre-provenance/<cohort>/parts/<subject>/`.
