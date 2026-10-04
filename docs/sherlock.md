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
  /oak/stanford/groups/russpold/shared/containers/fmriprep-25.2.5.sif \
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
under `derivatives/fmriprepviz-0.1.0-25.2.5+full+<campaign>/`. Each viewer uses seven
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

If an Oak clone reports SQLite I/O errors, place its disposable annex database
cache on scratch ([git-annex guidance](https://git-annex.branchable.com/git-annex/)):

```bash
git -C /path/to/oak/clone config annex.dbdir /scratch/users/$USER/git-annex-db
```

This local setting leaves file contents and Git history on Oak.

## Full sample and Oak

Full fMRIPrep uses container `bids-fmriprep-rigidinit1`. Build it from the original
25.2.5 SIF with `containers/fmriprep-25.2.5-rigidinit1.def` and
`--build-arg BASE_IMAGE=/path/to/original.sif` on a compute node. The recipe checks
the original niworkflows source, applies the validated initializer correction, and
stores `/opt/network/fmriprep-build.json`. Record both image checksums, the recipe
revision, and the DataLad container commit in the study before submission. Do not
reuse approvals of old fMRIPrep outputs for this corrected build.

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
