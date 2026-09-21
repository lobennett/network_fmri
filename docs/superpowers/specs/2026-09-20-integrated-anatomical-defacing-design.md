# Integrated Anatomical Defacing Design

**Status:** approved design, pending implementation plan  
**Date:** 2026-09-20  
**Packages:** `network_fw2bids`, `network_fmri`

## Purpose

Ensure that no identifiable anatomical NIfTI image is written to Oak, persistent
scratch, DataLad, a subject part, or the assembled BIDS dataset. Every T1w and T2w
image is defaced with PyDeface immediately after `dcm2niix` conversion and before
the subject export crosses from node-local temporary storage into persistent storage.

Transient DICOM archives and undefaced NIfTI images may exist only beneath the
current Slurm job's `$SLURM_TMPDIR`. They are removed before the conversion command
returns, whether conversion succeeds or fails. Original anatomy is never retained in
DataLad or copied into `sourcedata`.

## Privacy Invariants

1. Flywheel downloads, extracted DICOMs, and initial NIfTI conversions exist only
   beneath a validated `$SLURM_TMPDIR` workspace.
2. Persistent subject parts contain only defaced T1w and T2w images.
3. A subject part cannot be published unless every anatomical image has a valid
   defacing receipt.
4. Dataset assembly independently checks the receipts and image checksums before it
   creates the persistent BIDS dataset.
5. Failure is closed: missing tools, missing scratch space, malformed output,
   incomplete receipts, checksum mismatches, or cleanup failures prevent publication.
6. Receipts contain no credentials, DICOM metadata, voxel data, or temporary paths.

## Runtime Environment

PyDeface runs from a pinned Apptainer image containing PyDeface and its required FSL
runtime. The workflow configuration records the image path, immutable image checksum,
and PyDeface version. The image is mounted read-only. The job-local workspace is the
only writable bind used by the defacing command.

`network_fw2bids` continues to call `dcm2niix` with BIDS metadata anonymization
enabled. That option removes identifying metadata but does not remove facial voxels;
PyDeface supplies the separate image-level privacy operation.

## Conversion and Publication Flow

For each subject conversion job:

1. Require `$SLURM_TMPDIR` to resolve to a real, writable directory. Reject missing,
   symlinked, root-level, or otherwise unsafe scratch locations.
2. Create a unique sensitive workspace under `$SLURM_TMPDIR` with owner-only
   permissions.
3. Download each Flywheel DICOM archive into that workspace, extract it safely, and
   run `dcm2niix -ba y` there.
4. Place converted files into a BIDS tree that remains inside the sensitive workspace.
5. For every `*_T1w.nii[.gz]` and `*_T2w.nii[.gz]` file, run PyDeface into a separate
   output path. Do not overwrite the only undefaced copy.
6. Validate the defaced output, then atomically replace the staged anatomical image.
7. Write a subject receipt after all anatomical images pass validation.
8. Copy the now-safe BIDS tree and receipt into a staging directory beside the
   persistent subject-part destination.
9. Remove the sensitive workspace and verify that it is gone. Cleanup failure makes
   the command fail before publication.
10. Atomically publish the persistent staging directory with no replacement.

The safe copy in step 8 is necessary because node-local storage and persistent scratch
are different filesystems; the existing atomic no-replace rename works only when the
staging directory and destination share a filesystem.

## Defacing Validation

For each anatomical image, `network_fw2bids` requires:

- a zero exit status from the pinned PyDeface command;
- exactly one expected defaced output;
- a readable NIfTI with the same dimensions, voxel sizes, orientation, and affine as
  the converted input;
- finite voxel values and nonempty image data;
- image content that is not byte-for-byte or voxel-for-voxel identical to the input;
- the original BIDS JSON sidecar to remain present and valid; and
- no PyDeface intermediate files outside the sensitive workspace.

The JSON sidecar receives a `Defaced` boolean set to `true`. It also records the
defacing software name and version using BIDS-compatible provenance fields where
available. The receipt remains the authoritative machine-verifiable evidence.

## Receipt Contract

Each subject part contains:

```text
code/network_fw2bids/defacing/sub-<label>.json
```

The receipt records:

