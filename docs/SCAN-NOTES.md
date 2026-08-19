# Scan notes

What gets curated, what is deliberately left out, and which source records are wrong. Code lives
in `acquisitions.py` (labels) and `sessions.py` (subjects and sessions).

## Acquisition naming
Functional labels are canonical `task-<bidsTask>_bold`, optionally with a `_1`/`_run_2` dedup
suffix. `TASKS` is an allowlist: an unrecognised task name is skipped, not curated under a
misspelled entity. anat, dwi and fmap have one spelling each, listed in `NON_FUNC`.

744 acquisitions were normalized on Flywheel on 2026-08-18, collapsing years of operator drift
(ten spellings of `spatialTSWCuedTS`, the `shapeMaching` typo, `task_` for `task-`) into a rule
plus six rows. Rollback: `$SCRATCH/normalize-r01network.json`.

## Deliberately not curated
| Label | Reason |
|---|---|
| `3Plane Loc SSFSE`, `GE HOS FOV28*`, `HO Shim` | localizers and shims |
| `Processed Images*` | scanner-derived, not source data |
| `run-1_sbref` | single-band reference, unused downstream |
| `T1w MPRAGE PROMO` | 4D PROMO motion-nav series; not a valid `_T1w` (`T1W_FILE_WITH_TOO_MANY_DIMENSIONS`) |
| `fmap-fieldmap_1` | a second fieldmap that cannot improve SDC — see below |
Subject `n01` (the pilot) uses a different naming convention entirely and nothing maps it.

**Why the second fieldmap is dropped rather than salvaged.** Exactly four sessions have two,
and their acquisition times show none is a mid-session re-shim:

| Session | Ordering |
|---|---|
| s76 / 24392 | both fieldmaps at the **same timestamp** (00:20:14), before any run |
| s76 / 24425 | both at the **same timestamp** (00:03:23), before any run |
| s76 / 25492 | fmap 23:14:43 → all 3 runs → fmap_1 at 00:03:48, **after every run** |
| s1486 / 28061 | fmap 02:13:36 → all 5 runs → fmap_1 at 03:21:03, **after every run** |

Two are one acquisition landing in two Flywheel containers, so either copy is equivalent. The
other two are *trailing* fieldmaps with no subsequent runs; the leading fieldmap, taken 1-2 min
before the first run, is a strictly better match for those runs than one taken 20-50 min later.
No run in this project is ever preceded by a fieldmap it does not already get, so a run index
plus time-partitioned `B0FieldSource` would add machinery for no gain.

A trailing fieldmap could still support a field-drift estimate across a session — a motion and
shim-stability QA signal, which belongs to `network_qa`, not to SDC. The data stays on Flywheel.

## Wrong source records
Five sessions sit under the wrong subject on Flywheel, compensated for in code; none are fixed at
source (reparenting returns `403 Admin privileges are required to move a session to a Core edition
project`).
| Session | Filed under | Belongs to |
|---|---|---|
| `22752` | s03 | s10 — different participant |
| `22542` | s19-2 | s19 — duplicate subject record |
| `20210305` | s29-2 | s29 |
| `20201112` | s43-2 | s43 |
| `unknown2` | ex26207 | s297 |
`s29/22424` is excluded outright: an fmap-only test session, single-echo, no usable functional or
behavioral data.

## Trimming shifts the BOLD clock by 10.43 s
`trim` removes the first 7 volumes of every BOLD. TR is 1.49 s, so **every trimmed run starts
10.43 s later than the scanner did**. Nothing in this repository adjusts event timing, and the raw
behavioral files carry no such adjustment either, so **`events.tsv` onsets must be shifted by
−10.43 s** (equivalently, expressed relative to the trimmed first volume) — `network_events`'s
responsibility, not this repo's. This fails silently: a wrong shift produces no BIDS validation
error and no obvious artifact, only wrong first-level models. Confirm the convention before
writing any `events.tsv`, and re-check it if `N_DUMMY` or TR ever changes.
```
N_DUMMY = 7        (network_fmri.trim)
TR      = 1.49 s   (sidecar RepetitionTime)
shift   = 10.43 s
```
Trimmed files are marked with `NumberOfVolumesDiscardedByUser: 7`, which is how a consumer can
tell a trimmed run from an untrimmed one.

