# MechaBABS processing design

`network_fmri` prepares one canonical raw BIDS dataset. MechaBABS and BABS run
containerized processing after that dataset passes pre-curation BIDS validation.
The study and its DataLad history remain the source of truth; a dashboard database
is a rebuildable index.

## Ownership boundary

`network_fmri` continues to author the raw dataset because MechaBABS only adds
derivatives to an existing study. It owns Flywheel conversion, immediate defacing,
behavior and participant ingestion, dummy-volume trimming, events, B0 links, global
signal reports, BIDS validation, review manifests, and approved raw-data curation.

MechaBABS owns MRIQC, anatomical preprocessing with FreeSurfer, and full fMRIPrep.
BABS owns their subject jobs, scheduler state, result branches, merge, and compute
provenance. Its native `datalad run` records are retained. `network_fmri` uses
ordinary DataLad saves for authoring and human-review milestones.

The processing sequence is:

```text
Flywheel -> prepared raw BIDS -> BIDS validation
          -> MechaBABS MRIQC -> scan review -> raw curation -> BIDS validation
          -> MechaBABS fMRIPrep anatomical stage -> surface review
          -> MechaBABS full fMRIPrep
```

High-motion scans may remain eligible for preprocessing. Analysis exclusions are
separate records and do not remove imaging from fMRIPrep.

## Dataset layout

The current raw BIDS repository becomes a subdataset rather than the study root.
The active study lives on Sherlock scratch and has a durable DataLad sibling on Oak.
All paths are configuration values.

```text
network-study/
├── dataset_description.json       # DatasetType: study
├── sourcedata/
│   ├── raw/                       # canonical raw BIDS DataLad subdataset
│   ├── sourcedata+subjects.tsv
│   └── sourcedata+subjects+sessions.tsv
├── derivatives/                   # installed merged derivative subdatasets
├── code/network_fmri/
│   ├── scan_decisions.tsv
│   ├── analysis_exclusions.tsv
│   ├── surface_review.tsv
│   └── records/
```

The MechaBABS campaign is a separate DataLad dataset at `campaign_dir`. It clones
the wrapper beneath `studies/`, runs BABS there, and records scheduler provenance.
After a stage merges, `processing advance` installs that derivative as a subdataset
under the wrapper's `derivatives/`. The wrapper is therefore the canonical analysis
dataset; the campaign remains its reproducible processing record.

The raw dataset retains the canonical in-scanner and out-of-scanner behavioral
subdatasets under `sourcedata/behavioral/`, their pinned commits, participant data,
events, event-QC sidecars, and defacing receipts. Processing derivatives no longer
live inside the raw repository.

Migration is additive. A command creates a new study and installs the raw dataset at
`sourcedata/raw`; it does not move, rewrite, or delete the existing dataset. It fails
if the destination exists with a different DataLad identity or subdataset commit.
The pilot migration uses a fresh study and campaign before the 46-subject study is
created. Existing absolute-path review metadata is regenerated against the raw
subdataset and installed derivatives. Previously approved decision values and reviewer identity
are reapplied only when their BIDS acquisition keys and generated evidence rows match,
then the review is sealed again in the study.

## Campaign and gates

Package-owned MechaBABS templates live in
`src/network_fmri/mechababs/{clusters,apps}/`. `study init` renders their configured
container and license paths into the pinned campaign. The Sherlock cluster file is adapted from the
existing `sherlock-compat` work. App files cover MRIQC 24.0.2, fMRIPrep 25.2.5
anatomical processing, and fMRIPrep 25.2.5 full processing. The campaign pins the
MechaBABS and BABS Git revisions and container dataset.

The anatomical app produces the FreeSurfer surfaces that fMRIPrep will later reuse.
The full app declares the anatomical result as a chained input, which is also the
MechaBABS ordering dependency. `network_fmri` advances one named app at a time:

1. MRIQC can advance after prepared-BIDS validation.
2. The anatomical app can advance only after scan decisions are sealed and the
   curated raw dataset passes validation.
3. Full fMRIPrep can advance only after `surface_review.tsv` is sealed.

The chained input remains a second ordering check; it does not replace either human
gate. Failed cells remain in BABS for inspection and explicit intervention.

## Configuration and commands

`workflow.toml` gains a `[mechababs]` table containing the study path, durable Oak
sibling, campaign label, raw slot, container-dataset source, Git pins, cluster file,
and app files. All configured paths are absolute on Sherlock except files resolved
inside the installed `network_fmri` checkout.

The command interface is:

```text
network-fmri study init CONFIG [--pilot-subject SUBJECT]
network-fmri processing plan CONFIG [--pilot-subject SUBJECT]
network-fmri processing advance CONFIG --stage mriqc|anatomical|fmriprep
network-fmri processing status CONFIG
network-fmri records build CONFIG --output PATH
```

`study init` creates or verifies the wrapper study, metadata tables, raw subdataset,
campaign, and durable sibling. `processing advance` checks the relevant sealed review
before invoking MechaBABS for one app. It never loops indefinitely; repeated calls
reconcile campaign state until a cell is merged or requires intervention.

## Dashboard record contract

Every durable fact is stored in the study. The dashboard reads these sources:

- `network_fmri` milestone receipts and defacing receipts;
- MechaBABS campaign state and refreshed BABS job status;
- BIDS validator reports and MRIQC metrics;
- scan decisions, analysis exclusions, and surface decisions;
- event conversion errors and `*_desc-truncation.json` sidecars;
- DataLad dataset identities and commits.

`records build` normalizes them into one SQLite file for querying. The database is a
cache and is never committed or treated as provenance. It is written by one collector
on local disk, not concurrently on Oak or Sherlock's shared filesystem. A future API
server can rebuild or refresh it from the study.

The normalized tables are:

| Table | Key | Purpose |
|---|---|---|
| `entities` | subject, session, task, run, acquisition | BIDS identities |
| `stage_attempts` | stage, scope, attempt | job state, times, commits, logs |
| `findings` | entity, finding type, evidence path | MRIQC, behavior, validation findings |
| `decisions` | entity, decision scope | reviewer decisions and reasons |
| `artifacts` | stage, path | reports, images, derivatives, receipts |

Behavioral timing is represented as evidence rather than inferred from names. A
nonmonotonic finding records total, kept, and dropped test trials from its truncation
sidecar. Its scan decision and task-first-level exclusion remain distinct rows. Query
results can be exported to TSV or JSON without changing the canonical behavior tree.

No credentials, participant identifiers beyond BIDS IDs, raw behavioral content, or
imaging content enter the index. Paths point to access-controlled study artifacts.

## Failure and validation rules

- Study creation is idempotent only when DataLad identities and commits match.
- A dirty raw dataset, campaign, or nested behavioral dataset blocks advancement.
- Missing app dependencies or an unlocked campaign environment block submission.
- The Sherlock job preamble supplies modern Git, git-annex, the campaign environment,
  local scratch, and the valid FreeSurfer license.
- The pilot must complete MRIQC, both review gates, anatomical processing, and full
  fMRIPrep before creating the 46-subject campaign.
- Tests use temporary DataLad repositories and fake command runners; the Sherlock
  pilot is the integration test for BABS, containers, Slurm, and the license.
