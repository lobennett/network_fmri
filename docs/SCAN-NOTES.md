# Scan and scientific decisions

This document records choices that cannot be reconstructed from filenames alone: what is
curated, which source records are corrected, how behavioral files are paired, and why
preprocessing differs from package defaults. For commands and stage order, use
[the README](../README.md). For Sherlock operations, use
[AGENT-ONBOARDING.md](AGENT-ONBOARDING.md).

## At a glance

| Decision | Current rule |
|---|---|
| Cohorts | discovery: 5 subjects; validation: 41; excluded: 11 |
| Initial BOLD removal | 7 volumes, or 10.43 seconds at TR 1.49 seconds |
| Event timing | shift onsets by −10.43 seconds; clip events to acquired duration |
| Anatomicals | one T1w per subject; one T2w where available |
| Fieldmaps | keep the first usable fieldmap; do not curate redundant trailing copies |
| fMRIPrep | subject level, `--dummy-scans 0`, `--no-submm-recon` |
| MRIQC motion threshold | `--fd_thres 0.5` |
| Output spaces | `MNI152NLin2009cAsym:res-2 T1w fsnative fsaverage6`, plus 91k CIFTI |
| Distortion-correction gap | `sub-s1399/ses-12` has no fieldmap |

The relevant implementation is primarily in `fw2bids/acquisitions.py`,
`fw2bids/sessions.py`, `prepare/trim.py`, and the pinned `network_events` package.
`network_fmri check` asserts the decisions that can be checked mechanically.

## Curation decisions

### Acquisition names and omissions

Functional labels normalize to `task-<bidsTask>_bold`, with a deduplication suffix when
needed. `TASKS` is an allowlist: an unknown task is skipped instead of becoming a misspelled
BIDS task. Anatomical, diffusion, and fieldmap labels use the canonical mappings in
`NON_FUNC`.

On 2026-08-18, 744 Flywheel acquisitions were normalized to resolve operator drift,
including ten spellings of `spatialTSWCuedTS`, `shapeMaching`, and `task_` prefixes.
The rollback record is `$SCRATCH/normalize-r01network.json`.

These acquisitions do not enter BIDS:

| Label or record | Reason |
|---|---|
| `3Plane Loc SSFSE`, `GE HOS FOV28*`, `HO Shim` | localizers and shims |
| `Processed Images*` | scanner-derived rather than source data |
| `run-1_sbref` | unused single-band reference |
| `T1w MPRAGE PROMO` | 4D motion-navigation series, not a valid T1w |
| `fmap-fieldmap_1` | redundant second fieldmap |
| subject `n01` | pilot with a different naming convention |
| `s29/22424` | fieldmap-only test session with no usable functional or behavioral data |

Four sessions contain a second fieldmap. In `s76/24392` and `s76/24425`, both copies have
the same timestamp. In `s76/25492` and `s1486/28061`, the second fieldmap occurs after all
runs. The leading map is therefore equivalent or temporally closer. A trailing map could
support field-drift QA, so source data remain on Flywheel, but time-partitioned
`B0FieldSource` would add no correction coverage.

### Source-record corrections

Five Flywheel sessions are filed under the wrong subject. Source reparenting returns
`403 Admin privileges are required`, so code maps them to their correct owner:

| Session | Filed under | Correct subject |
|---|---|---|
| `22752` | s03 | s10 |
| `22542` | s19-2 | s19 |
| `20210305` | s29-2 | s29 |
| `20201112` | s43-2 | s43 |
| `unknown2` | ex26207 | s297 |

Three visits were split into a functional session and a fieldmap-only container. Because
each map was acquired about 1.5 minutes before its twin's first BOLD,
`sessions.SESSION_MERGES` assigns both halves the same BIDS session:

| Subject | Fieldmap container | Functional twin | Fieldmap | First BOLD |
|---|---|---|---|---|
| s1258 | `unknown_2` | `28338` | 02:34:09 | 02:35:47 |
| s1391 | `unknown` | `28270` | 23:08:00 | 23:09:38 |
| s1445 | `unknown_5` | `28037` | 00:09:41 | 00:11:27 |

Visits for s247, s1270, s321, and s1326 remain separate because each half has a fieldmap
and a full task battery. `s03/ses-13` is intentionally anatomical plus fieldmap only.

Two labels in `s43/ses-11` were crossed. They were corrected on Flywheel on 2026-08-18;
trees exported earlier require re-importing s43. Rollback:
`$SCRATCH/relabel-s43-ses11.json`.

| Time | Volumes | Correct task |
|---|---:|---|
| 08:57:54 | 524 | stopSignalWDirectedForgetting, aborted |
| 09:13:20 | 368 | stopSignalWFlanker, complete |
| 09:29:30 | 103 | stopSignalWDirectedForgetting, aborted |

