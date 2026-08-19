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
| validation | pending (merge + re-import in flight) |

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

## 2026-08-18 — discovery through events

| Stage | Result |
|---|---|
| trim | 873 BOLDs, median 336 → 329 volumes |
| global-signal | pre-trim and post-trim, echo-2 only (291 files) |
| behavior-clean | 224 CSVs, 1:1 with BIDS func runs |
| events | 224 `_events.tsv`, min onset 0.179 s, 0 negatives |
| validate | `rc=0`, 2289 files |

Onset shift is 7 × 1.49 s = 10.43 s, applied in `network_events` from the sidecar's
`NumberOfVolumesDiscardedByUser` — never baked into the raw behavioral files.

## 2026-08-18 — MRIQC on discovery

Array `39709054`, 7 session-level jobs (s03 ses-05/13, s10 ses-09, s19 ses-03/05,
s29 ses-04, s43 ses-05) via the mechababs campaign at
`$SCRATCH/mechababs_campaigns/r01network` (`code/mechababs@a31fb43`, MRIQC 24.0.2).

Two setup requirements, both non-obvious:

- Campaign venv must be **Python 3.12** — babs pins `numpy < 2.0`, which has no cp313
  wheels.
- `con-duct` must be installed explicitly: mechababs shells out to `duct` but does not
  declare it as a dependency.

All 7 units `COMPLETED` in 36–49 min (`MaxRSS` 21–22 GB), merged, ledger
`babs-merged: true`. T1w selection and the resulting deletions are in
[SCAN-NOTES.md](SCAN-NOTES.md); discovery now holds 5 T1w, one per subject, with the two
rejected images recoverable from `e2073f76` in the cohort dataset.

Earlier attempts, both archived under `derivative-attempts/`:

`39725072`'s predecessor failed all 7 tasks at the zip step with `7z: command not found`
— MRIQC itself had finished, and cleanup then dropped ~40 min of compute per task. `7z`
lives in Sherlock's `system p7zip/16.02`; the binary is self-contained, so the fix is a
`PATH` entry rather than an Lmod load. `--mem` also went 24G → 32G after the observed
21–22 GB peaks.

The first submission (`39706846`) failed all 7 tasks in 39 s on two errors in
`clusters/sherlock.yaml`: `job_compute_space: /scratch/${USER}` (on Sherlock `$SCRATCH`
is `/scratch/users/<sunetid>`, and `/scratch` is root-owned) and a `source
/etc/profile.d/modules.sh` that does not exist here. Since `babs init` bakes the
preamble into `code/participant_job.sh`, the fix needs
`mechababs retire-derivative` and a re-scaffold, not just a config edit — the failed
attempt is archived at `derivative-attempts/discovery-MRIQC-24.0.2-attempt-1`.
