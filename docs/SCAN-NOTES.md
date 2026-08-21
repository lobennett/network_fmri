# Scan notes

Per-scan decisions: what is curated, what is dropped and where, and which source records are
wrong. Code is in `fw2bids/acquisitions.py` (labels) and `fw2bids/sessions.py` (subjects and
sessions). For the pipeline itself see [../README.md](../README.md).

Anything that "fails silently" below produces no validation error and no visible artefact — only
wrong first-level models. `network_fmri check` asserts the ones that can be asserted.

---

## 1. Curation: what reaches BIDS at all

### Acquisition naming
Functional labels are canonical `task-<bidsTask>_bold`, optionally with a `_1`/`_run_2` dedup
suffix. `TASKS` is an allowlist, so an unrecognised task name is skipped rather than curated under
a misspelled entity. anat, dwi and fmap have one spelling each, in `NON_FUNC`.

744 acquisitions were normalised on Flywheel on 2026-08-18, collapsing years of operator drift
(ten spellings of `spatialTSWCuedTS`, the `shapeMaching` typo, `task_` for `task-`) into a rule
plus six rows. Rollback: `$SCRATCH/normalize-r01network.json`.

### Not curated
| Label | Reason |
|---|---|
| `3Plane Loc SSFSE`, `GE HOS FOV28*`, `HO Shim` | localizers and shims |
| `Processed Images*` | scanner-derived, not source data |
| `run-1_sbref` | single-band reference, unused downstream |
| `T1w MPRAGE PROMO` | 4D motion-nav series; not a valid `_T1w` (`T1W_FILE_WITH_TOO_MANY_DIMENSIONS`) |
| `fmap-fieldmap_1` | a second fieldmap that cannot improve SDC — see below |

Also skipped: subject `n01` (the pilot, a different naming convention entirely) and `s29/22424`
(an fmap-only test session, single-echo, no usable functional or behavioural data).

### The second fieldmap
Four sessions have two, and none is a mid-session re-shim:

| Session | Ordering |
|---|---|
| s76 / 24392 | both at the same timestamp (00:20:14), before any run |
| s76 / 24425 | both at the same timestamp (00:03:23), before any run |
| s76 / 25492 | fmap 23:14:43 → 3 runs → fmap_1 at 00:03:48, after every run |
| s1486 / 28061 | fmap 02:13:36 → 5 runs → fmap_1 at 03:21:03, after every run |

Two are one acquisition landing in two Flywheel containers, so either copy is equivalent; the
other two trail every run, and the leading fieldmap taken 1–2 min before the first run is a
strictly better match than one taken 20–50 min later. No run here is ever preceded by a fieldmap it
does not already get, so time-partitioned `B0FieldSource` would add machinery for no gain. A
trailing fieldmap could still support a field-drift QA signal; the data stays on Flywheel.

---

## 2. Wrong source records, compensated in code

Five sessions sit under the wrong subject. None is fixed at source — reparenting returns `403
Admin privileges are required to move a session to a Core edition project`.

| Session | Filed under | Belongs to |
|---|---|---|
| `22752` | s03 | s10 — a different participant |
| `22542` | s19-2 | s19 — duplicate subject record |
| `20210305` | s29-2 | s29 |
| `20201112` | s43-2 | s43 |
| `unknown2` | ex26207 | s297 |

### Split visits leave a fieldmap stranded
Flywheel split three single visits into two sessions, putting the fieldmap in a container with no
functional runs. Each was acquired ~1.5 min before its twin's first BOLD, so it belongs to those
runs:

| Subject | Stray session | Twin | fmap | First BOLD |
|---|---|---|---|---|
| s1258 | `unknown_2` | `28338` | 02:34:09 | 02:35:47 |
| s1391 | `unknown` | `28270` | 23:08:00 | 23:09:38 |
| s1445 | `unknown_5` | `28037` | 00:09:41 | 00:11:27 |

Reparenting is refused here too, so `sessions.SESSION_MERGES` gives the stray session its twin's
number: the fieldmap joins the twin's BIDS session, the empty container stops consuming a number,
and each subject goes 13 → 12 sessions with behavioural numbering lining up 1:1.

