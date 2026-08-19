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

## Crossed acquisition labels (s43 / ses-11) — fixed at source

Session 22890 had two `stop_with_*` labels applied to the wrong scans:

| Acq time | Volumes | Was labelled | Actually |
|---|---|---|---|
| 08:57:54 | 524 (73% of 718 median) | stopSignalWDirectedForgetting | correct, aborted |
| 09:13:20 | 368 (99.5% of 370 median) | stopSignalWDirectedForgetting_1 | **stopSignalWFlanker**, complete |
| 09:29:30 | 103 | stopSignalWFlanker | **stopSignalWDirectedForgetting**, aborted |

The two labels were swapped on Flywheel on 2026-08-18 so a re-pull produces correct
naming. Rollback record: `$SCRATCH/relabel-s43-ses11.json`.

Both behavioral CSVs exist for this session, so nothing is ambiguous once the labels
are right. **Any dataset exported before the fix still carries the wrong task names
for this session and needs s43 re-imported.**

## Auditing label vs DICOM SeriesDescription

Comparing each run's BIDS task against its DICOM `SeriesDescription` across 2738 func
runs found 6 disagreements. Only the s43 pair above was a real error. In the other
five the **label is right and the DICOM field is stale** — the participant performed a
different task than the protocol name, or the operator marked the corrected scan:

| Case | Label | DICOM says | Why the label wins |
|---|---|---|---|
| s29 / ses-01 | spatialTS | cuedTS | already-documented deliberate relabel |
| s1258 / ses-02 | spatialTS | cuedTS | only spatialTS behavioral exists; medians (335 vs 336) cannot discriminate |
| s320 / ses-12 | stopSignalWFlanker | directedForgettingWCuedTS | 106% of flanker median vs 75%; sibling scan is marked `..._real_bold` |
| s03 / ses-03 | nBack | goNogo | 96.5% of nBack median vs 127%; sibling scan is marked `actual_goNogo` |

**Adjudication rule.** Volume count against the task's cohort median decides it. Where
volumes agree with the label, the label stands. Where they contradict the label and
match the DICOM task, the label is wrong. Operator hints like `_real_` and `actual_`
mark the scan the operator considered correct.

## Multi-run sessions and behavioral pairing (discovery)

288 (subject, session, task) units: 221 pair 1:1, 60 are `rest` (no behavioral
expected), 4 have no behavioral, 3 had two BOLD runs against one CSV.

| Case | Resolution |
|---|---|
| s10 / ses-01 goNogo | run-1 = 38 vols (abort) → CSV pairs with run-2 |
| s29 / ses-12 directedForgettingWFlanker | run-1 = 61 vols (abort) → CSV pairs with run-2 |
| s43 / ses-11 | not ambiguous; crossed labels, see above |

Behavioral absent entirely (searched `raw_cleaned`, all 7 `archive/` collections,
`dropped_subjects`, `exclusions`, `qc`, `mTurk`):

- s03 / ses-01 nBack
- s19 / ses-02 goNogo
- s29 / ses-02 goNogo
- s19 / ses-11 directedForgettingWFlanker — only an already-processed
  `iti_adjusted_events` TSV survives; treated as irrecoverable

## Behavioral filename regimes

Three coexist in `raw_cleaned`, and **none encodes a run index** — run assignment can
never come from a filename:

```
raw               177   go_nogo_single_task_network__fmri_results (12).csv
clean-hyphen       41   sub-s03_ses-1_task-go-nogo_desc-raw.csv
clean-underscore    6   sub-s29_ses_11_task-directed_forgetting_with_flanker_desc_raw.csv
```

`(N)` suffixes are browser-download counters, not run numbers. Out-of-scanner practice
data lives in a `practice/` subdirectory and is excluded.

## Split scanner visits (fieldmap stranded)

Flywheel split three single visits into two sessions each, leaving the fieldmap alone
in a container with no functional runs. The fieldmap was acquired ~1.5 min before the
first BOLD run of its twin, so it belongs to those runs and those sessions currently
have no fieldmap for SDC.

