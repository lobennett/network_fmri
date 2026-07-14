# Data selection & exclusions — three channels, not one

network_fmri produces **one clean, valid BIDS dataset**. *Which* files a given
pipeline processes is a separate, declarative decision. The mistake to avoid
(inherited from the legacy pipeline) is overloading `.bidsignore` to mean both
"invalid" and "deselected" — those are different statements and belong on
different channels:

| Channel | The statement it makes | Consumed by |
|---|---|---|
| **`.bidsignore`** | "this file is **not valid** BIDS" | the BIDS validator |
| **`bids-filter-file`** (JSON) | "this **valid** file is / isn't processed by pipeline X" | fMRIPrep, XCP-D, lev1 (via pybids) |
| **`scans.tsv`** | the human-readable **why** | people / provenance |

Why this matters: with everything on `.bidsignore` you lose validator coverage on
real files you've hidden, and a downstream reader can't tell a malformed file from
a deliberate quality call. "Does your dataset validate, and which exclusions are
quality calls vs invalid files?" has no clean answer. Splitting the channels makes
both answerable.

## The current exclusion set, partitioned

| Category | Example | Valid BIDS? | Channel |
|---|---|---|---|
| Aborted single-volume bold | s43/ses-08 dForget run-1 | **No** | removed at source (deleted from Flywheel) — never curated |
| Protocol mislabel | s29/ses-01 cuedTS→spatialTS | now **yes** | fixed at source (relabel) |
| Superseded / legacy anat | acq-MPRAGEPromo T1w, stale s03 SagMPRAGE, ringing/FOV | Yes | `bids-filter-file` (select the canonical T1w/T2w acq) + `scans.tsv` |
| BOLD without behavioral | nBack / goNogo with no events | Yes | `bids-filter-file` (skip for lev1) + `scans.tsv` |
| Prematurely-ended but 4-D | 8–49 % of TRs | Yes | `bids-filter-file` (skip or salvage per analysis policy) + `scans.tsv` |
| Non-monotonic onsets | events logfile defect | Yes (bold is fine) | events step; `scans.tsv` |
| QC-failed acquisition | motion / artifact | Yes | `bids-filter-file` + `scans.tsv` |

After the two source-level fixes (see [SCAN-NOTES.md](SCAN-NOTES.md)),
**`.bidsignore` is effectively empty** — every remaining exclusion is a *valid*
file deselected for a specific pipeline, which is exactly what `bids-filter-file`
is for.

## Replacing the symlink-farm

**Old approach:** build a pruned mirror of the BIDS tree with symlinks minus the
`.bidsignore` matches, then point fMRIPrep/XCP-D at the mirror. Costs: a physical
mirror to maintain (and keep from drifting), validity conflated with selection,
and lost validator coverage on the hidden files.

**Canonical approach:** point fMRIPrep/XCP-D at the **one** clean dataset with
`--bids-filter-file <pipeline>.json` (both tools support it natively). pybids
selects at query time — no mirror, no drift. For exact-input provenance, record the
resolved file list the tool logs from its BIDS query, rather than hand-building a tree.

Example filter (fMRIPrep — take the canonical T1w, process only the task bolds):

```json
{
  "t1w":  {"acquisition": "SagMPRAGE", "suffix": "T1w"},
  "bold": {"task": ["goNogo", "nBack", "cuedTS", "spatialTS", "..."]}
}
```

## How network_fmri implements it (sketch — not yet built)

1. **Curation (`fw2bids`)** emits the clean BIDS plus a per-session `scans.tsv`
   whose `why`/status column is sourced from the selection config below.
2. A single **`selection` config** — rows of `(subject, session, task, run) →
   include/exclude + reason` — is the one source of truth. The orchestrator renders
   from it: (a) per-pipeline `bids-filter-file` JSONs, and (b) the `scans.tsv`
   `why` column. One edit, both artifacts stay consistent.
3. **`.bidsignore`** is rendered only for genuinely non-conformant files (currently
   none, after the source fixes).
4. The **fMRIPrep / XCP-D steps** receive `--bids-filter-file`; there is no symlink
   mirror anywhere in the pipeline.

This keeps the dataset canonical and validatable, keeps quality calls auditable and
reversible, and stops "invalid" and "deselected" from sharing one channel.