Not merged, since both halves already have their own fieldmap and full battery: s247 (ses-09/10),
s1270 (ses-11/12), s321 (ses-01/02), s1326 (ses-03/04). `s03/ses-13` is anat+fmap only by design.

### Crossed labels on s43 / ses-11 — fixed at source
Session 22890 had two `stop_with_*` labels on the wrong scans:

| Acq time | Volumes | Was labelled | Actually is |
|---|---|---|---|
| 08:57:54 | 524 (73% of median) | stopSignalWDirectedForgetting | correct, aborted |
| 09:13:20 | 368 (99.5% of median) | stopSignalWDirectedForgetting_1 | **stopSignalWFlanker**, complete |
| 09:29:30 | 103 | stopSignalWFlanker | **stopSignalWDirectedForgetting**, aborted |

Swapped on Flywheel 2026-08-18 (rollback `$SCRATCH/relabel-s43-ses11.json`). Both behavioural CSVs
exist, so nothing is ambiguous once the labels are right — but **any tree exported before the fix
carries the wrong task names and needs s43 re-imported.**

### Label vs DICOM SeriesDescription
6 of 2738 func runs disagree. Only the s43 pair above was a real error; the rest have a stale DICOM
field. **Adjudication rule: volume count against the task's cohort median decides.** Where volumes
agree with the label the label stands; where they contradict it and match the DICOM task, the label
is wrong.

| Case | Label | DICOM says | Why the label wins |
|---|---|---|---|
| s29 / ses-01 | spatialTS | cuedTS | a documented deliberate relabel |
| s1258 / ses-02 | spatialTS | cuedTS | only spatialTS behavioural exists; medians (335 vs 336) cannot discriminate |
| s320 / ses-12 | stopSignalWFlanker | directedForgettingWCuedTS | 106% of flanker median vs 75%; sibling marked `..._real_bold` |
| s03 / ses-03 | nBack | goNogo | 96.5% of nBack median vs 127%; sibling marked `actual_goNogo` |

---

## 3. Duplicate anatomicals: 10 scans rejected at source

A scan dropped after QA must not reappear on re-pull, so rejection is recorded **on Flywheel**.
`qa-reject` appends `_qa-reject` to the acquisition label, which makes `map_acquisition` return
`None`, *and* sets `info.BIDS.ignore` on the files — both are needed, because `curate` only ever
adds tags and `export` honours a tag an earlier run wrote. Keyed at the source rather than in a
table because the heuristic cannot see the session; a subject-level skip would drop the kept scan
too.

The list below is `qa_reject.REJECTS`, so `network_fmri qa-reject --apply` replays all of it and a
fresh Flywheel project reaches this state from the repo alone. Marking is idempotent. Each was
verified by matching the acquisition's NIfTI byte size against the annex key of the file deleted
from BIDS, rather than trusting session numbering.

| Target | Flywheel session | Label |
|---|---|---|
| s03 ses-05 T1w | 22734 | `NEW Sag_MPRAGE_T1` |
| s19 ses-03 T1w | 22542 | `NEW Sag_MPRAGE_T1` |
| s19 ses-03 T2w | 22542 | `T2w CUBE PROMO .8mm sag` |
| s29 ses-01 T2w | 20201113 | `T2w CUBE PROMO .8mm sag` |
| s1127 ses-01 T1w | 27774 | `NEW Sag_MPRAGE_T1` |
| s1258 ses-01 T1w | 27821 | `NEW Sag_MPRAGE_T1` |
| s1270 ses-01 T1w | 27820 | `NEW Sag_MPRAGE_T1` |
| s1351 ses-08 T1w | 28579 | `NEW Sag_MPRAGE_T1` |
| s216 ses-01 T1w | 26051 | `NEW Sag_MPRAGE_T1` |
| s1399 ses-02 T2w | 28131 | `T2w CUBE PROMO .8mm sag` |

Result: exactly one T1w per subject (57/57) and one T2w (55/57 — s43 has none in any session, nor
one excluded subject). Rollback records: `$SCRATCH/qa-reject-{t1w,t2w,validation,ignore}.json`.

