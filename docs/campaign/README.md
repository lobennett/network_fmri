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
| `sherlock.yaml` | Cluster resources and `array_throttle: 8` |
| `mechababs-local-patches.diff` | Processing level, resource override, primary input, datatype selection, and study metadata helper |
| `babs-local-patches.diff` | `pre_app_commands` support |

The scientific reasons for `--dummy-scans 0`, `--no-submm-recon`, output spaces, and
MRIQC's framewise-displacement threshold are in
[../SCAN-NOTES.md](../SCAN-NOTES.md#preprocessing-decisions).

MRIQC's `require_any_datatypes: [anat, func]` accepts anatomical-only and
functional-only sessions, excluding fieldmap-only sessions. It requires the patched
selector; the base selector silently ignores that key. The included `study_meta.py`
regenerates both subject and session TSVs after BIDS curation. It is specific to this
study's sessioned, multi-echo layout: BOLD counts use `echo-1` files to count each run
once. It is not a general BIDS inventory tool for sessionless or single-echo studies.

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

The snapshot includes `--abcc-qc: "n"`, matching the working campaign. It avoids
ABCC executive-summary failures in this container; `--linc-qc` and
`--warp-surfaces-native2std` remain enabled. Restoring this setting preserves the
existing processing choice.

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

Use these exact source bases. Each patch is a cumulative diff applied **once to its
own base**, not on top of a previous snapshot or a current upstream branch.

| Source | Base before patch | Patch | Recorded live source used to produce the patch |
|---|---|---|---|
| `https://github.com/lobennett/mechababs.git` | `ccb3f5b76bf17df09403ecef922b5c0acb8c78f3` | `mechababs-local-patches.diff` | `077759ab607d2c7bfe373f1f3d7d47613e2b15f8` |
| `https://github.com/lobennett/babs.git` | `618dcd744f287e0d241e5a5a2a476606d537c762` | `babs-local-patches.diff` | `17e7d57cbb0115b0ee6b3687e37d50d82d209943` |

The patches reproduce the active code changes. Pipeline and cluster YAMLs in this
directory replace the corresponding base configuration files. Historical pipeline
variants and test-cluster/E2E branches are not part of this reconstruction. The live
source commits are provenance references; reconstruction needs only the published
bases and these patches.

On a compute node, prepare fresh source clones and a **new** campaign path. The
base `bootstrap.sh` accepts branch/tag names through `git clone --branch`, not raw
commit IDs. Create local branches at the pinned commits so bootstrap cannot resolve
a moving remote branch instead:

```bash
# Set snapshot to this directory's absolute path, sources to a new scratch
# directory, and campaign to a new campaign path. Keep the source clones available.
set -euo pipefail
mkdir -p "$sources"
git clone https://github.com/lobennett/mechababs.git "$sources/mechababs"
git -C "$sources/mechababs" checkout -b network-snapshot ccb3f5b76bf17df09403ecef922b5c0acb8c78f3
git clone https://github.com/lobennett/babs.git "$sources/babs"
git -C "$sources/babs" checkout -b network-snapshot 618dcd744f287e0d241e5a5a2a476606d537c762
"$sources/mechababs/bootstrap.sh" "$campaign" \
  --mechababs "$sources/mechababs@network-snapshot" \
  --babs "$sources/babs@network-snapshot"

# Apply before configure, metadata selection, or BABS scaffolding.
git -C "$campaign/code/mechababs" apply --check "$snapshot/mechababs-local-patches.diff"
git -C "$campaign/code/babs" apply --check "$snapshot/babs-local-patches.diff"
git -C "$campaign/code/mechababs" apply "$snapshot/mechababs-local-patches.diff"
git -C "$campaign/code/babs" apply "$snapshot/babs-local-patches.diff"
cp "$snapshot/MRIQC-24.0.2.yaml" "$snapshot/fMRIPrep-25.2.5.yaml" \
  "$snapshot/XCP-D-26.0.2.yaml" "$campaign/code/mechababs/pipelines/"
cp "$snapshot/sherlock.yaml" "$campaign/code/mechababs/clusters/sherlock.yaml"
```

Then, in order:

1. Verify the vendored `HEAD`s equal the bases above. Review and commit the patched
   source and copied configuration in each vendored repository, then DataLad-save
   both subdataset pointers in the new campaign. `configure` requires clean pins;
   the bootstrap environment installs both packages editable, so it uses these edits.
2. Build one registered container shim per pipeline with `network_fmri shim` and
   verify the YAML source paths. Configure from the three pipeline filenames and
   `sherlock.yaml`; configure vendors the shims.
3. Regenerate and save each study wrapper's `sourcedata+subjects.tsv` and
   `sourcedata+subjects+sessions.tsv` by running the patched `study_meta.py` as a
   script (`python3 code/mechababs/study_meta.py --bids-dir <tree> --out <dir>`)
   against its curated BIDS tree; its help text labels itself `network_fmri
   study-meta`, but there is no such subcommand. The helper's `--out` directory must
   already exist. Register
   the cohort study wrappers with `add-dataset`; metadata must be current before
   generating inclusion lists or scaffolding.
4. Inspect composed BABS configuration, inclusion lists and generated job scripts.
   MRIQC must exclude fieldmap-only visits, XCP-D must lead with fMRIPrep input
   and pass `--abcc-qc n`, and generated arrays must carry throttle 8. Run
   `iterate --dry-run` before any separately authorized campaign advance.
5. Save the campaign dataset and changed study/subdataset pointers.

`uv run --frozen pytest -q tests/test_campaign_snapshot.py` applies the whole patch
to offline source fixtures and checks selection, metadata regeneration, composed
BABS configuration, and the `babs init` arguments `iterate` builds — including each
pipeline's processing level and `--throttle 8`. It does not launch BIDS apps, BABS,
Slurm, or a campaign.

A BABS container source is a shim DataLad dataset, not a direct `.sif` path. The
checked-in pipeline configs refer to shim datasets relative to the campaign root; create
them as siblings of the campaign or adjust those site-local paths. Missing or unregistered
shims fail initialization; the `network_fmri shim` command validates the registration and
vendors the subdataset. The FreeSurfer configs use `${HOME}/license.txt`; BABS may warn
while scaffolding because it checks the unexpanded string, but the generated shell bind
expands `HOME` when the job runs.

Relative shim sources and `${HOME}/license.txt` are portable substitutions for
operator-specific absolute paths. They may be adapted without changing the recorded
selection, resource, QC or scientific settings. Verify shared software/cache paths
in `sherlock.yaml` against the target site rather than copying a user's home directory.
