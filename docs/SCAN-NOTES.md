# Scan and scientific decisions

This is the scientific and source-data record for the single 46-subject BIDS dataset.
Historical adjudications are evidence, not automatic drops. The current pipeline writes
MRIQC evidence and requires explicit approval in `scan_decisions.tsv` before curation.

| Decision | Current rule |
|---|---|
| Dataset | one final 46-subject BIDS dataset |
| Initial BOLD removal | 7 volumes (10.43 seconds at TR 1.49 seconds) |
| Events | shift by −10.43 seconds and clip onsets outside acquired duration |
| MRIQC motion evidence | echo-2; mean FD ≥0.2 mm for rest; task: mean FD ≥0.2 mm or ≥20% frames at FD ≥0.5 mm |
| Anatomy | more than one T1w or T2w, or no T2w, requires review and an MRIQC recommendation |
| Echoes | a missing echo-1, echo-2, or echo-3 requires review |
| fMRIPrep | one subject across sessions, `--dummy-scans 0`, `--no-submm-recon` |
| Output spaces | `MNI152NLin2009cAsym:res-2 T1w fsnative fsaverage6`, with 91k CIFTI |

## Historical source curation

Scanner localizers and shims, processed images, `run-1_sbref`, `T1w MPRAGE PROMO`, and
known redundant trailing fieldmaps are not BIDS acquisitions. `n01` was a pilot with an
incompatible naming convention; `s29/22424` was a fieldmap-only test session. The pinned
conversion package owns current mapping rules; these notes remain review evidence.

Five Flywheel sessions were historically filed under another participant: `22752`
(s03→s10), `22542` (s19-2→s19), `20210305` (s29-2→s29), `20201112` (s43-2→s43), and
`unknown2` (ex26207→s297). Fieldmap-only containers for s1258, s1391, and s1445 belong
with their functional twin because the fieldmap preceded the first BOLD run.
`s03/ses-13` is intentionally anatomical plus fieldmap only.

The s43/ses-11 task labels were corrected on Flywheel on 2026-08-18; old exports must be
replaced. Ten previously selected duplicate anatomicals are historical candidates for
review, not automatic exclusions: s03/ses-05 T1w; s19/ses-03 T1w and T2w; s29/ses-01
T2w; s1127/ses-01 T1w; s1258/ses-01 T1w; s1270/ses-01 T1w; s1351/ses-08 T1w;
s216/ses-01 T1w; and s1399/ses-02 T2w. MRIQC CJV and CNR were most useful in the
original comparisons; invalid FBER values (−1) must not be ranked.

## Behavioral history

Canonical behavioral sourcedata is organized one file per logical BOLD acquisition. It
preserves raw CSV content and provenance; the runtime pipeline does not infer pairings.

Known BOLD scans with no recoverable behavior remain in BIDS and are recorded in
`behavioral_exceptions.tsv`: s03/ses-01 nBack, s19/ses-02 goNogo, s29/ses-02 goNogo,
s19/ses-11 directedForgettingWFlanker, s1292/ses-04 nBack, s300/ses-08 flanker,
s180/ses-12 shapeMatchingWCuedTS, and s1175/ses-11 cuedTSWFlanker.

Five historical false starts have no behavioral CSV because a complete repeated run
exists: s10/ses-01 goNogo run-1, s29/ses-12 directedForgettingWFlanker run-1,
s43/ses-11 stopSignalWDirectedForgetting run-2, s336/ses-05 goNogo run-1, and
s216/ses-05 directedForgetting run-1. Six pairings remain lower-confidence because only
browser download order distinguished equivalent files: s76/ses-12, s247/ses-12, and
s1175/ses-12. Their canonical provenance remains visible during modeling review.

Twenty-two aborted runs had behavioral onsets beyond acquired BOLD duration. Events keep
trials beginning during acquisition, do not shorten their durations, and record loss
evidence under `sourcedata/events_qc/`.

## Preprocessing and known limitations

The BOLD tree is trimmed exactly once before event generation. Sidecars record
`NumberOfVolumesDiscardedByUser: 7`; fMRIPrep must use `--dummy-scans 0`. MRIQC uses
`--fd_thres 0.5`, and the decision generator records `dvars_std` as evidence.

`sub-s1399/ses-12` has BOLD runs without a fieldmap. `sub-s03/ses-13` and
`sub-s297/ses-01` are fieldmap-only sessions. Nine subjects have T2w in a different
session from the retained T1w, so it cannot refine the pial surface. These conditions
remain reviewable evidence in the decisions manifest.