## Crossed acquisition labels (s43 / ses-11) — fixed at source
Session 22890 had two `stop_with_*` labels applied to the wrong scans:
| Acq time | Volumes | Was labelled | Actually |
|---|---|---|---|
| 08:57:54 | 524 (73% of 718 median) | stopSignalWDirectedForgetting | correct, aborted |
| 09:13:20 | 368 (99.5% of 370 median) | stopSignalWDirectedForgetting_1 | **stopSignalWFlanker**, complete |
| 09:29:30 | 103 | stopSignalWFlanker | **stopSignalWDirectedForgetting**, aborted |
The labels were swapped on Flywheel on 2026-08-18 (rollback: `$SCRATCH/relabel-s43-ses11.json`);
both behavioral CSVs exist, so nothing is ambiguous once the labels are right — but **any dataset
exported before the fix still carries the wrong task names and needs s43 re-imported.**

## Label vs DICOM SeriesDescription
6 of 2738 func runs disagree between BIDS task and DICOM `SeriesDescription`; only the s43 pair
was a real error — the other five have a stale DICOM field:
| Case | Label | DICOM says | Why the label wins |
|---|---|---|---|
| s29 / ses-01 | spatialTS | cuedTS | already-documented deliberate relabel |
| s1258 / ses-02 | spatialTS | cuedTS | only spatialTS behavioral exists; medians (335 vs 336) cannot discriminate |
| s320 / ses-12 | stopSignalWFlanker | directedForgettingWCuedTS | 106% of flanker median vs 75%; sibling scan is marked `..._real_bold` |
| s03 / ses-03 | nBack | goNogo | 96.5% of nBack median vs 127%; sibling scan is marked `actual_goNogo` |
**Adjudication rule.** Volume count against the task's cohort median decides it: where volumes
agree with the label, the label stands; where they contradict the label and match the DICOM task,
the label is wrong.

## Behavioral filename regimes
Three coexist in `raw_cleaned`, **none encoding a run index**:
```
raw               177   go_nogo_single_task_network__fmri_results (12).csv
clean-hyphen       41   sub-s03_ses-1_task-go-nogo_desc-raw.csv
clean-underscore    6   sub-s29_ses_11_task-directed_forgetting_with_flanker_desc_raw.csv
```
`(N)` suffixes are browser-download counters, not run numbers; out-of-scanner practice data lives
in a `practice/` subdirectory and is excluded.

## Split scanner visits (fieldmap stranded)
Flywheel split three single visits into two sessions each, stranding the fieldmap in a container
with no functional runs. Each was acquired ~1.5 min before its twin's first BOLD run, so it
belongs to those runs:
| Subject | Stray session | Twin | fmap time | First BOLD |
|---|---|---|---|---|
| s1258 | `unknown_2` | `28338` | 02:34:09 | 02:35:47 |
| s1391 | `unknown` | `28270` | 23:08:00 | 23:09:38 |
| s1445 | `unknown_5` | `28037` | 00:09:41 | 00:11:27 |
Reparenting at source is refused (`403 Can't create ad hoc when lab edition is off`), so
`sessions.SESSION_MERGES` gives the stray session its twin's number instead: the fieldmap joins
the twin's BIDS session, the empty container stops consuming a number, and each subject goes 13 →
12 sessions with behavioral numbering lining up 1:1. Not merged (both already have their own
fieldmap and full batteries): s247 (ses-09/10), s1270 (ses-11/12), s321 (ses-01/02), s1326
(ses-03/04); `s03/ses-13` is anat+fmap only by design.

## Behavioral session alignment and run assignment (discovery)
288 (subject, session, task) units: 221 pair 1:1, 60 are `rest` (no behavioral expected), 4 have
no behavioral, 3 had two BOLD runs against one CSV. `sourcedata/<sub>/<ses>/beh/` is canonical
(one CSV per BOLD run); no mapping table back to the raw tree is kept, since that tree may be
archived. Decisions not recoverable from the result:
| Subject | Session | Task | Paired with | Dropped as false start |
|---|---|---|---|---|
| s10 | ses-01 | goNogo | run-2 | run-1 (38 vols) |
| s29 | ses-12 | directedForgettingWFlanker | run-2 | run-1 (61 vols) |
| s43 | ses-11 | stopSignalWDirectedForgetting | run-1 (524 vols) | run-2 (103 vols, crossed labels — see above) |
Behavioral absent entirely (searched `raw_cleaned` and all archive collections): s03/ses-01 nBack,
s19/ses-02 goNogo, s29/ses-02 goNogo, s19/ses-11 directedForgettingWFlanker (only an
already-processed `iti_adjusted_events` TSV survives; irrecoverable). All 224 discovery files
resolve (the other 221 unambiguous 1:1); raw files carry **no** trim adjustment, so the -10.43 s
shift applies unconditionally downstream.

