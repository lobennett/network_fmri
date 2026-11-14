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
| FreeSurfer | standalone FreeSurfer 8.2.0 runs `recon-all`; every subject's white and pial surfaces require explicit approval before full fMRIPrep |
| fMRIPrep | one subject across sessions, `--dummy-scans 0`, `--no-submm-recon`, `--project-goodvoxels` |
| Output spaces | `MNI152NLin2009cAsym:res-2 T1w fsnative fsaverage6`, with 91k CIFTI |

## Historical source curation

Scanner localizers and shims, processed images, `run-1_sbref`, `T1w MPRAGE PROMO`, and
known redundant trailing fieldmaps are not BIDS acquisitions. `n01` was a pilot with an
incompatible naming convention. Session `s29/22424` was excluded from the canonical
sample and is preserved under Flywheel subject `qa-reject-s29`.

Four Flywheel sessions formerly filed under another participant were moved to their
canonical subjects: `22752` (s03→s10), `22542` (s19-2→s19), `20210305`
(s29-2→s29), and `20201112` (s43-2→s43). Fieldmap-only containers for s1258, s1391,
and s1445 belong with their functional twin because the fieldmap preceded the first BOLD run.
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

An unrecoverable run has BOLD data but no behavioral CSV in the raw, cleaned, or
canonical sources. These runs remain in BIDS and are recorded in
`behavioral_exceptions.tsv`: s03/ses-01 nBack, s19/ses-02 goNogo, s29/ses-02 goNogo,
s19/ses-11 directedForgettingWFlanker, s1292/ses-04 nBack, s300/ses-08 flanker,
s180/ses-12 shapeMatchingWCuedTS, and s1175/ses-11 cuedTSWFlanker.

LB approved task-first-level exclusions on 2026-10-10 for s03/ses-01 nBack,
s29/ses-02 goNogo, s300/ses-08 flanker, and s1292/ses-04 nBack. Event generation
installs these reviewed rows from the packaged `data/analysis_exclusions.tsv`,
preserving existing exclusions and rejecting conflicting decisions. Imaging stays
available for preprocessing and timeseries analyses. The archived s1292/ses-04
CSV duplicates the file assigned to the separate ses-05 nBack acquisition; it
must not be reused for ses-04. This finding does not establish who caused the
duplication.

LB also provisionally excluded s1134/ses-08 flanker from task-first-level models
on 2026-10-10. Its single omission response is entirely covered by motion-outlier
regressors, making both designs singular. Retain imaging; revisit eligibility
after explicit handling of nonestimable contrasts.

Five false starts were resolved in Flywheel by adding `_qa-reject` to their acquisition
labels. Conversion skips them before assigning run numbers, so the completed acquisition
is `run-1`. The affected scans are s10/ses-01 goNogo,
s29/ses-12 directedForgettingWFlanker, s43/ses-11 stopSignalWDirectedForgetting,
s336/ses-05 goNogo, and s216/ses-05 directedForgetting. Six pairings remain
lower-confidence because only
browser download order distinguished equivalent files: s76/ses-12, s247/ses-12, and
s1175/ses-12. Their canonical provenance remains visible during modeling review.

Twenty-two aborted runs had behavioral onsets beyond acquired BOLD duration. Events keep
trials beginning during acquisition, do not shorten their durations, and record loss
evidence under `sourcedata/events_qc/`.

Out-of-scanner behavior is a finalized DataLad input pinned in the workflow configuration
and installed inside the canonical BIDS dataset. It contains 1,263 raw CSVs from 37 subjects and
137 QA files. Thirty-four subjects have all 36 tasks; s373 has 30, s1314 has 6, and
s1391 has 3. Nine subjects have no out-of-scanner files: s03, s10, s19, s29, s43,
s599, s1057, s1338, and s1481. Its `missing_behavior.tsv` records all 393 absent
subject-task combinations, while `source_manifest.tsv` records hashes and original
paths. Existing QA exclusions remain evidence; no available raw behavior was removed.

## Preprocessing and known limitations

The corrected campaign uses fMRIPrep 25.2.7-rc1 with `--project-goodvoxels`, 91k CIFTI,
and niworkflows 1.15.3. Its BOLD `init_aff` transform is rigid, retaining affine
refinement and N4. The build receipt records the reported fMRIPrep version
`25.2.7.dev5+g7cf086b9b` and image checksum. Original sub-s03 pilot derivatives lack
the good-voxel flag and remain historical references, not the current campaign.
The original affine initializer can misplace the atlas brain weights and severely
distort BOLD-reference contrast. A matched reference test reproduced the failure;
the corrected full s03/ses-09 goNogo run restored its final T1w-reference contrast
(brain median 12 → 1,184). This validates that scan, not every registration in the
sample. Preserve the original pilot provenance and review regenerated outputs.

The rigid initializer does not eliminate seed-dependent failures. Historical
s29/ses-07 shapeMatching diagnostics reproduced a misplaced weighting map with
Dice overlap 0.037 against the archived brain mask, despite an N4 brain median
near 1,559. The paired spatialTS reference had overlap 0.905. Consequently, low
brain median alone is not a sufficient screen. Preserve both registration and
intensity evidence, including borderline partial-field-of-view cases.

