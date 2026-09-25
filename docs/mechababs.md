# MechaBABS processing

`network_fmri` prepares raw BIDS and enforces human review. Upstream
[con/mechababs](https://github.com/con/mechababs) owns campaigns, app dependencies,
Slurm submission through BABS, result merging, and compute provenance.

```text
Raw BIDS validation → MRIQC → scan approval → curation + validation
                   → FreeSurfer 8.2.0 → surface approval
                   → full fMRIPrep
```

High motion and analysis exclusions do not automatically exclude a scan from
preprocessing. Scan and [surface approvals](surface-review.md) remain separate,
committed review gates.
When surfaces need edits, the [correction workflow](surface-review.md#submit-corrections)
creates a separate upstream campaign and requires fresh approval before fMRIPrep.

## Layout

```text
network-study/
├── dataset_description.json          # DatasetType: study
├── sourcedata/
│   ├── raw/                          # canonical raw BIDS subdataset
│   └── sourcedata+subjects.tsv
├── derivatives/                      # upstream-managed BABS subdatasets
├── .mechababs/campaigns/network-v1/    # configs, uv.lock, environment, state
└── code/network_fmri/                 # identity, reviews, exclusions, receipts
```

Behavioral subdatasets, participant data, events, and defacing receipts stay with
raw BIDS. The study works on scratch and has a durable DataLad sibling on Oak.
Initialization clones the raw dataset without moving or deleting its source.
Only subject metadata is written: upstream infers subject-level jobs from it,
keeping all sessions together for anatomical processing and surface reuse.

## Configuration and commands

The `[mechababs]` section in [workflow.example.toml](../config/workflow.example.toml)
sets paths, campaign label, Git commits, and app/cluster templates. `study init`
renders the templates and calls upstream `campaign init` and `add-dataset`.
Upstream creates and checks the campaign's locked environment. No fork, bootstrap
script, or separate campaign repository is required.

```bash
network-fmri study init workflow.toml --pilot-subject s03
network-fmri processing plan workflow.toml
network-fmri processing advance workflow.toml --stage mriqc
network-fmri processing status workflow.toml
```

`advance` checks the selected stage's committed evidence, synchronizes the raw
subdataset, and calls `mechababs iterate --app APP --batch 1`. Repeat after checking
status. Anatomical processing requires scan approval and curated-BIDS validation;
full fMRIPrep requires surface approval. App `depends_on` settings also enforce
upstream ordering. The full app consumes the anatomical derivative to reuse surfaces.
Use `processing advance` to preserve these human gates; calling upstream `iterate`
directly does not enforce them.

Derivatives use upstream names, for example
`derivatives/fMRIPrep-25.2.5+anat+network-v1`. MechaBABS installs and merges them in
the study; `network_fmri` does not copy them elsewhere. Failed jobs require review.
See [Sherlock operations](sherlock.md) for the pilot and approval commands.

## Dashboard records

`network-fmri records build workflow.toml --output /local/path/records.sqlite`
builds a disposable SQLite index of entities, current jobs, findings, decisions,
and artifacts. The study and DataLad history remain authoritative. Upstream's jobs
table reports current job records, not a complete retry history; unavailable
attempt timestamps and commit IDs remain empty.

Event timing findings, scan exclusions, and surface decisions remain distinct
records linked by BIDS identity. The collector stores artifact paths and evidence,
not raw behavioral or imaging content. Use one collector on local disk.

## Verification

The configured upstream commits are pinned for reproducibility. Local tests check
command contracts and review gates; a Sherlock pilot must still verify containers,
Slurm, the FreeSurfer license, and all three processing stages before the full sample.
Existing campaigns using the former fork layout need a fresh upstream campaign;
they are not silently converted.