| Subject | Stray session | Twin | fmap time | First BOLD |
|---|---|---|---|---|
| s1258 | `unknown_2` | `28338` | 02:34:09 | 02:35:47 |
| s1391 | `unknown` | `28270` | 23:08:00 | 23:09:38 |
| s1445 | `unknown_5` | `28037` | 00:09:41 | 00:11:27 |

Moving the acquisition at source is refused — `403 Can't create ad hoc when lab
edition is off` — so `sessions.SESSION_MERGES` gives the stray session its twin's
number instead. The fieldmap then curates into the same BIDS session as the runs, and
the empty container stops consuming a session number: each subject goes 13 -> 12
sessions, which also makes their behavioral session numbering line up 1:1 with no
shift.

Same-day sessions that are **not** merged, because both members already have their own
fieldmap and full batteries: s247 (ses-09/10), s1270 (ses-11/12), s321 (ses-01/02),
s1326 (ses-03/04). `s03/ses-13` is anat+fmap only by design (the documented rescue
T1w session).

## Behavioral run assignment (discovery)

`sourcedata/<sub>/<ses>/beh/` is canonical: one CSV per BOLD run. No mapping table back to
the raw tree is kept, since that tree may be archived. The decisions that are not
recoverable from the result:

| Subject | Session | Task | Paired with | Dropped as false start |
|---|---|---|---|---|
| s10 | ses-01 | goNogo | run-2 | run-1 (38 vols) |
| s29 | ses-12 | directedForgettingWFlanker | run-2 | run-1 (61 vols) |
| s43 | ses-11 | stopSignalWDirectedForgetting | run-1 (524 vols) | run-2 (103 vols) |

All 224 discovery behavioral files resolve; the other 221 are unambiguous 1:1.

Raw behavioral files carry **no** trim adjustment, so the -10.43 s onset shift is
applied unconditionally downstream.

## Behavioral run assignment (validation)

1887 of 1893 non-rest runs get a behavioral CSV. The exceptions, all verified against a
session map that is identity 1:1 (each subject's raw and BIDS task sets match
session-for-session, so none of these is an alignment artefact):

**Two false starts**, resolved by volume count as above:

| Subject | Session | Task | Paired with | Dropped |
|---|---|---|---|---|
| s336 | ses-05 | goNogo | run-2 (382 vols, = median) | run-1 (298) |
| s216 | ses-05 | directedForgetting | run-2 (434) | run-1 (94) |

**Four behavioral files genuinely absent** — the task was scanned but no file exists in
any raw session, so these runs get no events:

| Subject | Session | Task |
|---|---|---|
| s1292 | ses-04 | nBack |
| s300 | ses-08 | flanker |
| s180 | ses-12 | shapeMatchingWCuedTS |
| s1175 | ses-11 | cuedTSWFlanker |

s180 and s1175 each *do* have one file for that task in the adjacent session, but it is
correctly consumed by that session's own scan — the task was simply run twice with only
one file saved.

**Three repeated tasks with two complete files** — the pairing is an assumption:

| Subject | Session | Task | run-1 | run-2 |
|---|---|---|---|---|
| s76 | ses-12 | directedForgettingWFlanker | `... (11).csv` | `... (12).csv` |
| s247 | ses-12 | stopSignalWDirectedForgetting | `....csv` | `... (1).csv` |
| s1175 | ses-12 | cuedTSWFlanker | `....csv` | `... (1).csv` |

Both scans and both files are complete in each case, so `pick_run`'s median rule does not
apply — and nothing recoverable says which file came from which run. Ruled out as
discriminators: file mtimes (one bulk 2024-07-31 copy, seconds apart), total and
in-scanner duration (the ordering that fits s76 inverts for s247 and s1175), and any
absolute timestamp (the CSVs carry only relative `time_elapsed`).

