# Simplified BIDS Construction and Preprocessing Pipeline

**Status:** approved design, pending implementation plan  
**Date:** 2026-09-18  
**Branch:** `simplified-bids-pipeline`

## Purpose

Replace the current cohort- and campaign-oriented `network_fmri` workflow with a readable
pipeline that builds one 46-subject BIDS dataset, records milestone states with DataLad,
uses MRIQC evidence plus explicit human approval to curate scans, and runs fMRIPrep on the
curated dataset.

The pipeline also incorporates a separately curated, canonical behavioral dataset. Raw
behavioral files are organized once so that each filename directly identifies its BOLD
acquisition. Runtime heuristic matching is not permitted.

## Scope

This design covers:

- assembly of one BIDS dataset from `network_fw2bids` subject exports;
- pre- and post-trimming global-signal reports;
- removal of seven initial functional volumes;
- B0 fieldmap metadata links;
- ingestion of canonical behavioral sourcedata and deterministic event generation;
- BIDS validation before and after curation;
- MRIQC execution and group reports;
- generation and approval of `scan_decisions.tsv`;
- removal of approved acquisition bundles from the current DataLad state;
- subject-level fMRIPrep execution; and
- Sherlock Slurm orchestration without `datalad run`.

Events are generated here because they are part of the complete BIDS dataset. Behavioral
model-quality rules, first-level modeling, general QA, XCP-D, and GLM execution remain out
of scope.

## Design Principles

1. One 46-subject BIDS dataset is the authoritative working dataset. Discovery and
   validation directories are removed from this branch.
2. Each module has one responsibility and exposes a direct Python or command-line
   interface.
3. DataLad records milestone states with `datalad save`; commands are never wrapped in
   `datalad run`.
4. Array jobs never commit concurrently. A dependent consolidation job validates the
   complete array output and performs one DataLad save.
5. Automated checks produce evidence and recommendations. Any flagged scan requires an
   explicit human decision before curation or fMRIPrep.
6. The Flywheel source is not mutated to express MRIQC decisions.
7. A stage either publishes a complete, validated output or fails without claiming its
   milestone. A complete evidence stage may contain explicitly recorded review findings;
   those findings are data, not hidden execution failures.

## Package Responsibilities

### `network_fw2bids`

Downloads and converts Flywheel data for one subject, then assembles isolated subject
parts into one BIDS dataset. `network_fmri` pins a reviewed `network_fw2bids` commit and
orchestrates its CLI; it does not duplicate conversion rules.

### `global_signal_plots`

Reads the current BIDS tree and writes global-signal tables and plots. `network_fmri`
creates each derivative dataset root and its `dataset_description.json`.

### `network_events`

Reads canonical `_beh.csv` files whose names already identify one logical BOLD
acquisition. It converts exact pairs to `_events.tsv` and records timing/truncation
evidence. It does not infer pairings or make exclusion decisions.

Conversion failures must be reported as failures and review evidence. They must not be
represented by silently successful, header-only event files.

### `network_qa`

Compiles imaging and behavioral evidence into review flags and recommendations. It does
not mutate the BIDS tree or launch applications. Its existing motion and behavioral
generators will be simplified to produce the decision evidence defined below.

Missing or unreadable evidence is unknown and reviewable; it is never interpreted as a
zero-valued quality problem.

### `network_fmri`

Owns configuration, ordered stage execution, Slurm submission, validation, curation,
DataLad saves, and milestone receipts. It replaces the existing campaign registry and
cohort orchestration on this branch.

## Dataset Layout

```text
<bids>/
├── dataset_description.json
├── participants.tsv
├── sub-*/
├── sourcedata/
│   ├── behavioral/
│   │   ├── sub-*/ses-*/beh/*_beh.csv
│   │   ├── behavioral_exceptions.tsv
│   │   └── provenance.tsv
│   └── events_qc/sub-*/ses-*/*_desc-truncation.json
├── derivatives/
│   ├── gs-pretrim/
│   ├── gs-posttrim/
│   ├── bids-validator/
│   ├── mriqc/
│   └── fmriprep/
└── code/network_fmri/
    ├── milestones/
    ├── scan_decisions.tsv
    └── scan_decisions.meta.json
```

Each derivative directory is a BIDS derivative dataset with its own
`dataset_description.json`.

## Canonical Behavioral Dataset

Behavioral reconciliation is a one-time data-curation operation performed before the
runtime pipeline is implemented.

The canonical behavioral DataLad dataset must:

- contain the 46-subject sample only;
- preserve the real raw CSV content;
- organize in-scanner data as
  `sub-<id>/ses-<id>/beh/sub-<id>_ses-<id>_task-<task>_run-<run>_beh.csv`;
