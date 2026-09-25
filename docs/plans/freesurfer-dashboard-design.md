# FreeSurfer 8 and pipeline provenance

Design for review, 2026-09-24. This describes the target behavior, not deployed functionality.

## Outcome

Maintain one canonical DataLad study. Run standalone FreeSurfer 8.2.0, approve
its surfaces, and supply those surfaces to fMRIPrep. A separate interactive
dashboard must trace every published file to its recorded sources and processing
steps, including behavioral data and analysis-specific exclusions.

## Processing order

1. Flywheel conversion and immediate anatomical defacing; canonical behavior
   and demographics join the same BIDS dataset.
2. Global-signal reports before trimming, remove seven volumes, generate events,
   regenerate reports, link fieldmaps, and validate BIDS, preserving existing order.
3. MRIQC, automatic result merging and evidence extraction, then scan review.
4. Apply approved scan decisions and validate the curated input.
5. Standalone FreeSurfer 8.2.0, automatic result merging and evidence extraction,
   then surface review.
6. fMRIPrep using the approved reconstruction, followed by output review.

MechaBABS/BABS owns Slurm execution, worker clones, dependencies, and merging.
`network_fmri` supplies app configuration, evidence collection, and committed
review gates. The controller advances available work automatically, stops at
unapproved gates or failures, and resumes without repeating completed work.
Final output review is a separate state from successful job completion.

## Standalone reconstruction and reuse

- Pin the FreeSurfer 8.2.0 Linux container by checksum, including any required
  patches; record the build stamp, command, selected inputs, and resources.
- Use the anatomicals retained by scan review. Ambiguous anatomical selection
  blocks reconstruction instead of silently choosing a file. Record T1w/T2w
  selection and reconstruction options before submission.
- Start a fresh reconstruction; never resume a FreeSurfer 7 working directory.
  Preserve the old pilot's logs and results as superseded evidence.
- Preserve subject-level processing across sessions and explicitly align
  FreeSurfer subject directory names with fMRIPrep's session-tracking settings.
- Create a versioned FreeSurfer derivative through an upstream app configuration.
  Use a small BIDS-to-recon-all adapter only if existing app support is insufficient.
- Verify output completeness and record a SHA-256 inventory of the reconstruction
  supplied to review. Approval binds to that inventory, input commit, and container.
  Changed evidence invalidates approval; automatic collection never overwrites it.
- Pass the approved subjects directory to fMRIPrep 25.2.5 with
  `--fs-subjects-dir` and `--fs-no-resume`. If auxiliary writes are required, use
  a job-local copy of the pinned derivative and preserve the reviewed originals.
  Verify that reconstruction is not resumed and reviewed surfaces are unchanged.
- Keep FreeSurfer reconstruction and fMRIPrep's bundled FreeSurfer utilities
  separately identified in provenance. Reuse compatibility requires a pilot.

Surface review follows the [current procedure](../surface-review.md): ITK-SNAP
ribbon inspection, Freeview surface checks, and explicit approval after corrections.

## File provenance contract

Extend the existing records index rather than create a second database of record.
Producers save versioned receipts with their outputs; collectors normalize them.

- **Artifact:** stable identity, dataset ID, relative path, dataset commit,
  content hash or annex key, BIDS identity, and availability. Archive members
  retain their member path and parent archive identity.
- **Source:** Flywheel project/session/acquisition/file identifiers and recorded
  source version or checksum; behavioral files use canonical dataset ID, commit,
  path, and content identity. Credentials and identifying DICOM headers are omitted.
- **Attempt:** stage, scope, input/output commits, software/container identity,
  parameters, Slurm job, timestamps, logs, and outcome. Retain failed and superseded
  attempts alongside successful ones.
- **Link:** input artifact, processing attempt, output artifact, and relationship
  such as conversion, defacing, trimming, event generation, or reconstruction.
  Preserve many-to-many inputs, including fieldmaps and shared anatomicals.
- **Finding/decision:** affected artifact or scan, evidence, scope, reviewer,
  timestamp, and reason. Preprocessing eligibility, surface approval, and
  first-level eligibility remain distinct.

Use existing receipts and upstream provenance first. Add missing links at the
producing stage; do not infer them solely from filenames or claim a subject-level
dependency identifies the exact input of every file. Historical gaps are labeled
unrecorded. New publications must contain complete links at the pipeline-stage
boundary; recording every internal command's temporary file is outside scope.
Checksums and identifiers preserve lineage for temporary, nondefaced inputs
without retaining their image content outside permitted temporary storage.

The SQLite index remains disposable, atomically rebuilt on local disk, and includes
its source commits and refresh time. It must distinguish stale scheduler state,
unavailable content, missing evidence, and genuinely failed processing.

## Dashboard boundary

Build the website as a separate project against this record contract. Its views
are a subject/stage overview, searchable file lineage, attempt logs and reports,
and review queues. NiiVue displays approved-to-serve defaced images and surfaces.
Show behavior timing findings and their analysis consequences beside the matching
scan, including scans retained for preprocessing but excluded from task models.

Initially serve through localhost or an authenticated SSH tunnel. A read-only API
serves the index and allowlisted study artifacts; it cannot browse arbitrary paths.
Existing CLI/TSV review remains authoritative. Dashboard review submission can be
added through that same validation/commit path later, never by editing SQLite.
MongoDB and REDCap are unnecessary for this design. Reconstruction edits and
downstream scientific analyses are outside the first dashboard release.

## Migration and acceptance

Use a fresh campaign identity for the changed app graph. Retain prior campaigns
and raw/MRIQC evidence; reuse completed MRIQC only after checking input commits,
configuration, and review seals. Never relabel old outputs as FreeSurfer 8.

Before expanding beyond sub-s03, demonstrate:

1. Standalone 8.2.0 runs on Sherlock with recorded resource use and complete outputs.
2. Missing, rejected, or stale surface approval blocks fMRIPrep; regenerated
   evidence preserves human decisions only when their bound inputs are unchanged.
3. A reviewed reconstruction is reused by fMRIPrep without recon-all resuming;
   required volumetric and surface derivatives complete successfully.
4. Automatic merge, extraction, and review preparation survive controller restart
   without duplicate submission or overwritten decisions.
5. A functional output, anatomical surface, and events file trace to their recorded
   source inputs. The timing-related first-level exclusion stays separate from
   preprocessing eligibility. Missing historical links are visibly incomplete.
6. Rebuilding the index reproduces those links, preserves attempt history, and
   does not change canonical data or expose nondefaced inputs.

Implementation should land in two parts: standalone FreeSurfer and its handoff
first, then provenance integration and the separate dashboard. The dashboard can
display pending stages while reconstruction runs; approval remains manual.

## References

- [FreeSurfer release notes](https://surfer.nmr.mgh.harvard.edu/fswiki/ReleaseNotes)
- [fMRIPrep 25.2.5 reuse options](https://fmriprep.org/en/25.2.5/usage.html)
- Existing code: `processing.py`, `handoff.py`, `qa/freesurfer.py`, and `records/`
  under `src/network_fmri`; conversion/defacing receipts in `network_fw2bids`.
