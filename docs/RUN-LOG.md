# Run log

## 2026-08-18 — first full curate + export

**Flywheel normalization.** 744 acquisitions renamed to canonical
`task-<bidsTask>_bold`. Verified beforehand to be output-neutral (0 collisions
created, 0 acquisitions whose BIDS mapping changed, 0 labels left uncovered).
Rollback record: `$SCRATCH/normalize-r01network.json`.

The 5 session reparentings in the same pass failed with
`403 Admin privileges are required to move a session to a Core edition project`.
Zero moves applied, so nothing is half-migrated.

**Curate + export.** 57 subjects across three cohorts, 51 array tasks, all
`COMPLETED`. 0 hard failures, 2 transient retries recovered. 975 GB of per-subject
exports. 60 curate invocations for 57 subjects — the extra three are the forced
subject groups for s10, s19, s29, s43.

A first attempt at `--throttle 8` hit HTTP 500s on 5 tasks and was cancelled; see
[OPERATIONS.md](OPERATIONS.md).

### Verification

Every subject's NIfTI filenames were diffed against the previous pipeline's
per-subject exports:

| Cohort | Subjects | Result |
|---|---|---|
| discovery | 5 | identical |
| validation | 41 | identical |
| excluded | 11 | identical |

Compared against `$SCRATCH/bids_staging/parts/<cohort>/<subject>`, **not** the
merged `bids_staging/<cohort>` trees — those have been through `prune`, which
deletes scans and renumbers runs, so their filenames legitimately differ from a
fresh export.

This covers subject, session number, task, run, echo and suffix. It does not
compare file contents. Sidecar metadata was spot-checked: `Units: Hz` is present on
fieldmaps, confirming `MetadataExtras` applied during the live curate.

### Validation

Run with `docker://bids/validator:3.0.1`.

| Cohort | Result |
|---|---|
| discovery | `rc=0` — "This dataset appears to be BIDS compatible", 2065 files / 98.5 GB |
| excluded | `rc=16` — 3 invalid sidecars, 1037 files / 52.5 GB |
| validation | pending merge |

The `excluded` failure is `SoftwareVersions` as a list in `sub-s1165/ses-02`; see
[SCAN-NOTES.md](SCAN-NOTES.md). A pure-Python schema check had reported the same
cohort as clean, which is why the container validator replaced it.

### Outstanding

- merge of `validation` still running
- `validation` not yet validated or DataLad-versioned
- no cohort has been DataLad-versioned yet