So they are paired by **browser download order** — bare `.csv` before ` (1)`, and `(11)`
before `(12)` — on the assumption that each run's file was downloaded after that run.
The condition sequences are randomised per run and differ on 24–97% of trials, so if the
assumption is wrong for a given case, that pair's events are swapped. Treat these 6 runs
as lower confidence than the rest.

## T1w selection from MRIQC (discovery)

Discovery has 7 T1w images across 5 subjects. s10, s29 and s43 have one each; s03 and
s19 have two, so only those two need a choice. Session-level MRIQC 24.0.2 IQMs:

| | s03 ses-05 | s03 ses-13 | | s19 ses-03 | s19 ses-05 |
|---|---|---|---|---|---|
| `cjv` (lower better) | 0.9755 | **0.6925** | | 0.7176 | **0.6971** |
| `cnr` (higher better) | 0.8667 | **1.4734** | | 1.3149 | **1.3357** |
| `snr_total` (higher) | 3.9246 | **5.3842** | | 4.6347 | **4.6496** |
| `efc` (lower) | 0.5363 | **0.5041** | | **0.4900** | 0.5018 |
| `fber` (higher) | 2536.7 | **7153.2** | | **4689.3** | 4429.8 |
| `wm2max` (lower) | 0.4036 | **0.3942** | | 0.4125 | **0.4016** |
| `qi_2` (lower) | 0.0009 | **0.0005** | | **0.0005** | 0.0007 |

**s03 → keep ses-13.** It wins every metric (7-0, `qi_1` tied at 0) by wide margins: CJV
29% better, CNR 41%, FBER 64%. ses-05 is a genuinely poor scan.

**s19 → keep ses-05, but the two are near-equivalent.** The split is 4-3 and every margin
on the winning side is under 3% (`snr_total` differs by 0.3%). The call rests on `cjv`,
`cnr` and `snr_total` — CJV being the most informative single T1w metric for INU and
motion — while `efc`, `fber` and `qi_2` favour ses-03. Either would be defensible.

## T2w selection from MRIQC (discovery)

Six T2w across four subjects; s43 has none in any session, so **four of five** subjects
end with exactly one T2w, not all five. Only s19 and s29 had a choice.

| | s19 ses-01 | s19 ses-03 | | s29 ses-01 | s29 ses-04 |
|---|---|---|---|---|---|
| `cjv` (lower better) | **1.0038** | 1.0465 | | 1.0557 | **1.0180** |
| `cnr` (higher better) | **0.7450** | 0.7162 | | **0.7194** | 0.7018 |
| `snr_total` (higher) | **3.5656** | 3.5555 | | 3.8254 | **3.8578** |
| `efc` (lower) | 0.5272 | **0.5177** | | 0.5173 | **0.5132** |
| `wm2max` (lower) | 0.1942 | **0.1940** | | 0.1893 | **0.1877** |
| `qi_2` (lower) | **0.0009** | 0.0011 | | 0.0010 | **0.0007** |

**s19 → keep ses-01** (4-2), **s29 → keep ses-04** (5-1).

`fber` is excluded from both tallies: it is `-1` for s19 ses-01 and for *both* s29
sessions, which is MRIQC's could-not-estimate sentinel rather than a measurement — the
CUBE PROMO T2w does not give it usable background. Counting `-1` as a value would have
flipped s19 to ses-03 on a number that means nothing.

s29's kept T2w happens to sit in ses-04 alongside its kept T1w. s19's do not (T2w ses-01,
T1w ses-05), which is fine — fMRIPrep coregisters across sessions.

## Anat selection from MRIQC (validation)

41 subjects, all with at least one T1w and one T2w. Six needed a choice; MRIQC ran on all
48 anat-bearing sessions.