Six of 2,738 functional runs disagree with DICOM `SeriesDescription`. Only the s43 pair was
a real labeling error. The adjudication rule is volume count against the task's cohort
median. Four labels stand despite stale DICOM text:

| Run | Label retained | DICOM text | Evidence |
|---|---|---|---|
| s29/ses-01 | spatialTS | cuedTS | documented deliberate relabel |
| s1258/ses-02 | spatialTS | cuedTS | only spatialTS behavior; medians cannot distinguish |
| s320/ses-12 | stopSignalWFlanker | directedForgettingWCuedTS | 106% of flanker median; sibling marked `..._real_bold` |
| s03/ses-03 | nBack | goNogo | 96.5% of nBack median; sibling marked `actual_goNogo` |

## Anatomical QA decisions

Ten duplicate anatomical scans are rejected at source. `qa-reject` appends `_qa-reject`
to the acquisition label and sets `info.BIDS.ignore`; both are required because curation
adds tags and export honors old tags. `network_fmri qa-reject --apply` replays the
idempotent decision list. Each target was verified against the annex key of the removed
BIDS file.

| Rejected target | Flywheel session | Source label |
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

The result is one T1w for all 57 subjects and one T2w for 55; s43 and one excluded subject
have none. Rollback records are `$SCRATCH/qa-reject-{t1w,t2w,validation,ignore}.json`.

CJV and CNR are primary because they are most informative about intensity nonuniformity
and motion. SNR, EFC, FBER, WM2MAX, and QI2 break close cases. FBER values of −1 are
MRIQC's could-not-estimate sentinel and are excluded rather than ranked.

| Cohort | Subject/scan | Kept | Dropped | Metric tally | Decision evidence |
|---|---|---|---|---:|---|
| discovery | s03 T1w | ses-13 | ses-05 | 7–0 | CJV 29%, CNR 70% |
| discovery | s19 T1w | ses-05 | ses-03 | 4–3 | CJV 2.9%, CNR 1.6% |
| discovery | s19 T2w | ses-01 | ses-03 | 4–2 | CJV 4.1%, CNR 4.0%; FBER invalid |
| discovery | s29 T2w | ses-04 | ses-01 | 5–1 | primary metrics split; tally decides; FBER invalid |
| validation | s1127 T1w | ses-09 | ses-01 | 6–1 | CJV 8.3%, CNR 7.4%, SNR 5.2% |
| validation | s1270 T1w | ses-06 | ses-01 | 6–1 | CNR 9.5%, FBER 16%, SNR 7.9% |
| validation | s216 T1w | ses-11 | ses-01 | 5–2 | CJV 10%, CNR 14.2%, FBER 15.8% |
| validation | s1258 T1w | ses-06 | ses-01 | 4–3 | both primary metrics agree |
| validation | s1351 T1w | ses-01 | ses-08 | 3–4 | primary metrics override tally |
| validation | s1399 T2w | ses-01 | ses-02 | 4–2 | CJV 4.8%, CNR 2.1%; FBER invalid |

Excluding invalid FBER matters: s19's T2w would otherwise flip to the rejected scan.

## Timing and behavioral alignment

### BOLD trimming

Every BOLD loses its first seven volumes:

```text
N_DUMMY = 7
TR = 1.49 seconds
onset shift = 7 × 1.49 = 10.43 seconds
```

Trimmed sidecars record `NumberOfVolumesDiscardedByUser: 7`. Because neither the scanner
nor raw behavioral files adjust their clock, `network_events` shifts event onsets by
−10.43 seconds. This is silent but essential: revisit it whenever `N_DUMMY` or TR changes.

### Behavioral file pairing

`sourcedata/<sub>/<ses>/beh/` is canonical, with one CSV named for its corresponding BOLD.
Raw filenames have no run index; browser suffixes such as `(1)` are download counters.
Practice files are excluded. Pairing was resolved once and preserved with provenance in
the canonical Oak dataset.

Five false-start scans receive no CSV because a complete repeated run exists:

| Subject/session | Task | Paired scan | Dropped scan |
|---|---|---|---|
| s10/ses-01 | goNogo | run-2 | run-1, 38 volumes |
| s29/ses-12 | directedForgettingWFlanker | run-2 | run-1, 61 volumes |
| s43/ses-11 | stopSignalWDirectedForgetting | run-1 | run-2, 103 volumes |
| s336/ses-05 | goNogo | run-2 | run-1, 298 volumes |
| s216/ses-05 | directedForgetting | run-2 | run-1, 94 volumes |

Eight scanned runs have no recoverable behavioral file and cannot be modeled:

| Subject/session | Task |
|---|---|
| s03/ses-01 | nBack |
| s19/ses-02 | goNogo |
| s29/ses-02 | goNogo |
| s19/ses-11 | directedForgettingWFlanker |
| s1292/ses-04 | nBack |
| s300/ses-08 | flanker |
| s180/ses-12 | shapeMatchingWCuedTS |
| s1175/ses-11 | cuedTSWFlanker |