- resolve repeated-run and ambiguous-session cases through manual review using source
  data and `SCAN-NOTES.md` as evidence;
- keep provenance from each canonical file to its original filename and checksum;
- separate practice, surveys, and other out-of-scanner data from the in-scanner tree; and
- record known task BOLD scans without recoverable behavioral data in
  `behavioral_exceptions.tsv`.

`behavioral_exceptions.tsv` documents absence and is not a mapping table. Known missing
behavior does not remove a BOLD acquisition from the BIDS dataset and does not block
fMRIPrep.

At runtime, the pipeline copies a pinned canonical commit into
`sourcedata/behavioral/` and records the source dataset and commit in a receipt. It then
enforces these invariants:

- every canonical `_beh.csv` identifies exactly one logical BOLD acquisition;
- every non-rest BOLD has exactly one `_beh.csv` or one reviewed exception;
- no BOLD has both a behavioral file and an exception;
- rest scans require neither; and
- extra, duplicate, or undocumented missing behavioral records stop the pipeline.

## Ordered Pipeline

### 1. Assemble BIDS

Run `network_fw2bids` subject jobs in parallel. Each job writes an isolated subject part.
A dependent assembly job verifies all 46 parts and atomically publishes one BIDS root.
Initialize DataLad and save `bids-assembled`.

### 2. Ingest canonical behavior

Copy the pinned canonical behavioral tree into `sourcedata/behavioral/`, verify its source
commit and content availability, enforce the one-to-one invariants, and save
`behavioral-sourcedata-ingested`.

### 3. Global signal before trimming

Run `global_signal_plots` on the assembled BIDS tree. Write `gs_metrics.tsv` and `gs.pdf`
under `derivatives/gs-pretrim/`, then save `gs-pretrim`.

### 4. Trim dummy volumes

Remove the first seven volumes from every BOLD NIfTI in place. Atomically update each
sidecar with `NumberOfVolumesDiscardedByUser: 7`. A malformed sidecar, unreadable NIfTI,
or per-file failure fails the whole stage. Save `dummy-volumes-trimmed`.

### 5. Generate events

Verify canonical behavioral identities again and run `network_events` after trimming so
it derives the onset shift from each BOLD sidecar. With TR 1.49 seconds and seven removed
volumes, the expected shift is 10.43 seconds.

Clip events whose onsets fall beyond the acquired NIfTI duration and record trial-loss
evidence under `sourcedata/events_qc/`. Known behavioral exceptions produce no event
file. A conversion failure produces no `_events.tsv`; it is recorded in
`sourcedata/events_qc/conversion_errors.tsv` and later becomes a review flag. The stage
is complete only when every exact pair has either a valid events file or an explicit
conversion-error record. An inability to finish that audit fails the stage. Save
`bids-events-generated` after the full conversion and audit complete.

### 6. Global signal after trimming

Run the same global-signal operation on the trimmed tree and write
`derivatives/gs-posttrim/`. Save `gs-posttrim`.

### 7. Link B0 fieldmaps

For each session, write a stable `B0FieldIdentifier` on the fieldmap family and the
corresponding `B0FieldSource` on applicable BOLD sidecars. Metadata writes are atomic.
Save `b0-fieldmaps-linked`.

### 8. Validate the complete pre-curation dataset

Run the official BIDS validator and retain human-readable and structured output under
`derivatives/bids-validator/`. The report is saved even when validation fails. Validator
errors cause a nonzero stage result after the report is committed; warnings are recorded
without failing the stage. Save `bids-precuration-validated` on success.

### 9. Run MRIQC

Submit participant-level MRIQC jobs across the 46 subjects, followed by a dependent group
job. Use MRIQC's `--fd_thres 0.5` and disable public IQM submission. The consolidation job
requires complete IQMs and individual reports before producing group tables and reports.
Save `mriqc-complete`.

### 10. Generate scan decisions

Inventory every logical anatomical and functional acquisition and write
`code/network_fmri/scan_decisions.tsv`. Clean scans are retained automatically. Any flag
sets `decision=review`, `approval_required=yes`, and `approved=no`. Save
`scan-decisions-generated` and stop before curation.

### 11. Approve scan decisions

The reviewer changes every flagged row from `review` to `keep` or `drop`, supplies a
controlled reason and explanation, and sets `approved=yes`. The approval validator checks
the schema, acquisition identities, MRIQC input commit, inventory hash, and evidence
paths. Any edit to the governed fields after approval changes the manifest checksum and
invalidates approval. Save `scan-decisions-approved`.

### 12. Curate the current BIDS state

Remove each approved `drop` as a logical acquisition bundle. Removal includes all echoes,
sidecars, generated events, and other derived files belonging to that acquisition. The
canonical raw behavioral sourcedata remains immutable even when its corresponding BOLD is
dropped; the approved decision explains the resulting historical source record. Do not
remove known behavior-missing BOLD scans solely because behavior is absent.

