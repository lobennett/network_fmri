# network_fmri

`network_fmri` creates one reviewed 46-subject BIDS dataset on Sherlock. It builds
the raw dataset, records milestones with DataLad, and hands MRIQC and fMRIPrep to
MechaBABS/BABS.

```text
Flywheel -> defaced BIDS -> behavior/events -> validation
  |-> MRIQC -> scan review -> curation ---------|
  |-> FreeSurfer 8.2.0 -> FSQC -> surface review -------|-> fMRIPrep -> fmriprepviz
```

Copy [workflow.example.toml](config/workflow.example.toml), replace its placeholder
paths, and keep `FLYWHEEL_API_TOKEN` in the environment. Start with one subject:

```bash
uv sync --frozen
uv run --frozen network-fmri pipeline submit workflow.toml --pilot-subject s03
uv run --frozen network-fmri study init workflow.toml --pilot-subject s03
sbatch scripts/run_processing.sh workflow.toml --pilot-subject s03
```

The controller runs MRIQC and FreeSurfer independently, merges their outputs, and
prepares both reviews. FSQC 2.1.4 supplies surface metrics and boundary overlays;
its checksum-pinned `[fsqc]` container runs before manual surface approval. FreeSurfer requires an unambiguous anatomical selection;
fMRIPrep requires approved scans and [surfaces](docs/surface-review.md). Restart the
controller after approval or interruption. Failed jobs or changed anatomy require
intervention. Do not start the full sample until the pilot passes.

After fMRIPrep merges, the controller runs pinned fmriprepviz 0.1.0 on each
subject's T1w-space BOLD references and approved FreeSurfer ribbon. It saves a
registration GIF, interactive HTML viewer, logs and provenance in a DataLad
derivative, then pauses for final output review.

Before rendering, it extracts the fMRIPrep reports and checks BOLD/CIFTI lengths,
confound rows and TRs against the trimmed inputs. Mismatches stop the handoff;
passing checks do not replace visual review.

The current campaign uses fMRIPrep 25.2.7-rc1 with rigid BOLD atlas initialization
and `--project-goodvoxels`. The DataLad container commit and
`code/network_fmri/fmriprep-build.json` record the exact image and package versions.

For suspected seed-dependent BOLD-reference failures, the separate
`network_fmri.bold_reference_qc` module inventories expected runs and replays the
N4 weighting-map registration from archived HMC references. It writes per-run
coverage and intensity evidence, review images, and an exhaustive `runs.tsv`.
Follow the [isolated audit procedure](docs/sherlock.md#bold-reference-seed-audit)
before selecting repairs; the audit does not alter campaign derivatives.
`python -m network_fmri.fmriprep_seed_rerun plan` freezes visually confirmed
acquisitions and fresh seeds. The isolated Slurm repair worker reuses original
anatomical coordinates, measures each actual rerun and writes separate staged
archives and provenance. See the [repair procedure](docs/sherlock.md#isolated-acquisition-repairs)
for validation and integration requirements, including bounded, explicitly
authorized retry extensions for exhausted runs. After repairs pass,
`python -m network_fmri.fmriprep_seed_integration` builds complete subject ZIPs
in the separate `fMRIPrep-25.2.7-rc1+full+network-v3` folder. It preserves original
anatomy and unchanged acquisitions, rewrites repaired fieldmap provenance, and
includes per-run evidence. Use the new folder only when its README says COMPLETE.

See [Sherlock operations](docs/sherlock.md) for the command sequence and
[MechaBABS design](docs/mechababs.md) for ownership and review gates. The
[dashboard records](docs/dashboard-records.md) reference describes the disposable
SQLite index used by the dashboard. Fieldmaps import CNI spiral-recon outputs from Flywheel;
missing fieldmaps are recorded without inventing B0 links. Surface previews retain
the exact anatomical input hashes recorded by the standalone FreeSurfer adapter.

Publication includes the two pinned behavioral subdatasets and MRIQC report SVGs,
so the dashboard can inspect raw behavior and fetch reports without whole archives.