The isolated seed audit replays the pinned enhancement workflow through N4 from
the archived HMC reference with the recorded ANTs and NumPy seeds. An independent
AFNI automask of that HMC image is the primary coverage comparator; the archived
final mask is a second comparator. Neither comparator is supplied to the replayed
registration. Coverage is the fraction of comparator voxels with probability
weight greater than 0.5. The screening bands are below 0.25 (flagged),
0.25 to below 0.75 (borderline), and at least 0.75 (passing). Calibration in the
exact campaign image reproduced coverage 0.0203 for s29/ses-07 shapeMatching,
0.9684 for the paired spatialTS run, and 0.8274 for s03/ses-09 goNogo. The two
s29 archived-mask Dice values exactly matched the prior diagnostic values;
the failed reference retained central darkening on visual inspection. These
examples establish separation for the known failure, not accuracy on every
partial-field-of-view run. Centroid distance describes the truncated mapped mask
and must not be interpreted as the initializer transform's translation.
Replay is an imperfect proxy for the original execution: the completed campaign
audit included displaced replays with visually uniform archived references and
passing replays with abnormal archived intensity. Therefore also screen the
archived reference's relative median, `(p50 - p2) / (p98 - p2)`, measured inside
the independent HMC automask. Require inspection below 0.68 or when its absolute
difference from the replay exceeds 0.05. In calibration against the whole
campaign, six passing replays had archived relative medians 0.463–0.517; the
remaining passing replays were at least 0.704. Visually confirmed failures in
the coverage-flagged group reached 0.654; retained originals were at least 0.722.
The 0.68 threshold lies in this observed gap. These conservative thresholds
identify review candidates, not automatic repair decisions or general-purpose
fMRIPrep quality thresholds. Invalid intensity measurements block selection.
Inspect every coverage or intensity candidate against the archived reference;
retain usable originals when only their replay fails. The audit is evidence for
repair selection, not scan exclusion or final output approval.

The campaign audit covered all 2,592 acquisitions in the 46-subject roster.
It found 183 replay coverage flags and 15 borderline runs. Visual comparison
retained seven flagged originals and every borderline original, while the
archived-intensity screen identified six additional damaged references with
passing replays. The combined severe screen therefore flags 189 acquisitions
(7.29%), and visual adjudication selects 182 acquisitions across 40 subjects
(7.02%) for staged repair. All 204 coverage/intensity candidates have recorded
visual decisions. Run-level measurements, image checksums and reasons belong
with the audit and repair-plan provenance.

A fresh master seed changes the derived ANTs seed used by registration outside
the BOLD-reference step as well. `--skull-strip-fixed-seed` does not freeze all
anatomical/template processing. Any targeted repair must preserve the existing
subject coordinate system, verify reuse of its anatomical and template outputs,
and record the fresh seeds. Repairs belong in separate staging storage; changing
the shared derivatives requires a separate integration decision. Stop before
repair submission if more than 10% of assessed runs are flagged.

Targeted repairs expose only original anatomical derivatives to `--derivatives`;
functional derivatives and old configuration files are excluded. Validate the
original T1w grid, mask, tissue maps, both directions of both MNI registrations,
fsnative-to-T1w transform, and surface registrations before building the full
workflow. The full workflow must use those inputs and contain a new BOLD
initializer for exactly one acquisition, with every echo. Native sphere GIFTI
conversion may run from the unchanged FreeSurfer reconstruction; no new fsLR or
MSM registration is permitted. This image may recompute T2w preprocessing into
the original T1w coordinates. Its sMRIPrep surface workflow also derives native
`lh.midthickness` and `rh.midthickness` when absent, even with `--fs-no-resume`.
An isolated exact-image reproduction added only those two files, preserving
every original reconstruction file. Permit those additions only in the
node-local copy, checking finite vertices and unchanged white-surface topology.
Record every staged addition, modification and removal with hashes as a warning
in per-run provenance. These warnings do not stop derivative generation; changes
beyond the expected midthickness derivation require a consistency report before
integration. Shared source reconstructions remain read-only throughout.
The original anatomical midthickness GIFTIs remain the validated reuse inputs.
New anatomical outputs must not replace the
originals during integration. Measure the actual rerun N4 weighting map and
reference intensities, rather than relying only on a second random replay,
and inspect each repaired reference before proposing integration. Bound retries
to three distinct master/ANTs seed pairs per acquisition by default. An explicit
follow-up authorization permits up to ten additional seed pairs for exhausted
acquisitions, including near-threshold coverage cases. Preserve the initial plan
and every consumed seed, and retain the same coverage and intensity thresholds.
Additional retries do not automatically approve borderline outputs or establish
that a low-coverage result has the catastrophic initializer failure. When the
additional bound is exhausted, report the images and metrics for a scientific
decision instead of changing the acceptance threshold.

All 182 selected acquisitions obtained an accepted replacement under these
unchanged checks and targeted visual review. Actual rerun coverage ranged from
0.750373 to 0.987755 (median 0.904639). Archived-reference relative intensity
had median 0.526359 before repair and 0.776383 afterward; every accepted
replacement was at least 0.702999. The selected s295/ses-03 spatialTS attempt
passes coverage narrowly at 0.750373. Direct inspection confirms brain-aligned
weights and a uniform reference; retain that narrow margin in its provenance.
Other plausible, below-threshold attempts remain unselected rather than being
reclassified as catastrophic failures. These results establish correction of
the targeted reference defect, not approval of unrelated scan quality.

The BOLD tree is trimmed exactly once before event generation. Sidecars record
`NumberOfVolumesDiscardedByUser: 7`; fMRIPrep must use `--dummy-scans 0`. MRIQC uses
`--fd_thres 0.5`, and the decision generator records `dvars_std` as evidence.

`sub-s1399/ses-12` has BOLD runs without a fieldmap. `sub-s03/ses-13` and
`sub-s297/ses-01` are fieldmap-only sessions. Nine subjects have T2w in a different
session from the retained T1w, so it cannot refine the pial surface. These conditions
remain reviewable evidence in the decisions manifest.
