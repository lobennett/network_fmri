# network_fmri

Flywheel → BIDS curation for the r01network project, as Slurm array jobs.

A launcher over a pinned fork of [fw-heudiconv](https://github.com/lobennett/fw-heudiconv)
(`e7509a4`). This repo owns the heuristic, the session numbering and the job
submission; the fork does the Flywheel work.

- [docs/PIPELINE.md](docs/PIPELINE.md) — how the stages fit together
- [docs/SCAN-NOTES.md](docs/SCAN-NOTES.md) — what is curated, skipped, or corrected
- [docs/RUN-LOG.md](docs/RUN-LOG.md) — what has been run, and how it was verified

## Setup

```bash
export UV_PROJECT_ENVIRONMENT="$SCRATCH/venvs/network_fmri"
uv sync
```

The venv goes on `$SCRATCH`, not `$HOME` (15 GB, NFS). Export that variable in
every shell or `uv` builds a second venv at `./.venv`.

Flywheel credentials come from `~/.config/flywheel/user.json` (`fw login <key>`).

Run everything on a compute node (`sh_dev`) or via `sbatch` — never a login node.

## Steps

```bash
# 1. dry run one subject (read-only, writes nothing)
network_fmri curate --project r01network --subject s10

# 2. curate + export a cohort, one array task per subject
network_fmri submit fw-heudiconv --cohort discovery --live \
  --partition russpold,normal --throttle 3

# 3. per-subject parts -> one cohort dataset (submit it; ~1 TB of rsync)
network_fmri merge --cohort discovery

# 4. official BIDS validator, pulled as a container on first use
network_fmri validate --cohort discovery -- --ignoreWarnings
```

Step 1 is a plain command — a dry run writes nothing, so there is nothing to
record. Steps 2-3 are wrapped in `datalad run`: each array task calls
`import-subject`, which creates `parts/<subject>` as a dataset and records the
curate+export command in it; `merge` then creates the cohort dataset the same way,
so there is no separate versioning step. git-annex is installed on first use.

To redo one subject with its record intact:

```bash
network_fmri import-subject --cohort discovery --subject s10 --live
```

To read what was recorded:

```bash
git -C $SCRATCH/network_fmri/discovery/parts/s10 log --oneline
git -C $SCRATCH/network_fmri/discovery/bids log -1 --format=%B   # cmd, exit, outputs
```

Cohorts are `discovery` (5 subjects), `validation` (41), `excluded` (11). Output
lands under `$SCRATCH/network_fmri/<cohort>/`.

Steps 2 and 3 take hours at full scale. Step 2 submits itself; step 3 does not, so
submit it directly:

```bash
NF=$SCRATCH/venvs/network_fmri/bin/network_fmri
for c in discovery excluded validation; do
  sbatch -J nf-merge-$c -p russpold,normal -c 2 --mem=8G -t 12:00:00 \
    -o $SCRATCH/network_fmri/logs/$c/merge-%j.out \
    -e $SCRATCH/network_fmri/logs/$c/merge-%j.err \
    --wrap "$NF merge --cohort $c"
done
```

`validate` is quick enough to run on an interactive node.

`--live` writes to the **shared** Flywheel project; snapshot it first if you have
changed the heuristic.

Progress and failures:

```bash
squeue --me | grep nf-
grep -rh 'failed after' $SCRATCH/network_fmri/logs/*/*.err
```

Resubmit failures with the same `--cohort` plus explicit subjects, so output paths
stay cohort-scoped:

```bash
network_fmri submit fw-heudiconv --cohort validation --subject s180 s247 --live
```

## Layout

```
src/network_fmri/
├── cli.py            verb dispatch, sbatch rendering, merge
├── curate.py         payload: session map → engine → export, with retries
├── sessions.py       chronological numbering, aliases, session overrides
├── heuristic.py      acquisition label → BIDS filename (fw-heudiconv hooks)
├── acquisitions.py   task rule, allowlist, skip lists
├── cohorts.py        cohort rosters
├── validate.py       BIDS validator, via container
├── dataset.py        DataLad plumbing: git-annex, create, recorded runs
├── container.py      pull-and-run Apptainer images, cached
└── template.sbatch   per-subject Slurm array
```

External tooling is provisioned, not assumed: the validator is pulled as a
container, git-annex by `datalad-installer`. Neither needs host setup or an image
you have to share.