### How the choices were made
**CJV and CNR decide first** — the most informative single metrics for INU and motion — with the
others as tie-breakers on a near-identical pair. `fber` is excluded wherever it reads `-1`, which
is MRIQC's could-not-estimate sentinel rather than a measurement; it affects the CUBE PROMO T2w in
particular and would otherwise have flipped s19's T2w on a number that means nothing.

All eight choices, tallied over `cjv`, `cnr`, `snr_total`, `efc`, `fber`, `wm2max`, `qi_2`. The
underlying IQMs are in each cohort's MRIQC derivatives.

| Cohort | Subject | Scan | Keep | Drop | Tally | Margin on the deciding metrics |
|---|---|---|---|---|---|---|
| discovery | s03 | T1w | ses-13 | ses-05 | 7-0 | cjv 29%, cnr 70% — unambiguous |
| discovery | s19 | T1w | ses-05 | ses-03 | 4-3 | cjv 2.9%, cnr 1.6% — near-equivalent |
| discovery | s19 | T2w | ses-01 | ses-03 | 4-2 | cjv 4.1%, cnr 4.0%; `fber` excluded (-1) |
| discovery | s29 | T2w | ses-04 | ses-01 | 5-1 | cjv 3.6% for ses-04 but cnr 2.5% for ses-01 — primaries split, decided on the tally; `fber` excluded (-1 both) |
| validation | s1127 | T1w | ses-09 | ses-01 | 6-1 | cjv 8.3%, cnr 7.4%, snr 5.2% |
| validation | s1270 | T1w | ses-06 | ses-01 | 6-1 | cnr 9.5%, fber 16%, snr 7.9% |
| validation | s216 | T1w | ses-11 | ses-01 | 5-2 | cjv 10%, cnr 14.2%, fber 15.8% |
| validation | s1258 | T1w | ses-06 | ses-01 | 4-3 | cjv 1.9%, cnr 3.3% — both primaries agree |
| validation | s1351 | T1w | ses-01 | ses-08 | 3-4 | cjv 2.6%, cnr 3.9% — **tally disagrees**, decided on the primaries |
| validation | s1399 | T2w | ses-01 | ses-02 | 4-2 | cjv 4.8%, cnr 2.1%; `fber` excluded (-1) |

Excluding `fber` changed an outcome: it reads `-1` for s19 ses-01 and for both s29 sessions, and
s19's T2w would have flipped to ses-03 on that non-measurement.

---

## 4. Trimming shifts the BOLD clock by 10.43 s

`trim` removes the first 7 volumes of every BOLD, so **every trimmed run starts 10.43 s later than
the scanner did**. Neither this repo nor the raw behavioural files adjust for it, so `events.tsv`
onsets must be shifted by −10.43 s — `network_events`'s job. This fails silently. Re-check it if
`N_DUMMY` or TR ever changes.

```
N_DUMMY = 7        (network_fmri.prepare.trim)
TR      = 1.49 s   (sidecar RepetitionTime)
shift   = 10.43 s
```

Trimmed files carry `NumberOfVolumesDiscardedByUser: 7`, which is how a consumer tells a trimmed
run from an untrimmed one, and what `check --only trim` asserts.

---

## 5. Behavioural pairing: which run got which CSV

`sourcedata/<sub>/<ses>/beh/` is canonical — one CSV per BOLD run, named for the run it belongs to.
The raw filenames encode **no run index**, so pairing had to be derived; it is frozen in the
canonical dataset on `$OAK` with its own provenance, and this repo no longer reads the raw tree.

Three raw filename regimes coexist, none with a run index. `(N)` suffixes are browser-download
counters; out-of-scanner practice data lives in `practice/` and is excluded.

```
raw               177   go_nogo_single_task_network__fmri_results (12).csv
clean-hyphen       41   sub-s03_ses-1_task-go-nogo_desc-raw.csv
clean-underscore    6   sub-s29_ses_11_task-directed_forgetting_with_flanker_desc_raw.csv
```

### False starts: 5 runs get no CSV
Resolved by volume count against the task median — the aborted run of a repeated pair is dropped.