## Behavioral session alignment and run assignment (validation)
1887 of 1893 non-rest runs get a behavioral CSV. The exceptions, all verified against an identity
1:1 session map (each subject's raw and BIDS task sets match session-for-session, so none is an
alignment artefact): **Two false starts**, resolved by volume count as above:
| Subject | Session | Task | Paired with | Dropped |
|---|---|---|---|---|
| s336 | ses-05 | goNogo | run-2 (382 vols, = median) | run-1 (298) |
| s216 | ses-05 | directedForgetting | run-2 (434) | run-1 (94) |
**Four behavioral files genuinely absent** — scanned but no file exists in any raw session, so
these runs get no events (s180 and s1175 each have one file for the same task in the adjacent
session, but it is correctly consumed by that session's own scan — the task was simply run twice
with only one file saved):
| Subject | Session | Task |
|---|---|---|
| s1292 | ses-04 | nBack |
| s300 | ses-08 | flanker |
| s180 | ses-12 | shapeMatchingWCuedTS |
| s1175 | ses-11 | cuedTSWFlanker |
**Three repeated tasks with two complete files, paired by assumption:** `pick_run`'s median rule
does not apply since both scans and both files are complete, and no recoverable signal (mtimes,
durations, absolute timestamps) discriminates between them — so they are paired by **browser
download order** (bare `.csv` before `(1)`, `(11)` before `(12)`), assuming each file was
downloaded after its run. Condition order is randomised and differs on 24–97% of trials, so a
wrong assumption swaps that pair's events; treat these 6 runs as lower confidence than the rest.
| Subject | Session | Task | run-1 | run-2 |
|---|---|---|---|---|
| s76 | ses-12 | directedForgettingWFlanker | `... (11).csv` | `... (12).csv` |
| s247 | ses-12 | stopSignalWDirectedForgetting | `....csv` | `... (1).csv` |
| s1175 | ses-12 | cuedTSWFlanker | `....csv` | `... (1).csv` |

## T1w selection from MRIQC (discovery)
Discovery has 7 T1w images across 5 subjects; only s03 and s19 have two, so only those two need a
choice. Session-level MRIQC 24.0.2 IQMs:
| | s03 ses-05 | s03 ses-13 | | s19 ses-03 | s19 ses-05 |
|---|---|---|---|---|---|
| `cjv` (lower better) | 0.9755 | **0.6925** | | 0.7176 | **0.6971** |
| `cnr` (higher better) | 0.8667 | **1.4734** | | 1.3149 | **1.3357** |
| `snr_total` (higher) | 3.9246 | **5.3842** | | 4.6347 | **4.6496** |
| `efc` (lower) | 0.5363 | **0.5041** | | **0.4900** | 0.5018 |
| `fber` (higher) | 2536.7 | **7153.2** | | **4689.3** | 4429.8 |
| `wm2max` (lower) | 0.4036 | **0.3942** | | 0.4125 | **0.4016** |
| `qi_2` (lower) | 0.0009 | **0.0005** | | **0.0005** | 0.0007 |
**s03 → keep ses-13** (7-0). **s19 → keep ses-05** (4-3, near-equivalent). **Rule: CJV and CNR
decide first** — the most informative single metrics for INU and motion — so on a near-identical
pair the rest are tie-breakers only.

## T2w selection from MRIQC (discovery)
Six T2w across four subjects; s43 has none in any session, so **four of five** subjects end with
exactly one T2w. Only s19 and s29 had a choice.
| | s19 ses-01 | s19 ses-03 | | s29 ses-01 | s29 ses-04 |
|---|---|---|---|---|---|
| `cjv` (lower better) | **1.0038** | 1.0465 | | 1.0557 | **1.0180** |
| `cnr` (higher better) | **0.7450** | 0.7162 | | **0.7194** | 0.7018 |
| `snr_total` (higher) | **3.5656** | 3.5555 | | 3.8254 | **3.8578** |
| `efc` (lower) | 0.5272 | **0.5177** | | 0.5173 | **0.5132** |
| `wm2max` (lower) | 0.1942 | **0.1940** | | 0.1893 | **0.1877** |
| `qi_2` (lower) | **0.0009** | 0.0011 | | 0.0010 | **0.0007** |
**s19 → keep ses-01** (4-2), **s29 → keep ses-04** (5-1). `fber` is excluded from both tallies —
it reads `-1` for s19 ses-01 and for *both* s29 sessions, MRIQC's could-not-estimate sentinel
rather than a measurement — which would otherwise have flipped s19 to ses-03 on a number that
means nothing.

