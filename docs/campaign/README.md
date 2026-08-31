# Preprocessing campaign

This directory is the durable snapshot of the study-specific mechababs/BABS configuration
for MRIQC, fMRIPrep, and XCP-D. The live campaign is a DataLad dataset on purgeable scratch:

```text
$SCRATCH/mechababs_campaigns/r01network
```

Campaign commands use `$NETWORK_FMRI_CAMPAIGN` when set, otherwise the path above;
`--campaign` overrides either value for one invocation.

Edit live configuration under `<campaign>/code/mechababs/`, then refresh this snapshot in
the same commit. Do not treat these copies as the running campaign.

## Snapshot contents

| File | Purpose |
|---|---|
| `MRIQC-24.0.2.yaml` | MRIQC arguments, including `--fd_thres 0.5` |
| `fMRIPrep-25.2.5.yaml` | Subject-level fMRIPrep arguments and output spaces |
| `XCP-D-26.0.2.yaml` | Subject-level XCP-D and its pre-app input cleanup |
| `sherlock.yaml` | Cluster resources and `array_throttle: 12` |
| `mechababs-local-patches.diff` | Processing level, resource override, and primary-input support |
| `babs-local-patches.diff` | `pre_app_commands` support |

The scientific reasons for `--dummy-scans 0`, `--no-submm-recon`, output spaces, and
MRIQC's framewise-displacement threshold are in
[../SCAN-NOTES.md](../SCAN-NOTES.md#preprocessing-decisions).

## Operate safely

```bash
uv run --frozen network_fmri campaign -- iterate --dry-run   # always inspect first
uv run --frozen network_fmri campaign -- iterate --batch 1   # advance one cell
uv run --frozen network_fmri campaign -- status              # submit a status query
```

Important constraints:

- `mechababs configure` rewrites the ledger. Never run it while cells are in flight.
- One `iterate` tick can advance a cell in every cohort; keep batches small.
- A failed `iterate` action reports the failure but does not resubmit it. From the
  campaign's pinned environment, use `babs submit <project> --count 1` as a canary.
- Pipeline arguments are baked into the BABS job script at initialization. Changing them
  requires retiring and scaffolding a new derivative attempt.
- Retired attempts under `derivative-attempts/` are records, not resumable projects:
  BABS stores absolute RIA paths.
- Keep the campaign clean. Untracked logs or unsaved subdataset pointers block iteration.
- Scaffolding can take 15 minutes to two hours on Lustre; slow does not imply hung.

## Why XCP-D needs local adaptations

### Select fMRIPrep as the primary input

BABS passes `input_datasets[0]` as the application's positional input. Raw BIDS must lead
for MRIQC and fMRIPrep, but XCP-D must receive the unpacked fMRIPrep derivative.
`mechababs.primary_input` selects that entry; only the XCP-D config overrides the
`BIDS` default.

After scaffolding, verify that the positional input in
`<project>/code/bids-xcpd_zip.sh` is:

```text
sourcedata/fMRIPrep-25.2.5/fMRIPrep-25.2.5
```

### Remove T2w-only anatomical directories from XCP-D's copy

Nine analyzed subjects have T1w and T2w images in different sessions. fMRIPrep can write a
T2w-only `anat/` directory, but XCP-D interprets it as a second anatomical session and
rejects the subject before processing.

The XCP-D config uses `pre_app_commands` to remove anatomical directories containing no
T1w from its temporary unzipped input. XCP-D consumes the retained fMRIPrep surfaces; it
does not need the isolated T2w derivative that triggers its session-layout error.

Alternatives already ruled out:

- `--bids-filter-file`: XCP-D's filter-to-session-list path is inactive;
- `.bidsignore`: it does not hide the files from the layout query;
- `--session-id`: it would also remove functional runs from that session.

## Recreate the campaign

On a compute node:

1. run the campaign's mechababs `bootstrap.sh`;
2. configure the campaign from the three pipeline YAMLs and `sherlock.yaml`;
3. add each cohort dataset from `$SCRATCH/network_fmri/<cohort>/bids`;
4. apply both vendored patch files;
5. build and vendor one container shim per pipeline with `network_fmri shim`;
6. inspect generated job scripts and run `iterate --dry-run`;
7. save the campaign dataset and its subdataset pointers.

A BABS container source is a shim DataLad dataset, not a direct `.sif` path. The
checked-in pipeline configs refer to shim datasets relative to the campaign root; create
them as siblings of the campaign or adjust those site-local paths. Missing or unregistered
shims fail initialization; the `network_fmri shim` command validates the registration and
vendors the subdataset. The FreeSurfer configs use `${HOME}/license.txt`; BABS may warn
while scaffolding because it checks the unexpanded string, but the generated shell bind
expands `HOME` when the job runs.
