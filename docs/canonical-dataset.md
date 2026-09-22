# Canonical dataset design

`network_fmri` builds one DataLad-managed BIDS dataset. Imaging can be rebuilt from
Flywheel, while the reconciled behavioral repositories are immutable inputs pinned by
commit.

## Final layout

```text
<bids>/
├── dataset_description.json
├── participants.tsv
├── participants.json
├── sub-*/ses-*/{anat,fmap,func}/
├── sourcedata/
│   ├── behavioral/
│   │   ├── in_scanner/       # pinned DataLad subdataset
│   │   └── out_of_scanner/   # pinned DataLad subdataset
│   └── events_qc/
├── derivatives/
└── code/network_fmri/
```

The root dataset is the single source of truth. Its Git tree records the exact commits
of both behavioral subdatasets. This retains their manifests, exceptions, QA evidence,
and annex history without duplicating their contents or depending on absolute symlinks.

## Pipeline behavior

The workflow configuration pins the path and 40-character commit of each behavioral
repository. The behavioral stage verifies that each source is clean and at the expected
commit, installs it as a DataLad subdataset at the fixed path above, obtains the content
needed by the next stage, and saves the root dataset. An existing matching subdataset is
a safe no-op; any conflicting path or commit fails before publication.

`network_events` reads only `sourcedata/behavioral/in_scanner`, audits it against the
BOLD identities, and writes `_events.tsv` files beside their functional scans. The
out-of-scanner battery remains intact as source data. Later analyses may write derived
scores under a named directory in `derivatives/`.

A clean rebuild starts with a new output directory and repeats conversion, assembly,
behavioral installation, event generation, QA, and preprocessing. Imaging changes can
therefore be made upstream and rebuilt without editing the finalized behavioral sources.
Participant demographics belong in `participants.tsv` and `participants.json`; their
source must likewise be reconciled and pinned before the pipeline publishes them.

## Finalized inputs

| Input | Oak path | Commit |
|---|---|---|
| In-scanner behavior | `/oak/stanford/groups/russpold/data/network_grant/behavioral_data/canonical` | `8edc76d2bc36c175195d384953c5f1834e6a2e51` |
| Out-of-scanner behavior | `/oak/stanford/groups/russpold/data/network_grant/behavioral_data/canonical_out_of_scanner` | `c2e14a7b0d437c3fd8b38a7b701a820d8acaea44` |