| Subject | Session | Task | Paired with | Dropped |
|---|---|---|---|---|
| s10 | ses-01 | goNogo | run-2 | run-1 (38 vols) |
| s29 | ses-12 | directedForgettingWFlanker | run-2 | run-1 (61 vols) |
| s43 | ses-11 | stopSignalWDirectedForgetting | run-1 (524 vols) | run-2 (103 vols — crossed labels, §2) |
| s336 | ses-05 | goNogo | run-2 (382 = median) | run-1 (298) |
| s216 | ses-05 | directedForgetting | run-2 (434) | run-1 (94) |

### Behavioural file absent entirely: 8 runs get no events
Searched `raw_cleaned` and every archive collection. These runs were scanned but no file exists, so
they cannot be modelled.

| Subject | Session | Task |
|---|---|---|
| s03 | ses-01 | nBack |
| s19 | ses-02 | goNogo |
| s29 | ses-02 | goNogo |
| s19 | ses-11 | directedForgettingWFlanker (only a processed `iti_adjusted_events` TSV survives) |
| s1292 | ses-04 | nBack |
| s300 | ses-08 | flanker |
| s180 | ses-12 | shapeMatchingWCuedTS |
| s1175 | ses-11 | cuedTSWFlanker |

s180 and s1175 each have one file for the same task in the adjacent session, but it is correctly
consumed by that session's own scan — the task was run twice with only one file saved.

### Paired by assumption: 6 runs, lower confidence
Three sessions repeated a task with **two complete files and two complete scans**, so the median
rule does not apply and no recoverable signal (mtimes, durations, absolute timestamps)
discriminates. They are paired by browser download order (bare `.csv` before `(1)`, `(11)` before
`(12)`), assuming each file was downloaded after its run. Condition order is randomised and differs
on 24–97% of trials, so a wrong assumption swaps that pair's events.

| Subject | Session | Task | run-1 | run-2 |
|---|---|---|---|---|
| s76 | ses-12 | directedForgettingWFlanker | `... (11).csv` | `... (12).csv` |
| s247 | ses-12 | stopSignalWDirectedForgetting | `....csv` | `... (1).csv` |
| s1175 | ses-12 | cuedTSWFlanker | `....csv` | `... (1).csv` |

### Totals
Discovery: 288 (subject, session, task) units — 221 pair 1:1, 60 `rest`, 4 absent, 3 false starts.
Validation: 1887 of 1893 non-rest runs get a CSV.
Excluded: **none of the 11 subjects has a directory in the raw behavioural tree**, so there is
nothing to reconcile and nothing in the canonical dataset. `ingest-beh` reports this and exits 0,
so a scripted run of all three cohorts does not stop on it.

---

## 6. Events truncation: 22 runs clipped to the scan

A run aborted at the scanner leaves the behavioural session running, so the CSV records trials that
were never imaged. Nothing clipped them, so 22 runs carried `events.tsv` rows past the end of their
timeseries — worst case `sub-s1391/ses-07` `task-shapeMatching`, 348.7 s of scan against 1626.1 s of
events. A first-level model on those builds regressors for timepoints that do not exist.

Fixed in `network_events` (`c5f653b`): onsets clip to the acquired length, read from the NIfTI
because the sidecar's `NumberOfTemporalPositions` records the *intended* count — `sub-s19/ses-07`
claims 524 for a 223-volume scan. The clip's cost is reported under `scan_*` keys in the truncation
sidecar, so `network_qa` decides whether the surviving run is worth modelling.

**No scan needs rejecting for this.** All 22 keep every real `trial_type` after clipping (retention
0.50–1.00); the only losses are the `unknown` and `n/a` junk categories.

`onset` is clipped, `duration` is not, so 12 runs still hold a final trial whose box-car ends up to
6.7 s past the last volume. Deliberate: the trial was presented and its onset is inside the scan, so
the design matrix simply has no timepoints for the tail. Truncating `duration` would misstate the
stimulus; dropping the trial would discard real data.

**Volume count is the wrong test for this.** Task duration varies by design, so "shorter than the
task's modal length" flags 870 of 2738 acquisitions and still misses 12 of the 22 overruns, which
sit above 0.60 of modal. Audit scripts: `$SCRATCH/nf_audit/`.