The complete pre-curation dataset remains recoverable from the preceding DataLad commit,
subject to annex content being retained by a configured remote.

### 13. Repair metadata and validate curated BIDS

Rebuild B0 links after removal, reject references to deleted acquisitions, and run the
BIDS validator again. Save reports under `derivatives/bids-validator/` with distinct
pre-curation and curated names. Save `mriqc-curated` only after validation succeeds.

### 14. Run fMRIPrep

Submit one subject-level fMRIPrep job per approved subject, processing all of that
subject's sessions together. The dataset is already trimmed, so use `--dummy-scans 0`.
Retain the currently verified study options unless deliberately revised, including
`--no-submm-recon`, fixed seeds, and the established output spaces.

A dependent consolidation job verifies all subject outputs, fMRIPrep
`dataset_description.json`, subject reports, and expected subject coverage before saving
`fmriprep-complete`.

## `scan_decisions.tsv` Contract

The file contains one row per observed logical acquisition. Multi-echo BOLD images form
one logical acquisition. A missing expected T1w or T2w has no acquisition to identify, so
the file also permits a synthetic `record_type=missing_expected` row for that subject and
modality. Observed data use `record_type=acquisition`.

Required identity and evidence fields include:

- subject, session, datatype, suffix, task, acquisition, direction, and run;
- expected, observed, and missing echoes;
- representative echo;
- trimmed and original TR counts;
- expected TR-count mean and observed fraction;
- MRIQC metric values and report paths;
- behavioral status and event-conversion status;
- triggered flags;
- automated recommendation, recommendation status, and rationale;
- decision, approval requirement, approval state, reason code, and reason detail; and
- reviewer and review timestamp.

`scan_decisions.meta.json` records the source DataLad commit, MRIQC version, MRIQC input
commit, package commits, inventory hash, generation timestamp, and approved manifest
checksum.

Rows without flags use `decision=keep` and `approval_required=no`. Flagged rows require a
human decision. Any remaining `review`, missing reason, missing approval, changed
inventory, or stale checksum blocks curation and fMRIPrep.

Controlled drop reasons initially include:

- `excessive_motion`;
- `anatomical_quality`;
- `incomplete_acquisition`;
- `severe_artifact`;
- `duplicate_lower_quality`;
- `aborted_run`;
- `missing_required_metadata`; and
- `other`, which requires a nonempty explanation.

## Functional Review Rules

MRIQC motion evidence comes from echo 2 for a complete multi-echo acquisition. No other
echo is silently substituted.

- Rest is flagged when `fd_mean >= 0.2` mm.
- Task is flagged when `fd_mean >= 0.2` mm or `fd_perc >= 20%`.
- MRIQC must report that `fd_perc` was calculated with `fd_thres = 0.5` mm.
- `dvars_std` is recorded as supporting evidence and is not thresholded.
- Echoes 1, 2, and 3 are expected. Any missing echo flags the logical acquisition.
- Unequal volume counts across echoes flag the logical acquisition.
- A missing echo 2 prevents calculation of the configured motion recommendation and
  remains a review condition.

The manifest records trimmed `tr_count` and `original_tr_count = tr_count + 7`. Each
logical acquisition contributes one count: echo 2 when present, the single available
image for single-echo data, or the shared count when echo 2 is missing but all observed
echoes agree. An acquisition with inconsistent observed echo counts has no trustworthy
representative count, remains flagged, and is omitted from the expected-length mean.

For each BIDS task label, the pipeline calculates the mean original TR count across all
countable logical acquisitions, including short acquisitions that may themselves be
flagged. A scan is flagged as `short_scan` when:

```text
original_tr_count < 0.50 * expected_tr_count_mean
```

The strict less-than comparison is intentional.

## Anatomical Review Rules

Counts are evaluated per subject across all sessions and separately for T1w and T2w.

- Zero T1w images requires review.
- More than one T1w image requires review.
- Zero T2w images requires review.
- More than one T2w image requires review.
- MRIQC failure for either modality requires review.

A subject passes the count check only with exactly one T1w and exactly one T2w.

For duplicate images, the nonbinding recommendation follows this order:

1. Prefer complete, valid MRIQC output over a failed result.
2. Compare primary metrics: lower CJV and higher CNR are better.
3. If both primary metrics select the same image, recommend it.
4. If they disagree, use a majority of higher SNR, lower EFC, higher valid FBER, lower
   QI2, and WM2MAX closer to the documented 0.6-0.8 range.
5. Ignore the FBER `-1` sentinel rather than ranking it.
6. A tie or insufficient valid evidence produces
   `recommendation_status=indeterminate`.