- schema version;
- subject label;
- status `success`;
- PyDeface version;
- container path identity and SHA-256 checksum;
- relative BIDS path for every T1w and T2w image;
- SHA-256 checksum of the undefaced transient input;
- SHA-256 checksum of the published defaced output; and
- dimensions, voxel sizes, and affine digest used during validation.

The receipt is written atomically only after all anatomy passes. A subject with no
T1w or T2w image may receive a successful empty receipt, but the existing QA rules
still flag missing anatomy for review.

## Assembly Contract

`network_fw2bids._assembly` validates every part before copying any subject data. It:

1. inventories every T1w and T2w file in the subject part;
2. requires an exact one-to-one match with the receipt entries;
3. verifies each published-file checksum and `Defaced: true` sidecar value;
4. rejects extra, missing, malformed, symlinked, or checksum-mismatched anatomy; and
5. copies the validated receipts into the assembled dataset under
   `code/network_fw2bids/defacing/`.

`network_fmri` treats this verified assembly as part of the existing
`bids-assembled` milestone. Its milestone receipt records the pinned
`network_fw2bids` revision, PyDeface image checksum and version, subject receipt paths,
and the count of defaced T1w and T2w images. There is no later defacing stage because
identifiable anatomy must never enter the persistent pipeline.

## Configuration and Commands

`network_fmri` adds a required configuration section:

```toml
[pydeface]
image = "/absolute/path/to/pydeface-2.1.0-fsl-6.0.7.18.sif"
version = "2.1.0"
sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
```

The Flywheel array worker passes only these non-sensitive values to
`network-fw2bids`. Credentials remain in `FLYWHEEL_API_TOKEN`. The command includes
the container image, expected checksum, and version; it never includes a persistent
scratch path for sensitive inputs.

## Failure and Recovery

- A failed conversion publishes no subject part.
- A failed defacing operation publishes no subject part.
- An existing subject-part destination is never replaced automatically.
- A stale or partial persistent staging directory is not accepted as a completed part.
- Array success receipts are written only after safe publication.
- Assembly refuses legacy subject parts that predate the defacing receipt contract.
- Logs identify the subject and BIDS-relative anatomical path but never include the
  Flywheel token, DICOM headers, temporary filenames, or voxel content.

On Slurm termination, shell and Python cleanup handlers attempt immediate removal.
Because `SIGKILL` cannot be trapped, the Slurm job wrapper also removes any prior
job-owned sensitive workspace before starting. Sherlock's node-local temporary storage
remains the final containment boundary for uncatchable termination.

## Testing

`network_fw2bids` tests cover:

- rejection when `$SLURM_TMPDIR` is missing, unsafe, symlinked, or unwritable;
- DICOM download and conversion occurring only beneath node-local scratch;
- PyDeface invocation for every T1w and T2w and for no functional or fieldmap image;
- successful output validation and receipt generation;
- tool failure, missing output, malformed NIfTI, geometry change, unchanged voxels,
  missing sidecars, and invalid sidecars;
- cleanup on success and every failure path;
- safe cross-filesystem copying followed by atomic persistent publication; and
- assembly rejection for missing, extra, stale, symlinked, or mismatched receipts.

`network_fmri` tests cover:

- strict PyDeface configuration parsing;
- propagation of the pinned image, version, and checksum to array workers;
- absence of credentials and sensitive scratch paths from commands and receipts;
- assembly failure when any subject part lacks verified defacing evidence;
- defacing provenance in the `bids-assembled` milestone; and
- synthetic pilot and 46-subject flows containing only defaced persistent anatomy.

## Operational Acceptance

Before the full sample runs, a one-subject pilot must demonstrate that:

1. the job uses `$SLURM_TMPDIR` for all downloaded and undefaced material;
2. persistent parts contain only defaced anatomy;
3. the receipt and sidecar inventories agree;
4. the assembled BIDS dataset passes validation and visual defacing review; and
5. no sensitive temporary material remains after the job exits.

The full run may begin only after the pilot's defaced T1w and T2w images have been
visually inspected. Automated validation proves execution and structural integrity; it
does not replace visual confirmation that facial anatomy was adequately removed.
