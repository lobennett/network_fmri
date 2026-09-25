# Surface review: FreeSurfer 8.2.0

Inspect every subject before fMRIPrep. Use ITK-SNAP for the ribbon overlay and
Freeview for the actual surfaces. FSQC summaries can help locate problems but do
not replace visual review. This is our study procedure, not a claimed reproduction
of Kalanit's lab protocol.

Reconstruction can run alongside MRIQC using the selected anatomy. Functional
curation can change later; anatomical checksums must still match before surface
reuse. A changed T1w or T2w requires a new reconstruction and review.
The Slurm app requests 96 GB for the [FreeSurfer 8 memory footprint](https://surfer.nmr.mgh.harvard.edu/fswiki/rel7downloads/rel8notes).

## Inspect

Work from the extracted, versioned FreeSurfer `subjects/sub-ID` directory.
The review gate requires `norm.mgz`, `ribbon.mgz`, both white/pial surfaces,
`brain.mgz`, `aseg.stats`, and the reconstruction completion marker. Presence alone
does not establish quality; approval remains manual.

| File | Purpose |
| --- | --- |
| `mri/norm.mgz` | Normalized T1 background in the reconstruction's own grid |
| `mri/ribbon.mgz` | Volumetric white-matter/cortical-ribbon labels |
| `surf/lh.white`, `surf/rh.white` | White/gray boundaries |
| `surf/lh.pial`, `surf/rh.pial` | Gray/CSF boundaries |
| `mri/brainmask.mgz`, `mri/wm.mgz`, `mri/aseg.mgz` | Diagnose masking or segmentation errors |

Export viewing copies to a separate folder using the pinned FreeSurfer installation.
Replace the two paths below; do not write exports inside the sealed reconstruction:

```bash
FS_SUBJECT=/path/to/subjects/sub-s03
QC_DIR=/path/to/review-exports/sub-s03
mkdir -p "$QC_DIR"
mri_convert "$FS_SUBJECT/mri/norm.mgz" "$QC_DIR/norm.nii.gz"
mri_convert "$FS_SUBJECT/mri/ribbon.mgz" "$QC_DIR/ribbon.nii.gz"
itksnap -g "$QC_DIR/norm.nii.gz" -s "$QC_DIR/ribbon.nii.gz"
```

These conversions change file format only: do not reorient or resample either file.
In ITK-SNAP, use `norm` as the main image and `ribbon` as the segmentation, with a
partly transparent overlay. Labels are **2: left white, 3: left cortex, 41: right
white, 42: right cortex**; 0 is background. Use discrete label colors.

Also inspect the meshes directly:

```bash
freeview -v "$FS_SUBJECT/mri/norm.mgz" \
  -f "$FS_SUBJECT/surf/lh.white":edgecolor=blue \
     "$FS_SUBJECT/surf/rh.white":edgecolor=blue \
     "$FS_SUBJECT/surf/lh.pial":edgecolor=red \
     "$FS_SUBJECT/surf/rh.pial":edgecolor=red
```

Scroll through axial, coronal, and sagittal slices in both hemispheres. Check that
white surfaces follow the white/gray boundary and pial surfaces follow gray/CSF.
Look for missing cortex, white-matter holes, bridges across sulci, and inclusion
of dura, vessels, skull, or cerebellum. Check orbitofrontal, temporal-pole, medial,
and occipital regions carefully. Toggle overlays off to inspect the image itself;
inspect inflated/surface views when a defect is unclear. Compare T2-assisted pial
placement against the registered T2 when one was used.

Record the reviewer, date, affected hemisphere/region, screenshots of problems,
and whether correction is needed. Leave `approved=no` while any issue is unresolved.

## Correct only when needed

`ribbon.mgz` is a surface-derived inspection output. Painting its labels does not
correct the meshes consumed by fMRIPrep. Use a separate writable reconstruction
copy and Freeview's reconstruction-editing tools for corrective edits:

| Finding | Correction input | Typical restart in the same pinned FreeSurfer 8.2.0 container |
| --- | --- | --- |
| White matter omitted/included incorrectly | `mri/wm.mgz` | `recon-all -s sub-ID -autorecon2-wm -autorecon3` |
| Pial boundary includes nonbrain tissue | Copy `brain.finalsurfs.mgz` to `brain.finalsurfs.manedit.mgz`, then edit the copy | `recon-all -s sub-ID -autorecon-pial` |
| Broader skull-strip or intensity error | Diagnose `brainmask.mgz`/normalization first | Choose the restart for that cause; do not apply a blanket rerun |

Set `SUBJECTS_DIR` to the writable copy for a restart. Preserve the original inputs,
container/build identity, reconstruction options, and T2-pial configuration.
Check the rerun logs and inspect the newly generated ribbon and surfaces again.
ITK-SNAP edits are not automatically transferred into FreeSurfer.

## Approve and hand off

Preserve the original reconstruction, edited inputs, notes, exact rerun command,
logs, and regenerated outputs in DataLad. Publish a corrected reconstruction as a
new versioned campaign result and regenerate its review; never overwrite the sealed
`+review` evidence or manually replace a fingerprint to reuse approval.

Only after inspection passes, set `approved=yes`, `reviewer`, `reviewed_at`, and
`notes` in `surface_review.tsv`, then run `network-fmri surfaces validate` with the
workflow config. Approval is tied to the reconstruction contents; changed files
invalidate it. fMRIPrep must receive that approved reconstruction with
`--fs-subjects-dir` and `--fs-no-resume`.

Use notes such as `ITK-SNAP ribbon: pass; Freeview white/pial: pass; edits: none`,
or reference the correction record and repeat inspection when edits were made.

## Submit corrections

The FreeSurfer image must include the current `freesurfer_app.py` adapter
(`--corrections-dir` in its help). Rebuild/register the image from
`containers/freesurfer-8.2.0.def` when upgrading an older adapter; retain the same
FreeSurfer installer checksum. The worker checks the original build and T1/T2 hashes.

On Sherlock, stop the processing controller, save review notes, and create an edit
copy outside the study. This immediately blocks fMRIPrep, including any old approval:

```bash
network-fmri surfaces checkout workflow.toml --pilot-subject s03 \
  --subject s03 --kind wm --reviewer LB --reason "Describe the observed defect" \
  --output /path/to/surface-edits
```

Edit `sub-s03/mri/wm.mgz` in that copy. Use `--kind pial` for
`brain.finalsurfs.manedit.mgz`, or `wm-pial` for both. Keep the image grid unchanged.
If editing on another computer, return the edited inputs to this work directory.

```bash
network-fmri surfaces submit-corrections workflow.toml --pilot-subject s03
network-fmri processing run workflow.toml --pilot-subject s03
```

Submission seals the inputs in DataLad and creates an upstream campaign named
`<campaign>-editN`, using the committed parent settings. The controller submits,
monitors, merges, and extracts its outputs, then archives the previous review and
stops for fresh approval. Subjects without edits are copied without rerunning
`recon-all`. fMRIPrep consumes this campaign's approved surfaces.

Omit `--pilot-subject` for the full sample. Multiple subjects may follow `--subject`
when they need the same correction type. No mesh, ribbon, or unrelated file edits
are accepted. Broader reconstruction problems still require a diagnosed restart.
Cancel an unsubmitted round with `surfaces cancel-corrections`; its work copy is
preserved. Retry interrupted initialization with the same `submit-corrections`
command: it verifies the sealed inputs/configs and asks upstream to restore its
environment. It never falls back to old approval. A correction round cannot start
after fMRIPrep has been initialized.

References: [FreeSurfer 8.2 recon-all](https://github.com/freesurfer/freesurfer/blob/v8.2.0/scripts/recon-all),
[FreeSurfer editing guide](https://surfer.nmr.mgh.harvard.edu/fswiki/FreeviewGuide/FreeviewWorkingWithData/FreeviewEditingaRecon),
[8.2 label definitions](https://github.com/freesurfer/freesurfer/blob/v8.2.0/distribution/FreeSurferColorLUT.txt).