The recommendation never changes `decision=review` or approves itself. Historical choices
from `SCAN-NOTES.md` may be shown as context but are not ground truth.

## DataLad Provenance

The BIDS root is one DataLad dataset. Each milestone writes a small receipt under
`code/network_fmri/milestones/` containing the stage name, time, input and output paths,
package versions, container identity where applicable, relevant hashes, Slurm job IDs,
and validation summary.

The orchestrator then runs `datalad save -d <bids> -m <message>` once. It never uses
`datalad run`. Array workers write only their isolated outputs and logs.

Milestone messages are:

1. `bids-assembled`
2. `behavioral-sourcedata-ingested`
3. `gs-pretrim`
4. `dummy-volumes-trimmed`
5. `bids-events-generated`
6. `gs-posttrim`
7. `b0-fieldmaps-linked`
8. `bids-precuration-validated`
9. `mriqc-complete`
10. `scan-decisions-generated`
11. `scan-decisions-approved`
12. `mriqc-curated`
13. `fmriprep-complete`

## Slurm Execution

The pipeline submits explicit dependent jobs rather than delegating orchestration to BABS
or `datalad run`.

- Flywheel conversion: one array element per subject, then one assembly job.
- MRIQC: participant jobs, then one group/consolidation job.
- fMRIPrep: one array element per subject, then one verification/consolidation job.
- Serial mutation stages run only after all upstream jobs succeed.
- Logs live in a configured directory outside the raw BIDS namespace or under a clearly
  ignored operational log directory.
- Every submission command supports a print/dry-run mode.

Container paths, partitions, resources, TemplateFlow, FreeSurfer license, scratch paths,
and concurrency limits belong in a Sherlock site configuration rather than source code.
The initial defaults retain the currently verified MRIQC 24.0.2 and fMRIPrep 25.2.5
images; changing a version is an explicit configuration and provenance change.

## Failure and Restart Behavior

- Stages verify their declared inputs before mutation.
- File mutations use temporary files and atomic replacement where practical.
- A partial stage does not write its success receipt or DataLad milestone.
- A validator failure writes and saves its diagnostic report in a diagnostic DataLad
  commit that is distinct from the success milestone, then exits nonzero.
- Array consolidation lists missing and failed subjects and refuses to commit incomplete
  output.
- Curation refuses stale or partially approved decisions.
- Re-running a completed stage verifies its receipt and output rather than silently
  overwriting it.
- Restarts occur at stage boundaries using the last valid DataLad commit.

## Configuration and CLI Shape

One versioned workflow configuration names the BIDS destination, subject roster,
canonical behavioral source and commit, package pins, container/site configuration, and
resource limits. Secrets such as `FLYWHEEL_API_TOKEN` remain in the environment.

The public CLI should remain small:

```text
network-fmri pipeline plan <config>
network-fmri pipeline submit <config>
network-fmri pipeline status <config>
network-fmri decisions validate <bids-dir>
network-fmri curate <bids-dir>
```

`plan` prints the exact ordered stages and commands without filesystem or Slurm mutation.
Internal stage commands may exist for Slurm scripts but are not the primary user
interface.

## Testing and Acceptance

Unit tests cover stage contracts, decision-schema validation, threshold boundaries,
multi-echo grouping, anatomical ranking, behavioral invariants, acquisition-bundle
removal, sidecar repair, and milestone receipts.

Integration tests use synthetic miniature BIDS trees and must demonstrate:

- exact 46-subject roster enforcement at assembly boundaries without requiring real data;
- deterministic canonical behavior-to-BOLD identity checks;
- seven-volume trimming and event onset adjustment;
- pre/post global-signal derivative isolation;
- inclusive FD thresholds and strict short-scan threshold;
- review blocking and checksum invalidation;
- complete removal of an approved multi-echo acquisition bundle;
- B0 repair after removal;
- validator reports retained on failure; and
- no concurrent DataLad saves from array jobs.

Sherlock acceptance uses a dry-run plan followed by a small pilot subset before the full
46-subject submission. The full workflow is complete only when the curated BIDS validator
has no errors, every fMRIPrep subject job succeeds, consolidation verifies all expected
subjects, and the DataLad dataset is clean at `fmriprep-complete`.

## Delivery Sequence

Implementation is divided into independently reviewable changes:

1. Curate and audit the canonical behavioral DataLad dataset.
2. Simplify `network_events` around exact identity conversion and explicit failure
   evidence.
3. Simplify `network_qa` around the approved scan-decision contract and thresholds.
4. Replace the `network_fmri` branch orchestration, pin the reviewed dependency commits,
   and add Sherlock scripts.
5. Run the synthetic integration suite and Sherlock pilot, then submit the full sample.

The later packages depend on reviewed commits from the earlier steps; pins are updated
only after each upstream change is complete.