| Subject | Scan | Keep | Drop | Tally | Margin on the deciding metrics |
|---|---|---|---|---|---|
| s1127 | T1w | ses-09 | ses-01 | 6-1 | cjv 8.3%, cnr 7.4%, snr 5.2% |
| s1270 | T1w | ses-06 | ses-01 | 6-1 | cnr 9.5%, fber 16%, snr 7.9% |
| s216 | T1w | ses-11 | ses-01 | 5-2 | cjv 10%, cnr 14.2%, fber 15.8% |
| s1258 | T1w | ses-06 | ses-01 | 4-3 | cjv 1.9%, cnr 3.3% — both primaries agree |
| s1351 | T1w | ses-01 | ses-08 | 3-4 | cjv 2.6%, cnr 3.9% — **tally disagrees** |
| s1399 | T2w | ses-01 | ses-02 | 4-2 | cjv 4.8%, cnr 2.1%; `fber` excluded (-1) |

**The rule is CJV and CNR first**, not a majority of the eight IQMs. They are the most
informative for INU and motion, and on a near-identical pair the other metrics move within
noise. s1351 is where the two criteria actually diverge: ses-08 wins four metrics, but one
of them (`snr_total`) by 0.3%, while both primaries favour ses-01. Decided on the primaries,
consistent with discovery's s19.

`fber` is excluded wherever it reads `-1`, which is MRIQC's could-not-estimate sentinel
rather than a measurement — it affects the CUBE PROMO T2w in particular.

## No distortion correction for one session

`sub-s1399/ses-12` has 3 BOLD runs (9 files) and no field map, so those runs go through
fMRIPrep uncorrected. It is the only such session across discovery and validation; every
other session has exactly one field map with its `_magnitude`, and there are no field maps
without BOLD — the standing check that [session merges](#split-scanner-visits-fieldmap-stranded)
still hold.

## Keeping QA-rejected scans out of the next pull

A scan dropped after QA must not reappear when the project is re-pulled, so the
rejection is recorded **on Flywheel**: `network_fmri qa-reject --target s03/05/T1w`
appends `_qa-reject` to the acquisition label, and `map_acquisition` returns `None` for
any label matching that marker. The scan is never curated, so `export` never downloads it.

It has to be keyed at the source rather than in a table here, because the heuristic
cannot see the session: `infotodict` is called per session but `SeqInfo.accession_number`
is `None` (only `patient_id` is populated), and a subject-level skip would drop the kept
scan along with the rejected one.

Applied so far (rollback records: `$SCRATCH/qa-reject-t1w.json`, `qa-reject-t2w.json`):

| Target | Flywheel session | Label |
|---|---|---|
| s03 ses-05 T1w | 22734 | `NEW Sag_MPRAGE_T1_qa-reject` |
| s19 ses-03 T1w | 22542 | `NEW Sag_MPRAGE_T1_qa-reject` |
| s19 ses-03 T2w | 22542 | `T2w CUBE PROMO .8mm sag_qa-reject` |
| s29 ses-01 T2w | 20201113 | `T2w CUBE PROMO .8mm sag_qa-reject` |
| s1127 ses-01 T1w | 27774 | `NEW Sag_MPRAGE_T1_qa-reject` |
| s1258 ses-01 T1w | 27821 | `NEW Sag_MPRAGE_T1_qa-reject` |
| s1270 ses-01 T1w | 27820 | `NEW Sag_MPRAGE_T1_qa-reject` |
| s1351 ses-08 T1w | 28579 | `NEW Sag_MPRAGE_T1_qa-reject` |
| s216 ses-01 T1w | 26051 | `NEW Sag_MPRAGE_T1_qa-reject` |
| s1399 ses-02 T2w | 28131 | `T2w CUBE PROMO .8mm sag_qa-reject` |

Each was verified before renaming by matching the acquisition's NIfTI byte size against
the annex key of the file deleted from BIDS, rather than trusting the session numbering.

A fresh pull now curates exactly one T1w and one T2w per subject — T1w only for s43, which
has no T2w in any session. Confirmed by replaying `map_acquisition` over every anat
acquisition in the project: all 41 validation subjects and 4 of 5 discovery subjects come
back exactly 1/1.

Rollback records: `$SCRATCH/qa-reject-{t1w,t2w,validation}.json`.

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
