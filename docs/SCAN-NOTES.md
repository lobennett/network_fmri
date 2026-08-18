# Scan notes

What gets curated, what is deliberately left out, and which source records are
wrong. Code lives in `acquisitions.py` (labels) and `sessions.py` (subjects and
sessions).

## Acquisition naming

Functional labels are canonical `task-<bidsTask>_bold`, optionally with Flywheel's
`_1`/`_run_2` dedup suffix for a repeat acquisition in one session. `TASKS` is an
allowlist, so an unrecognised task name is skipped rather than curated under a
misspelled entity.

anat, dwi and fmap have one spelling each and are listed explicitly in `NON_FUNC`.

Labels were normalized on Flywheel on 2026-08-18 — 744 renames collapsing four
years of operator drift (ten spellings of `spatialTSWCuedTS`, the `shapeMaching`
typo, `task_` for `task-`). That is why the code is a rule plus six rows instead of
a 60-row spelling table. Rollback record: `$SCRATCH/normalize-r01network.json`.

## Deliberately not curated

| Label | Reason |
|---|---|
| `3Plane Loc SSFSE`, `GE HOS FOV28*`, `HO Shim` | localizers and shims |
| `Processed Images*` | scanner-derived, not source data |
| `run-1_sbref` | single-band reference, unused downstream |
| `T1w MPRAGE PROMO` | 4D PROMO motion-nav series; not a valid `_T1w` (`T1W_FILE_WITH_TOO_MANY_DIMENSIONS`) |
| `fmap-fieldmap_1` | a genuine second fieldmap — see below |

Subject `n01` is the pilot; its acquisitions use a different convention entirely
(`task-n-back_run-1_ssg`) and nothing maps them.

**`fmap-fieldmap_1` is real data being dropped.** Four sessions have a second
fieldmap: s76 (×3) and s1486. The fmap template hardcodes `run-1`, so curating
these needs a run index first.

## Wrong source records

Five sessions sit under the wrong subject on Flywheel. All are compensated for in
code; none are fixed at source, because reparenting a session returns
`403 Admin privileges are required to move a session to a Core edition project`.

| Session | Filed under | Belongs to |
|---|---|---|
| `22752` | s03 | s10 — different participant |
| `22542` | s19-2 | s19 — duplicate subject record |
| `20210305` | s29-2 | s29 |
| `20201112` | s43-2 | s43 |
| `unknown2` | ex26207 | s297 |

`s29/22424` is excluded outright: an fmap-only test session, single-echo, no usable
functional or behavioral data.

If a Flywheel admin ever moves those five, `SUBJECT_ALIASES`, `ReplaceSubject`,
`FWBIDS_FORCE_SUBJECT`, `relevant_labels()` and `jobs()` can all be deleted — about
70 lines that exist only to compensate.

## Trimming shifts the BOLD clock by 10.43 s

`trim` removes the first 7 volumes of every BOLD. TR is 1.49 s, so **every trimmed
run starts 10.43 s later than the scanner did**.

Nothing in this repository adjusts event timing. The raw behavioral files carry no
such adjustment either, so **`events.tsv` onsets must be shifted by −10.43 s**
(equivalently, expressed relative to the trimmed first volume). That is
`network_events`' responsibility, not this repo's.

This fails silently: a 10.43 s offset produces no BIDS validation error and no
obvious artifact, only wrong first-level models. Confirm the convention before
writing any `events.tsv`, and re-check it if `N_DUMMY` or TR ever changes.

```
N_DUMMY = 7        (network_fmri.trim)
TR      = 1.49 s   (sidecar RepetitionTime)
shift   = 10.43 s
```

Trimmed files are marked with `NumberOfVolumesDiscardedByUser: 7`, which is how a
consumer can tell a trimmed run from an untrimmed one.

## Known data defect

`sub-s1165/ses-02` `task-directedForgetting` echoes 1–3 carry `SoftwareVersions` as
a list:

```json
["28", "LX", "MR Software release:RX28.0_R04_UHP3T_2111.a"]
```

BIDS requires a string, so the `excluded` cohort fails validation with
`JSON_SCHEMA_VALIDATION_ERROR`. The DICOM tag is multi-valued for that scan and
Flywheel stored the list; fw-heudiconv copies it verbatim. It is a string in every
other sidecar checked (15/15 in discovery). Upstream data, not the heuristic.

Fixing it means coercing sidecars after export or editing the file metadata on
Flywheel. Neither is done.
