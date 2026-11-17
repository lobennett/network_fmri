# Sherlock operations

Use a clean checkout, the reviewed TOML, pinned containers, and a valid FreeSurfer
license. Keep `FLYWHEEL_API_TOKEN` out of files and logs.

## One-subject pilot

Prepare raw BIDS first. DICOMs and undefaced NIfTIs may exist only in
`$SLURM_TMPDIR`; inspect the defaced T1w and T2w images before continuing.

```bash
uv sync --frozen
export FLYWHEEL_API_TOKEN="$(< /secure/path/flywheel-token)"
uv run --frozen network-fmri pipeline submit workflow.toml --pilot-subject s03
uv run --frozen network-fmri pipeline status workflow.toml
```

Verify the FreeSurfer license with the pinned fMRIPrep image before creating a
campaign:

```bash
apptainer exec --cleanenv \
  --bind /home/users/logben/license.txt:/license.txt:ro \
  /oak/stanford/groups/russpold/shared/containers/fmriprep-25.2.7-rc1.sif \
  bash -lc 'export FS_LICENSE=/license.txt; mri_convert --version'
```

The following derivative paths assume campaign `network-v1`; use the label in
`workflow.toml`.

MRIQC 24.0.2 has a [known version-string bug](https://github.com/nipreps/mriqc/issues/1354):
it reports `24.1.0.dev0+gd5b13cb5.d20240826`. Preserve that reported version and
record the image checksum and build source when verifying the container.

Create the study and submit the processing controller from the pinned checkout. It
runs MRIQC and standalone FreeSurfer independently, merges outputs, and prepares
both reviews, including FSQC surface evidence. Configure `[fsqc]` with its image,
version `2.1.4`, and SHA256. FSQC runs on the controller allocation (8 GB minimum),
using a private virtual display and read-only reconstruction mount. It finishes other running work before pausing for approval. Restart it after a controller timeout or
interruption. Only one controller can operate on the study at a time.

The FreeSurfer profile binds job-specific temporary storage to both `/tmp` and
`/scratch`; FreeSurfer 8.2's `mri_vsinus_seg` writes to `/scratch` directly.

```bash
uv run --frozen network-fmri study init workflow.toml --pilot-subject s03
uv run --frozen network-fmri processing plan workflow.toml --pilot-subject s03
sbatch scripts/run_processing.sh workflow.toml --pilot-subject s03
uv run --frozen network-fmri processing status workflow.toml
```

For MRIQC alone, use `network-fmri processing run-mriqc workflow.toml
--pilot-subject s03`. Exit code 2 means the generated review contains an FD-threshold
mismatch that cannot be recovered from verified MRIQC timeseries. When available,
matching echo-2 timeseries supply the 0.5 mm percentage after reproducing the
original metrics; the review metadata records this without altering MRIQC outputs.
Use `--fd_thres: 0.5` in the MRIQC app configuration for new campaigns.
This is the framewise cutoff; the mean-FD review threshold remains 0.2 mm.
Neither controller approves scans or surfaces, or retries failed jobs.

After full fMRIPrep merges, `processing run` executes fmriprepviz on the controller's
Slurm allocation. It extracts only T1w BOLD references, checks that the approved
ribbon comes from the reconstruction used by fMRIPrep, and saves GIF/HTML viewers
under `derivatives/fmriprepviz-0.1.0-25.2.7-rc1+full+<campaign>/`. Each viewer uses seven
cuts per orientation and two frames per second. A restart verifies existing output
checksums and skips rendering; it does not rerun fMRIPrep. The final state is
`awaiting-output-review` until each subject has a committed final approval
(`processing review-output`; see [final review](mechababs.md#final-output-review)).
Approved campaigns finish as `complete`; correction requests remain blocked.
Run `uv sync --frozen` before restarting an older controller.
Worker and merge commits may differ only when their complete Git trees match in
the reviewed FreeSurfer dataset. The visualization receipt records both commits
and the shared tree; changed reconstructions remain blocked.

First, it extracts HTML reports, figures and confounds to the fMRIPrep `+review`
derivative. `code/network_fmri/fmriprep-evidence.json` records per-run output
lengths/TRs, missing outputs and archive/input commits. It reads image headers,
not voxel arrays, and obtains missing raw annex content when needed. Exit code 2
and `output-checks-failed` mean these checks failed; reports remain available and
registration rendering waits. A restart verifies existing evidence without rerunning
fMRIPrep. These checks do not validate a first-level design or approve final outputs.
Both legacy subject reports and fMRIPrep's separate anatomical/session reports are
supported. Restarting upgrades older report receipts in DataLad history; it does
not rerun preprocessing.

The following individual commands remain available for diagnosis. MechaBABS merges
MRIQC archives into the study. `prepare-review` extracts reports
and metrics into a separate DataLad derivative and records their source commits.
Generate the scan review there, edit every `review` row, then seal and commit it.

Run curation/BIDS validation in a compute-node allocation with
`--propagate=NONE`. Sherlock's login-node virtual-memory limit can prevent Deno
from reserving its heap, even when physical memory is available.

```bash
RAW=/scratch/groups/russpold/network_fmri/bids
STUDY=/scratch/users/logben/network-study
uv run --frozen network-fmri processing prepare-review workflow.toml --pilot-subject s03
MRIQC=$STUDY/derivatives/MRIQC-24.0.2+network-v1+review
REVIEW=$STUDY/code/network_fmri/scan_decisions.tsv

uv run --frozen network-fmri decisions generate "$RAW" \
  --mriqc-dir "$MRIQC" --output "$REVIEW" --approval-dataset "$STUDY"
uv run --frozen network-fmri decisions validate "$RAW" \
  --manifest "$REVIEW" --approval-dataset "$STUDY"
uv run --frozen network-fmri curate "$RAW" --manifest "$REVIEW" \
  --validator-image /home/groups/russpold/singularity_images/bids-validator-3.0.1.sif
```

To preserve an existing approved review instead of re-entering it, run
`network-fmri reviews migrate-scan` with its old manifest/BIDS root and the new
MRIQC derivative. The command copies human decisions only when regenerated BIDS
keys and evidence match exactly; otherwise it writes a mismatch report and leaves
the new review unsealed. `reviews migrate-surfaces` applies the same rule to an
existing surface checklist.

Follow the [surface review procedure](surface-review.md) for ITK-SNAP ribbon
inspection, Freeview surface checks, and correction handling.

Anatomical reconstruction can start before MRIQC review when the input selection
is unambiguous. Generate the surface checklist
from its derivative, inspect every subject, set `approved=yes` with reviewer and
timestamp, and seal it before full fMRIPrep.

```bash
uv run --frozen network-fmri processing advance workflow.toml --stage anatomical --pilot-subject s03
uv run --frozen network-fmri processing status workflow.toml

ANAT=$STUDY/derivatives/FreeSurfer-8.2.0+network-v1+review
uv run --frozen network-fmri surfaces generate workflow.toml \
  --pilot-subject s03 --anatomical-derivative "$ANAT"
uv run --frozen network-fmri surfaces validate workflow.toml --pilot-subject s03
uv run --frozen network-fmri processing advance workflow.toml --stage fmriprep --pilot-subject s03
```

Repeat `processing status` and `processing advance` until each stage is merged.
Failed cells require explicit intervention; the command does not retry them silently.

## Pilot acceptance

Before starting 46 subjects, require:

- valid DataLad identities and clean raw, study, and campaign worktrees;
- defacing receipts and visually acceptable defaced anatomy;
- committed scan and surface approvals whose hashes match their receipts;
- merged MRIQC, anatomical, and full fMRIPrep cells with no failed BABS jobs;
- readable derivative subdatasets and campaign Git pins matching `workflow.toml`.

Use `squeue --me`, `sacct`, `network-fmri processing status`, and the paths printed
by MechaBABS to investigate jobs. The Oak sibling is the durable copy; scratch is the
working copy.

The disposable annex database cache can live on scratch while file contents and
Git history remain on Oak ([git-annex guidance](https://git-annex.branchable.com/git-annex/)):

```bash
git -C /path/to/oak/clone config annex.dbdir /scratch/users/$USER/git-annex-db
```

## Full sample and Oak

Use the `fMRIPrep-25.2.7-rc1+full.yaml` app and DataLad container
`bids-fmriprep-25-2-7-rc1`. The validated image uses rigid BOLD atlas initialization,
`--project-goodvoxels`, and 91k CIFTI output. Record the image checksum, source
digest, reported package versions and validation evidence in
`code/network_fmri/fmriprep-build.json`. The older `rigidinit1` recipe remains a
historical reference; approvals of its outputs do not approve a new campaign.

Curation binds the approved manifest to a checksum inventory of its resulting BIDS
files. Later gates verify that result; they do not compare dropped scans with the
earlier MRIQC inventory. fMRIPrep workers request four CPUs, 32 GB and up to 48 hours
(the original s03 pilot took 20 hours); temporary files use node-local job storage.
The full app's `-w` path is under `/tmp`, bound to a unique directory in
`$L_SCRATCH_JOB`; `$BABS_TMPDIR` points to shared scratch and must not be used for
fMRIPrep's working files. The Oct. 2026 campaign's repaired workers request an exclusive node with at least 2 TiB of temporary disk and require 1 TiB
free local space and retain crash logs before cleanup. Retry only failed subjects
with BABS `submit --select sub-XX --skip-running-jobs`, then save the updated
derivative commit in the parent study before restarting its controller.

Use a separate `network-v1` configuration for the 46-subject run; retain the
approved pilot as a reference. Submit raw preparation, then start the processing
controller with an `afterok` dependency on `bids-precuration-validated`.

At each review boundary, run this on a compute node:

```bash
network-fmri publish workflow.toml --group oak_russpold --index /oak/stanford/groups/russpold/data/network_grant/network-dashboard-v1/records.sqlite
```

Publication copies committed raw and derivative datasets, retains their DataLad
identities, and refuses dirty or divergent destinations. Scratch remains the
working copy. Manual scan, surface, and final approvals are still required.

`scripts/run_study.sh workflow.toml [dashboard-index]` initializes the study,
advances processing to manual review, and publishes before and after processing.
Submit it with `--dependency=afterok:<final-raw-job>`. Restart after committing
review decisions. On Sherlock, set `UV_PROJECT_ENVIRONMENT` to an absolute path
under `$GROUP_HOME` for campaign initialization; the upstream `.venv` entry point
links there. Collaborators receive read access to the Oak copy.

## BOLD-reference seed audit

Create an audit directory outside the study and its derivatives. Run inventory,
tests and image processing on a compute allocation. Use the reviewed full roster,
the actual raw and archive dataset paths, and the image/checksum from the committed
build receipt. Do not infer a second campaign from a renamed path or alias.

```bash
ml system git
uv sync --frozen
uv run --frozen pytest -q tests/test_bold_reference_qc.py tests/test_fmriprep_seed_rerun.py
mkdir -p "$SCRATCH/network-seed-audit/results"
uv run --frozen python -m network_fmri.bold_reference_qc inventory \
  --raw /path/to/pinned/raw --archives /path/to/pinned/fmriprep-archives \
  --subjects-file /path/to/reviewed/subjects-46.txt \
  --output "$SCRATCH/network-seed-audit/inventory.json"
```

Verify the imported package path and pinned sibling revisions before calibration.
The inventory preserves archive availability, incomplete runs, recorded program
seeds, archive annex keys and Git commits. It rejects ambiguous configurations and
SBRef acquisitions, whose enhancement input differs from the HMC reference.
Calibrate known failed and plausible runs in the exact container first. Use the
`audit --subject SUBJECT --run EXACT_RUN_KEY` options for that limited check, with
fresh output and node-local work directories.

After calibration, export `FMRIPREP_IMAGE`, `FMRIPREP_IMAGE_SHA256`,
`TEMPLATEFLOW_HOME` and `NETWORK_FMRI_CODE` (the absolute validated checkout).
Slurm runs a spooled copy of the job script, so its path cannot locate the source.
Run one import preflight and verify `AUDIT_PREFLIGHT_PASSED` in its completed log
before submitting the full array:

```bash
export NETWORK_FMRI_CODE=/absolute/path/to/validated/network_fmri
sbatch --partition=russpold,normal --array=0 --time=00:15:00 \
  --output="$SCRATCH/network-seed-audit/preflight-%A_%a.log" \
  "$NETWORK_FMRI_CODE/scripts/run_bold_reference_audit.sh" \
  "$SCRATCH/network-seed-audit/inventory.json" "$SCRATCH/network-seed-audit/results" \
  --check-only
```

After that preflight succeeds:

```bash
sbatch --partition=russpold,normal --array=0-45%4 \
  --output="$SCRATCH/network-seed-audit/audit-%A_%a.log" \
  "$NETWORK_FMRI_CODE/scripts/run_bold_reference_audit.sh" \
  "$SCRATCH/network-seed-audit/inventory.json" "$SCRATCH/network-seed-audit/results"
```

Each worker uses the recorded ANTs/NumPy seeds to replay the weighting-map step
and retains metric JSON, weight and comparator images, plus a montage for
coverage and archived-intensity candidates. A replay can differ from the
original execution; use the archived reference when adjudicating damage.
The worker never reuses a work tree or saves the shared raw
dataset. A failed worker requires diagnosis; existing result directories are not
silently overwritten.

On a compute allocation, aggregate results into a new summary directory:

```bash
uv run --frozen python -m network_fmri.bold_reference_qc summarize \
  --manifest "$SCRATCH/network-seed-audit/inventory.json" \
  --results "$SCRATCH/network-seed-audit/results" \
  --output "$SCRATCH/network-seed-audit/summary"
```

Review `summary.json`, every row of `runs.tsv`, and all montages identified by
`review_reasons`, including passing replays with abnormal archived intensity.
Missing audits block repair selection; missing archives and incomplete outputs
remain explicit rows. Stop before reruns if more than 10% of assessed runs are
flagged. Preserve durable evidence on Oak and small reports outside purged storage.
Audit results do not approve replacing shared derivatives.

### Isolated acquisition repairs

Record one `reviews.json` entry for every coverage/intensity candidate, keyed by
the full acquisition identity. Each entry needs `decision` (`rerun` or `keep`),
a nonempty `reason`, and the inspected image's checksum and reviewer in the
evidence record. Do not select a usable original just because its replay fails.
On a compute allocation, create a new immutable plan:

```bash
uv run --frozen python -m network_fmri.fmriprep_seed_rerun plan \
  --summary "$SCRATCH/network-seed-audit/summary/summary.json" \
  --reviews "$SCRATCH/network-seed-audit/reviews.json" \
  --inventory "$SCRATCH/network-seed-audit/inventory.json" \
  --output "$SCRATCH/network-seed-audit/repair-plan.json"
```

The plan assigns three fresh master/ANTs seed pairs to each selected acquisition.
It rejects incomplete audits, missing visual decisions, source mismatches and
selection above 10%. Keep the plan with the durable evidence; never regenerate
it to restart a failed attempt without accounting for its previous seeds.
If an executed attempt stops during validation, resume through the Python
`run` command with the same plan and a fresh staging root, adding
`--previous-attempt /path/to/prior/attempt-N` for every consumed attempt.
The provenance must match the run, plan checksum and planned seed sequence;
three attempts remains the default total limit across restarts. A preflight-only
invocation does not consume a seed.

An explicitly authorized retry extension can allocate up to ten additional seeds
for exhausted acquisitions. Provide an `attempts.json` mapping each requested run
to all three original attempt directories, then freeze a separate plan:

```bash
uv run --frozen python -m network_fmri.fmriprep_seed_rerun extend \
  --plan "$REPAIR_PLAN" --attempts "$EXHAUSTED_ATTEMPTS" \
  --additional-attempts 10 --output "$RETRY_PLAN"
```

The extension preserves the parent plan checksum, original scope and consumed
seed evidence; every added master and ANTs seed differs from all original and
planned seeds. Run each additional attempt with `--plan "$RETRY_PLAN"` and
`--attempt-number 4` through `13`, giving parallel attempts separate staging
roots. Each invocation executes only its assigned seed, retains its archive
before checking, and uses the same QC thresholds. Inspect passing results before
selecting one for packing. If none pass, report the bounded failure with images
and metrics; do not lower the threshold automatically. Integration with the
extended plan accepts existing original-plan receipts only for original seeds.

Set `NETWORK_FMRI_CODE`, `UV_PROJECT_ENVIRONMENT`, `FMRIPREP_IMAGE`,
`FMRIPREP_IMAGE_SHA256`, `FREESURFER_SOURCE`, `FREESURFER_LICENSE`, and
`TEMPLATEFLOW_HOME` to the validated snapshot, frozen environment and original
campaign inputs. Group pipeline code and environments now live under
`$GROUP_HOME/projects/network_pipeline`; moved virtual environments can contain
stale shebangs and editable import paths. Verify the imported package path and
the pinned sibling revisions. Recreate a task-owned environment with
`uv sync --frozen` when relocation invalidates it.

Create a separate durable staging root under Oak, outside every source dataset.
Use the validated acquisition workflow for the selected campaign. Discover
partition limits with `sh_part`, request sufficient node-local disk, and use:

```bash
sbatch --array=INDEX-INDEX --partition=VERIFIED_PARTITIONS \
  --output="$REPAIR_STAGE/pilot-%A_%a.log" \
  "$NETWORK_FMRI_CODE/scripts/run_fmriprep_seed_rerun.sh" \
  "$REPAIR_PLAN" "$REPAIR_STAGE"
```

`INDEX` is zero-based in the plan. `--preflight-only` may be appended for a graph
check in its own fresh staging root. Each actual attempt repeats that check,
verifies source revisions and FreeSurfer content lineage, and uses fresh
node-local output/work directories. The image binds original data read-only.
Only original anatomy is exposed to derivative reuse; no saved functional work
or configuration is loaded. Failed program execution requires diagnosis; only
completed results that fail the measured QC proceed to another planned seed.

Per-attempt staging contains commands and provenance, graph/reuse checks,
application logs, actual N4 coverage/intensity evidence, a montage and an output
archive with checksums. Archive completed or partial derivatives immediately
after the app returns, before later validation can fail; an archive alone is
not a passing result. Retain execution markers and before/after FreeSurfer
inventories even for failed attempts. Record exact staged surface changes and
hashes in per-run provenance as warnings rather than blocking generation.
Expected additions are the pinned workflow's native `lh.midthickness` and
`rh.midthickness`, with geometry proof. Report any other difference as a possible
consistency problem; never write the shared source reconstruction.
Every automatic pass still needs visual review. Failed
attempts remain separately labeled. Staging never overwrites campaign outputs.
Submit the selected plan as an array; Slurm enforces the verified partition and
user limits. Keep an already-running acquisition in the campaign manifest rather
than launching a duplicate. Every task stages its own node-local reconstruction,
including tasks from the same subject.

Before integration, construct a file manifest containing only each approved
acquisition's functional outputs, transforms, confounds and reportlets. Record
their new seeds and the original anatomical/template lineage. Keep original
anatomy, usable acquisitions, and failed attempts out of that replacement
manifest. Propose a new versioned derivative with an explicit acquisition-level
overlay and rebuilt QA/GLM inputs. Rewrite the temporary `reuse` dataset link
to the preserved original anatomical dataset and retain acquisition-specific
fieldmap provenance: independently seeded jobs can produce different files
with the same fieldmap basename, so those files cannot be merged by filename.
The approved campaign target is
`$OAK/data/network_grant/bids/derivatives/fMRIPrep-25.2.7-rc1+full+network-v3`,
with the same per-subject ZIP names and archive roots as network-v2. Keep its
README marked **IN PROGRESS** until all 46 subjects and 2,592 acquisitions are
present and all 182 repairs have passed their checks and visual review.
Preserve audit, plan, review decisions and attempt receipts on Oak.

Build a subject once all of its selected acquisitions have accepted attempts.
The `--attempts` JSON maps each selected run identity to its accepted attempt
directory. Run on a compute node with enough local space for that subject's
repaired functional files (the largest original selected set is about 82 GB):

```bash
uv run --frozen python -m network_fmri.fmriprep_seed_integration \
  --plan "$REPAIR_PLAN" --attempts "$SUBJECT_ATTEMPTS" --subject s29 \
  --output "$NEW_DERIVATIVES" --code "$NETWORK_FMRI_CODE" \
  --image "$FMRIPREP_IMAGE" --image-sha256 "$FMRIPREP_IMAGE_SHA256"
```

The builder copies unchanged compressed ZIP entries verbatim, replaces only
selected functional files/reportlets, and retains original anatomy. It gives
new fieldmaps seed-specific identifiers and rewrites their references, resolves
temporary anatomical reuse links to the retained anatomy, and rebuilds affected
session reports with the pinned image. Per-run provenance and N4 evidence are
included in each corrected archive. Each worker writes an atomic archive and
an integration receipt; only the serial final stage saves the DataLad dataset.
Check `git-annex version --raw` in the serial worker's actual `PATH` before
saving: this pinned DataLad environment requires at least `10.20230126`.
Sherlock's `git-annex/8.20210622` module is too old. Prefer an existing compatible
installation and record its version. A failed save after successful archive
verification does not require rebuilding the archives or reinitializing the
dataset; preserve the verification receipts and resume the explicit save.
Before the final save, require all 46 readable subject archives, exactly 2,592
acquisition identities and all 182 accepted repair decisions. Check ZIP
membership and unchanged member metadata against the source archives, and
match embedded repair receipts, measured results and review-image hashes to
their accepted attempts. Validate surface before/after inventories, their
recorded difference and derivation geometry together. Earlier pilot receipts
predate the `surface_changes_expected` summary field; require their complete
surface evidence rather than treating a missing field as either approval or
failure. Keep dataset-level visual decisions alongside the
archives when an archive was already built before its visual review. Record
whether a checksum was verified during assembly or independently rechecked;
archive membership alone is not a full-payload checksum verification.
Save the new dataset serially with explicit paths (parallel annex hashing via
`datalad save -J 4` is compatible with this single-writer stage). Mark its README
complete only after the archive save succeeds, record a final completion
milestone, and confirm a clean dataset and unchanged source commit. Adoption
by QA/GLM workflows is a separate version selection; preserve their existing
inputs and results until that selection is made.
Share wholly unchanged archives only through locked annex objects with an
independent link inside the new dataset; do not hardlink writable extracted
images. The estimated new storage is about 8 TB of rebuilt archives plus
0.7 TB of staged repairs. Check actual usage before exceeding about 10 TB.