## Anat selection from MRIQC (validation)
41 subjects, all with at least one T1w and one T2w. Six needed a choice; MRIQC ran on all 48
anat-bearing sessions.
| Subject | Scan | Keep | Drop | Tally | Margin on the deciding metrics |
|---|---|---|---|---|---|
| s1127 | T1w | ses-09 | ses-01 | 6-1 | cjv 8.3%, cnr 7.4%, snr 5.2% |
| s1270 | T1w | ses-06 | ses-01 | 6-1 | cnr 9.5%, fber 16%, snr 7.9% |
| s216 | T1w | ses-11 | ses-01 | 5-2 | cjv 10%, cnr 14.2%, fber 15.8% |
| s1258 | T1w | ses-06 | ses-01 | 4-3 | cjv 1.9%, cnr 3.3% — both primaries agree |
| s1351 | T1w | ses-01 | ses-08 | 3-4 | cjv 2.6%, cnr 3.9% — **tally disagrees** |
| s1399 | T2w | ses-01 | ses-02 | 4-2 | cjv 4.8%, cnr 2.1%; `fber` excluded (-1) |
**The rule is CJV and CNR first**, same as discovery's T1w call above — on a near-identical pair
the other metrics move within noise. s1351 is where the raw tally (3-4) disagrees with the
primaries (both favour ses-01); decided on the primaries. `fber` is excluded wherever it reads
`-1` (could-not-estimate), affecting the CUBE PROMO T2w in particular.

## No distortion correction for one session
`sub-s1399/ses-12` has 3 BOLD runs (9 files) and no field map — fMRIPrep runs it uncorrected, the
only such session in either cohort. `sub-s03/ses-13` has a field map and T1w but **no functional
runs at all** (a standalone re-scan 14 months after the last task session), so `b0link` marks it
`orphan_fmap` and skips it — unlike the [stranded
fieldmaps](#split-scanner-visits-fieldmap-stranded) above, it shares its date with no other
session.

## The excluded cohort has no behavioural data

None of the 11 excluded subjects has a directory in the raw behavioural tree, so there is
nothing to reconcile and nothing in the canonical dataset for them. `ingest-beh` reports this
and exits 0 rather than failing, so a scripted run of all three cohorts does not stop on it.

## Keeping QA-rejected scans out of the next pull
A scan dropped after QA must not reappear on re-pull, so rejection is recorded **on Flywheel**:
`network_fmri qa-reject --target s03/05/T1w` appends `_qa-reject`, and `map_acquisition` returns
`None` for any matching label, so `export` never downloads it. Keyed at the source, not a table,
because the heuristic can't see the session — a subject-level skip would drop the kept scan too.

Applied so far, each verified by matching the acquisition's NIfTI byte size against the annex key
of the file deleted from BIDS rather than trusting the session numbering (rollback records:
`$SCRATCH/qa-reject-t1w.json`, `qa-reject-t2w.json`):
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
A fresh pull now curates exactly one T1w and one T2w per subject — T1w only for s43 (no T2w in any
session) — confirmed by replaying `map_acquisition` over every anat acquisition: all 41 validation
and 4 of 5 discovery subjects come back exactly 1/1. Rollback:
`$SCRATCH/qa-reject-{t1w,t2w,validation}.json`.

## Known data defect
`sub-s1165/ses-02` `task-directedForgetting` echoes 1–3 carry `SoftwareVersions` as a list:
```json
["28", "LX", "MR Software release:RX28.0_R04_UHP3T_2111.a"]
```
BIDS requires a string, so `excluded` fails validation with `JSON_SCHEMA_VALIDATION_ERROR`. The
DICOM tag is multi-valued for this scan; Flywheel stored the list and fw-heudiconv copied it
verbatim — a string in every other sidecar checked (15/15 in discovery), so this is upstream data,
not the heuristic. Fixing it means coercing sidecars post-export or editing Flywheel metadata —
neither is done.
