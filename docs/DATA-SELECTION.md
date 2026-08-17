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

## How network_fmri implements it — the `select` stage

The three channels are rendered by a terminal DAG stage, **`select`**, that runs
after `datalad` (see the [README](../README.md) walkthrough). Selection *logic*
lives in the standalone [`network_qa`](https://github.com/lobennett/network_qa)
package; network_fmri only *orchestrates* it — it shells the `network-qa` CLI,
exactly like the `events` stage shells `network-events`. Over the DataLad-tracked
cohort tree the stage:

1. **`network-qa compile`** runs the selected exclusion *generators* and writes a
   provenance-stamped lockfile at `code/exclusions_lock.json` (`_meta` records the
   generators, a UTC timestamp, and the `network_qa` code SHA).
2. **`network-qa render bidsignore`** writes `.bidsignore` with *only*
   genuinely-invalid files (`source == "invalid"`) — currently empty after the
   source-level curation fixes.
3. **`network-qa render scans-tsv`** writes a per-session `scans.tsv`
   (`filename` + `why`) for every excluded scan.
4. **`network-qa render bids-filter`** writes one coarse
   `code/bids-filter_<pipeline>.json` per pipeline (canonical anat acquisition +
   the task set) from [`config/selection.json`](../config/selection.json). fMRIPrep
   / XCP-D / MRIQC receive this via `--bids-filter-file`; there is no symlink
   mirror anywhere.

Because the lockfile, `scans.tsv`, and `.bidsignore` are all derived from the one
compiled exclusion set, they stay consistent by construction — one compile, three
renders.

### Coarse vs per-session filters

The coarse filter can't express a *per-scan* quality call: an excluded scan's task
stays in the task list, so the app still sees it. That's tolerable when the app
runs once per cohort, but not under BABS — it fans out **one job per
subject-session**, and a truncated run that crashes fMRIPrep fails that whole job.

So a pipeline can set `"per_session": true` in `config/selection.json`, and `select`
additionally renders `code/bids-filter_<pipeline>_<subid>_<sesid>.json` per
subject-session (`network-qa render bids-filter-per-session`). The name is
reconstructible inside a babs participant job from its `${subid}`/`${sesid}` shell
variables, so each job points `--bids-filter-file` at its own file. Three rules:

- **A task is dropped from a session only when every run of it in that session is
  excluded.** A filter dict has no per-task run selectivity, so dropping a task
  with a surviving sibling run would discard good data. Those stay selected and are
  printed as unexpressed exclusions — still enforced at lev1 from the lockfile. On
  discovery: 5 task-drops across 4 sessions, 2 unexpressible (s10/ses-01 goNogo,
  s29/ses-12 directedForgettingWFlanker — each has a good `run-2`).
- **`exclude_sources` decides what withholds a scan from *preprocessing*.** Default
  `short-run` only: a truncated 4-D file can crash the app, whereas `behavioral-qc`
  means the events logfile is defective and the BOLD is fine — that's a lev1 call.
- **Anat is not session-scoped.** These filters supersede the one babs generates
  itself (babs emits its `--bids-filter-file` before the args from
  `bids_app_args`, so the later one wins), and babs session-scopes `t1w`/`t2w`/
  `flair`/`roi`. Only **7 of 61** discovery sessions contain a SagMPRAGE T1w — anat
  is acquired sparsely — so session-scoping it would starve 54/61 jobs of an anat.

fMRIPrep opts in; **MRIQC does not** — a QC tool should measure every acquired run,
including the truncated ones.

### Two passes (important)

The exclusion generators split by whether they need fMRIPrep / lev1 outputs:

- **Pass 1 — the `select` stage (pre-fMRIPrep, this repo).** Runs only the
  fMRIPrep-**independent** generators: `short_run` (dim4 vs the per-task mode;
  aborted/short scans) and `behavioral` (missing / non-monotonic-onset events).
  This is what the DAG renders today, so `fw2bids pipeline` yields the complete
  pass-1 selection database in one command.
- **Pass 2 — later, in the network_glm / QA phase (post-fMRIPrep).** Re-compiles
  adding `motion` (needs fMRIPrep confounds via `motion_qa`'s `motion_metrics.tsv`)
  and `lev1_outlier` (needs cohort lev1 QC), then re-renders the same three
  channels. **Not** part of the `select` stage.

This keeps the dataset canonical and validatable, keeps quality calls auditable and
reversible, and stops "invalid" and "deselected" from sharing one channel.