Separately, 7 short scans have no `events.tsv` at all (`sub-s19/ses-02` goNogo at 22 volumes,
`sub-s599/ses-02` rest at 31, and five where a complete run-2 exists). The behavioural pairing
already rejected them, so they cannot reach a model and cost only preprocessing time.

---

## 7. Preprocessing flags that are decisions, not defaults

Three fMRIPrep/MRIQC settings were chosen against their defaults. Each is set in the
mechababs campaign at `code/mechababs/pipelines/`, and each would fail silently if wrong.

**`--dummy-scans 0`** (fMRIPrep). The tree is already trimmed — `trim` removes 7 volumes and
stamps `NumberOfVolumesDiscardedByUser`, and `network_events` shifted onsets by −10.43 s to
match — so fMRIPrep must discard nothing further. Explicit rather than left to
auto-detection, which could vary between runs. The study's earlier
`fmriprep_25.2.4` derivatives ran on an *untrimmed* tree, which is why their confounds carry
seven `non_steady_state_outlier` columns and ours should carry none.

**`--no-submm-recon`** (fMRIPrep). Every T1w is 0.5 × 0.5 × 0.8 mm, so the default would run
FreeSurfer at 0.5 mm isotropic with `-hires -cm`. On this data that is worse and slower: it
raises topological defects (thinner voxels amplify noise-driven segmentation error over ~8×
the vertices) and takes 20–40 h against a 24 h wall. The 1 mm conform gives 2–11 holes per
hemisphere across the five discovery subjects, and the BOLD is 2.8 mm, so surface precision
below 1 mm buys nothing downstream. Also matches the 25.2.4 derivatives, keeping the two
sets comparable. Revisit only if an analysis becomes anatomical (thickness, myelin, fine
parcellation) rather than functional.

**`--fd_thres 0.5`** (MRIQC). `fd_num`/`fd_perc` count frames above *this* threshold, and
0.5 mm is the study's task-scan motion criterion — so `network_qa` thresholds the IQM
directly instead of recomputing framewise displacement from fMRIPrep confounds. Motion
exclusions therefore need no fMRIPrep output and are known before preprocessing.
`network_qa` reads `provenance.settings.fd_thres` out of each IQM and refuses a mismatch,
because the same `fd_perc` number means something different at another threshold.

### Output spaces
`MNI152NLin2009cAsym:res-2 T1w fsnative fsaverage6` plus `--cifti-output 91k`, matching what
25.2.4 produced. `MNI152NLin6Asym` appears as well — not requested, but `--cifti-output`
pulls it in for the fsLR path. `network_glm`'s `--mni-template` defaults to 2009cAsym to
agree with the requested space.

### T2w is invisible to the anat stage for 9 subjects
Anat units are session-scoped, so a T2w in a different session than the T1w cannot
contribute to pial refinement (fMRIPrep logs "No T2w images provided - skipping Stage 7").
This affects 3 of 5 discovery and 6 of 41 validation subjects — and five of those six are
subjects whose T1w moved sessions because of the `_qa-reject` choice in §3. Better
anatomical quality was traded for T2w-assisted refinement.

## 8. Known gaps

**No distortion correction for one session.** `sub-s1399/ses-12` has 3 BOLD runs and no field map,
so fMRIPrep runs it uncorrected — the only such session in either analysed cohort.

**Fieldmap-only sessions are skipped by `b0link`.** `sub-s03/ses-13` (a standalone re-scan 14
months after the last task session) and `sub-s297/ses-01` have a field map but no functional runs,
so there is nothing to link; `b0link` marks them `orphan_fmap`. `check --only b0link` allows this.

**Multi-valued `SoftwareVersions` on one scan.** `sub-s1165/ses-02` `task-directedForgetting`
echoes 1–3 carry it as a list, which BIDS requires to be a string:

```json
["28", "LX", "MR Software release:RX28.0_R04_UHP3T_2111.a"]
```

Upstream data, not the heuristic (a string in 15/15 discovery sidecars checked). `fix-sidecars`
coerces it post-export; the Flywheel metadata is left alone.