For s19/ses-11, only a processed `iti_adjusted_events` TSV survives. The adjacent-session
files for s180 and s1175 are correctly consumed by those sessions and cannot be reused.

Six scans in three sessions are paired with lower confidence. Each session has two
complete scans and two complete files, with no discriminating timestamp or duration, so
browser download order is assumed:

| Subject/session | Task | run-1 | run-2 |
|---|---|---|---|
| s76/ses-12 | directedForgettingWFlanker | `(11).csv` | `(12).csv` |
| s247/ses-12 | stopSignalWDirectedForgetting | bare `.csv` | `(1).csv` |
| s1175/ses-12 | cuedTSWFlanker | bare `.csv` | `(1).csv` |

Condition order differs on 24–97% of trials, so swapping one of these pairs would change
its design. Discovery contains 288 subject/session/task units: 221 one-to-one pairs,
60 rest, four absent, and three false starts. Validation pairs 1,887 of 1,893 non-rest
runs. Excluded subjects have no behavioral directories; `ingest-beh` reports that and
exits successfully.

### Events beyond the acquired scan

Twenty-two aborted runs originally had event onsets beyond the actual timeseries. The
worst was `sub-s1391/ses-07/task-shapeMatching`: 348.7 seconds of scan and 1,626.1 seconds
of events. `network_events` reads the NIfTI length, not
`NumberOfTemporalPositions`, which can contain the intended rather than acquired count.

Onsets are clipped to the scan. Durations are not shortened: a trial that began during
acquisition remains real even if its boxcar extends beyond the final volume. Twelve runs
therefore have a last duration extending at most 6.7 seconds beyond acquisition. All 22
retain every real `trial_type`; only `unknown` and `n/a` categories are lost.
`network_qa` uses `scan_*` retention fields in the truncation sidecar to decide whether a
run remains usable.

Modal volume count is not a valid substitute because task length varies: it flags 870 of
2,738 acquisitions and misses 12 of the 22 true overruns. Seven short scans have no events
at all; behavioral pairing already excludes them, so they cost preprocessing time but
cannot enter a model.

## Preprocessing decisions

These values are explicit in `docs/campaign/mechababs-config.yaml` and are scientific
choices, not accidental defaults.

### MRIQC and fMRIPrep

- `--dummy-scans 0`: the BIDS tree is already trimmed and event onsets are already shifted.
  Automatic removal would double-discard volumes.
- `--no-submm-recon`: native T1w voxels are 0.5 × 0.5 × 0.8 mm, but 0.5 mm FreeSurfer took
  20–40 hours, increased topological defects, and adds no useful precision for 2.8 mm BOLD.
  The 1 mm conform matched the tested earlier 25.2.4 surfaces. Revisit for anatomical
  rather than functional analyses.
- MRIQC `--fd_thres 0.5`: `fd_num` and `fd_perc` are defined at the study's 0.5 mm motion
  threshold. `network_qa` checks the recorded threshold before using those IQMs.

The earlier proportion-of-frames `std_dvars > 1.5` criterion cannot be recovered from
MRIQC's mean `dvars_std`. A mean-based substitute excluded no run beyond FD. Mean DVARS
remains evidence but is not an exclusion rule.

fMRIPrep runs once per subject across sessions. This shares anatomy and FreeSurfer work
and avoids session-level BABS matching failures on subjects with one anatomical session.
XCP-D is also subject-level because chained BABS producer and consumer levels must match.

For nine subjects, T2w is in a different session from the retained T1w and cannot
contribute to pial refinement. This affects three discovery and six validation subjects;
choosing the better T1w took priority over T2w-assisted refinement.

### Analysis spaces

The requested outputs are:

```text
MNI152NLin2009cAsym:res-2 T1w fsnative fsaverage6
--cifti-output 91k
```

CIFTI also brings in `MNI152NLin6Asym` for fsLR. `network_glm` defaults to
MNI152NLin2009cAsym. fsaverage6 supports across-subject surface comparison; fsnative
retains each subject's native mesh. These spaces are deliberate and are not interchangeable
without resampling and corresponding metadata.

## Known limitations

- `sub-s1399/ses-12` has three BOLD runs and no fieldmap. It is the only analyzed session
  without distortion correction.
- `sub-s03/ses-13` and `sub-s297/ses-01` are fieldmap-only sessions. `b0link` correctly
  records them as `orphan_fmap`.
- `sub-s1165/ses-02/task-directedForgetting` has a list-valued DICOM
  `SoftwareVersions`. BIDS requires a string, so `fix-sidecars` coerces it after export;
  Flywheel metadata remains unchanged.
- Six ambiguous behavioral pairings depend on browser download order, as listed above.
- T2w-assisted pial refinement is unavailable for nine subjects, as described above.
