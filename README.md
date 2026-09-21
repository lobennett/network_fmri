# network_fmri

`network_fmri` builds and preprocesses one reviewed 46-subject BIDS dataset. It
orchestrates pinned sibling packages and Slurm jobs; it does not contain Flywheel
conversion rules, behavioral remapping rules, GLM code, or a second workflow engine.

```text
Flywheel parts → one BIDS dataset → canonical behavioral sourcedata
→ global signal (pretrim) → trim 7 volumes → events → global signal (posttrim)
→ B0 links → validator → MRIQC → scan decisions → human approval
→ curation → validator → fMRIPrep
```

Each serial milestone is committed with `datalad save` and receives a receipt in
`code/network_fmri/milestones/`. Array workers never write the shared DataLad history.
The receipt records package/container identities, scheduler job IDs, inputs, outputs,
and validation evidence without credentials.

## Configure and inspect

Copy [workflow.example.toml](config/workflow.example.toml) to a reviewed location and
replace every placeholder with an absolute Sherlock path. The roster file must contain
exactly the 46 selected subject IDs. Keep `FLYWHEEL_API_TOKEN` only in the environment.

```bash
uv sync --frozen
uv run --frozen network-fmri pipeline plan /path/to/workflow.toml
uv run --frozen network-fmri pipeline submit /path/to/workflow.toml --dry-run
```

The dry run has no filesystem or scheduler effects. Review the printed commands,
container paths, dataset path, roster, and Slurm resources before the pilot.

## Pilot, approval, and resume

Run a bounded one-subject operational pilot before the full submission. Copy the reviewed
configuration, keep its validated 46-subject roster, and change every runtime path under
`[paths]` to a dedicated pilot location. Then choose one roster member explicitly:

```bash
uv run --frozen network-fmri pipeline submit /path/to/pilot-workflow.toml \
  --pilot-subject s03 --dry-run
uv run --frozen network-fmri pipeline submit /path/to/pilot-workflow.toml \
  --pilot-subject s03
```

The `--pilot-subject` option derives a one-subject run from the validated full roster; it
does not accept a reduced roster or share the full-run BIDS, parts, work, or log paths.
Its identity is written to the submission record, so every resume of that pilot must
repeat the same `--pilot-subject s03` option.
Confirm Flywheel access, container binds, DataLad annex content, validator diagnostics,
and Slurm logs before the full 46-subject submission. The pilot must also use the pinned
PyDeface 2.1.0/FSL image from `[pydeface]`; verify its digest with
`sha256sum /absolute/path/to/pydeface-2.1.0-fsl-6.0.7.18.sif` before submission.
`dcm2niix -ba y` removes identifying metadata but does not remove facial voxels, so the
array worker passes this verified image to `network-fw2bids` for image-level defacing.

All DICOMs, undefaced NIfTIs, and PyDeface intermediates remain below the worker's
`$SLURM_TMPDIR`. Only defaced anatomy may enter the persistent subject parts or the
assembled DataLad dataset; original anatomy never belongs in `sourcedata`. Inspect the
pilot receipts and sidecars before proceeding:

```bash
jq . /path/to/pilot-bids/code/network_fw2bids/defacing/sub-s03.json
find /path/to/pilot-bids/sub-s03 -path '*/anat/*_T?w.json' -print -exec jq '.Defaced' {} \;
```

Open the pilot T1w and T2w images and review the defacing result visually. Automated
checks verify the receipts, checksums, and provenance; visual review is required before
submitting the 46-subject run.

The initial submission ends at `scan-decisions-generated`. Inspect and resolve every row
requiring review in `code/network_fmri/scan_decisions.tsv`, then seal it with:

```bash
uv run --frozen network-fmri decisions validate /path/to/bids
uv run --frozen network-fmri pipeline submit /path/to/workflow.toml --resume
```

Resume verifies the committed approval receipt before curation. If submission partly
fails, its record remains under the configured log directory; correct the cause and use
`--resume` to submit only missing stages.

## DataLad and diagnostics

Configure a durable annex remote and verify it retains content before curation. The
pre-curation commit remains recoverable only while the annexed content is available from
a remote. Do not execute multiple serial stages against the same dataset concurrently.

Validator reports and logs are written under `derivatives/bids-validator/`, including
failed runs. Global-signal outputs live in `derivatives/gs-pretrim/` and
`derivatives/gs-posttrim/`; MRIQC and fMRIPrep use their own derivative datasets.

The scientific source history and known exceptions are in
[SCAN-NOTES.md](docs/SCAN-NOTES.md). Sherlock operations are in
[SHERLOCK.md](docs/SHERLOCK.md).
