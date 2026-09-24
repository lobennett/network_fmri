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

Create the study and its embedded campaign, then advance MRIQC. One
`advance` call performs one reconciler transition, so inspect status and repeat it
until MRIQC is complete.

```bash
uv run --frozen network-fmri study init workflow.toml --pilot-subject s03
uv run --frozen network-fmri processing plan workflow.toml --pilot-subject s03
uv run --frozen network-fmri processing advance workflow.toml --stage mriqc
uv run --frozen network-fmri processing status workflow.toml
```

MechaBABS merges MRIQC directly into the study’s derivatives.
Generate the review there, edit every `review` row, then seal and commit it.

```bash
RAW=/scratch/groups/russpold/network_fmri/bids
STUDY=/scratch/users/logben/network-study
MRIQC=$STUDY/derivatives/MRIQC-24.0.2+network-v1
REVIEW=$STUDY/code/network_fmri/scan_decisions.tsv

uv run --frozen network-fmri decisions generate "$RAW" \
  --mriqc-dir "$MRIQC" --output "$REVIEW"
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

Advance anatomical preprocessing until complete. Generate the surface checklist
from its derivative, inspect every subject, set `approved=yes` with reviewer and
timestamp, and seal it before full fMRIPrep.

```bash
uv run --frozen network-fmri processing advance workflow.toml --stage anatomical
uv run --frozen network-fmri processing status workflow.toml

ANAT=$STUDY/derivatives/fMRIPrep-25.2.5+anat+network-v1
uv run --frozen network-fmri surfaces generate workflow.toml \
  --pilot-subject s03 --anatomical-derivative "$ANAT"
uv run --frozen network-fmri surfaces validate workflow.toml --pilot-subject s03
uv run --frozen network-fmri processing advance workflow.toml --stage fmriprep
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
