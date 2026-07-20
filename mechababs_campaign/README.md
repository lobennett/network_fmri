# MRIQC-via-BABS/mechababs campaign (Sherlock)

Operational config + runbook for running **MRIQC** on our BIDS DataLad datasets
through **BABS**, driven by the **mechababs** harness, on Sherlock. These are the
version-controlled *inputs* to a campaign — the campaign itself (its `.venv`,
vendored `code/{babs,mechababs}`, `DATASETS_STATE.tsv`, `derivatives/`) is a
separate DataLad dataset that must live on `$SCRATCH`/`$OAK`, never in this repo.

**Status: DRAFT for the 2026-07-22 Austin walkthrough.** The actual
`bootstrap`/`iterate`/`babs init`/`submit` run is gated on that meeting. Everything
here is config only — nothing is executed by keeping these files.

Upstream reference: `asmacdo/mechababs` (read at `main`); our fork adds one required
feature (local-BIDS selection). BABS: `PennLINC/babs`.

---

## How it fits together (mechababs → BABS → Slurm)

A **campaign** is a self-contained DataLad dataset. Every run is the composition of
three axes:

- **pipeline** (`pipelines/mriqc-24.0.2-sherlock.yaml`) — the container + BIDS-App flags
- **cluster** (`clusters/sherlock.yaml`) — Slurm resources + the per-job shell preamble
- **dataset** — a BIDS DataLad dataset, given by URL/path

`mechababs`'s `merge_config.py` composes pipeline × cluster × dataset into the single
`babs-config.yaml` that `babs init` consumes. BABS then scaffolds an input/output RIA
under a per-cell project, `babs submit` fans one Slurm job per (subject) or
(subject,session), each job `datalad run`s the containerized app and pushes a result
git branch, and `babs merge` consolidates them.

`mechababs iterate` is a **reconciler**: each invocation advances one cell
(dataset × pipeline) by at most one transition — scaffold (`babs init`, no submit) →
submit → poll `babs status --json` → merge. You re-run `iterate` repeatedly until
every cell is merged. State lives entirely in the `DATASETS_STATE.tsv` ledger (which
columns are populated), not a status enum.

### The tree you end up with (per current upstream)
```
mriqc-network/                         # campaign root = DataLad dataset
  campaign.yaml                        # {cluster, pipelines:{mriqc: path}, venv, limit}  (written by `configure`)
  DATASETS_STATE.tsv                   # the ledger (one row per dataset)
  .venv/                               # babs + mechababs editable (gitignored)
  code/{mechababs,babs,mriqc-24.0.2-shim}/   # pinned subdatasets
  derivatives/
    discovery_mriqc_attempt-1/         # one babs project per dataset×pipeline cell
      analysis/{code,inputs/data,containers}/  input_ria/ output_ria/
    validation_mriqc_attempt-1/
```
(Container path BABS expects: `<shim>/.datalad/environments/bids-mriqc/image`.)

---

## Two things that MUST be right or it won't run

### 1. git-annex 10 (not the Sherlock module)
Our BIDS DataLad datasets were created with **git-annex 10**. Sherlock's only
git-annex modules are **6.x and 8.20210622** (`ml spider git-annex`), and **v8
cannot operate on the v10 repo format** — it fails `git-annex filter-process` with
"Invalid argument". So the cluster preamble does **not** `module load system
git-annex`; it puts a standalone git-annex ≥10 first on `PATH`
(`clusters/sherlock.yaml`), at:

```
/home/groups/russpold/sw/git-annex-standalone/bin
```

**DONE — provisioned 2026-07-19.** Copied the network_fmri container's standalone
git-annex 10 out to the prefix:
```bash
apptainer exec -B /home/groups/russpold/sw:/hostsw <network_fmri.sif> \
  cp -a /opt/git-annex.linux /hostsw/git-annex-standalone
```
Verified on the host (no module): `git-annex version → 10.20260624-...` (exact match
to what created our datasets). The preamble puts this dir first on PATH and hard-fails
if it isn't ≥10. Still worth confirming with Austin whether mechababs/BABS expects a
different provisioning route on a module-only cluster.

### 1b. babs install must be wheels-only on Sherlock
babs pulls the full nipreps stack (`niworkflows → h5py, pillow, scikit-image, …`). A
naive `pip/uv pip install` **source-builds** those and fails on Sherlock (no HDF5/build
env). Verified fix: install with wheels forced —
```bash
uv pip install --only-binary=:all: babs            # PyPI 0.5.4 resolves cleanly this way
```
`bootstrap.sh:111` does a plain `uv pip install -e code/babs` (no `--only-binary`), so
on Sherlock it will fail unless run with `--system-site-packages` (a base env that
already has the heavy deps — we don't have one). **Austin question:** how does bootstrap
provision babs's compiled deps on Sherlock — wheels-forcing, a base conda/module env, or
`--system-site-packages`? (Our throwaway proof env: `/scratch/users/logben/babs_depcheck_venv`.)
`babs init` arg surface confirmed to match this config: `--container-ds`,
`--container-name`, `--container-config`, `--processing-level`, `--list-sub-file`, `--queue`.

### 2. mechababs ref = UPSTREAM `main` (the fork is being retired)
Decision (2026-07-19): the `lobennett/mechababs` fork will be deleted; the campaign
pins **`asmacdo/mechababs@main`** — nothing here depends on the fork anymore. Upstream
main already has the automation we want (`iterate` reads `babs status --json`; `babs
merge` auto-pulls results).

**PIVOTAL OPEN ITEM — local-BIDS selection.** Upstream `select.py` has **no
`file://`/local-dataset awareness**; that was a fork-only feature. Our inputs are local
DataLad datasets on OAK, so on pure upstream, `mechababs add-dataset`/`iterate` cannot
scan/select them out of the box. This is now **the** thing to resolve with Austin:
either (a) he upstreams local-BIDS support, or (b) we bypass mechababs's selector and
drive **BABS directly** — `babs init --datasets <name>=<our OAK datalad path>` with a
hand-supplied `--list-sub-file` inclusion, using these YAMLs only for the container +
resource config. Option (b) is a viable fallback that needs no fork. Do NOT plan around
the retired fork.

---

## Runbook (execute AFTER the meeting / green-light)

```bash
# 0. PREREQUISITES  (both DONE 2026-07-19)
#    - git-annex 10 provisioned at /home/groups/russpold/sw/git-annex-standalone (§1).
#    - container shim BUILT at /scratch/users/logben/mechababs_campaigns/mriqc-24.0.2-shim
#      (bids-mriqc → our local 24.0.2 sif, content-local; verified `mriqc --version`).
#      Rebuild if needed: bash /home/users/logben/network_fmri/mechababs_campaign/mriqc-24.0.2-local-shim.sh
export PATH=/home/groups/russpold/sw/git-annex-standalone:$PATH
cd /scratch/users/logben/mechababs_campaigns
# clone UPSTREAM mechababs fresh (fork retired) to get bootstrap.sh:
git clone https://github.com/asmacdo/mechababs.git   # or `git -C mechababs pull`

# 1. BOOTSTRAP the campaign (upstream mechababs + babs main).
#    ⚠️ babs deps must be WHEELS on Sherlock (§1b) — if bootstrap's plain install
#    fails on h5py/pillow, resolve with Austin (wheels-forcing / base env).
./mechababs/bootstrap.sh mriqc-network \
  --babs https://github.com/PennLINC/babs.git@main \
  --mechababs https://github.com/asmacdo/mechababs.git@main
cd mriqc-network && source .venv/bin/activate

# 2. copy these config files into the vendored mechababs (or point --pipelines/
#    --cluster at them) and CONFIGURE (bind pipeline-set × cluster)
cp /home/users/logben/network_fmri/mechababs_campaign/clusters/sherlock.yaml       code/mechababs/clusters/
cp /home/users/logben/network_fmri/mechababs_campaign/pipelines/mriqc-24.0.2-sherlock.yaml code/mechababs/pipelines/
mechababs configure \
  --pipelines mriqc-24.0.2-sherlock.yaml \
  --cluster sherlock.yaml \
  --limit 1                    # first smoke pass; drop --limit for the full sweep

# 3. register our OAK BIDS datalad datasets. --processing-level session is REQUIRED
#    (these are local, not OpenNeuro, so metadata auto-derivation can't run).
mechababs add-dataset file:///oak/stanford/groups/russpold/data/network_grant/bids/discovery  --processing-level session
mechababs add-dataset file:///oak/stanford/groups/russpold/data/network_grant/bids/validation --processing-level session

# 4. DRY-RUN the first reconciler tick — inspect the composed babs-config + planned commands
mechababs iterate --batch 1 --dry-run

# 5. real ticks: scaffold (babs init) → submit → poll → merge. Re-run until merged.
mechababs iterate --batch 1
babs status --project-root derivatives/discovery_mriqc_attempt-1     # monitor
```

Results land in `derivatives/<ds>_mriqc_attempt-1/` (per-scan IQM JSON + HTML +
group TSVs). Direct-MRIQC smoke tests already confirmed the container + our BIDS +
TemplateFlow produce func/anat/dwi IQMs end-to-end.

---

## Agenda / open decisions for Austin
1. **git-annex 10 provisioning** on a cluster whose module tops out at v8 — what does
   mechababs/BABS expect? (datalad-installer prefix? conda? container?) — see §1.
2. **Container source** — pin our local 24.0.2 sif via this shim vs a ReproNim fetch
   (babs#383 open → shim needed either way).
3. **Local-BIDS selection (TOP priority — see §2).** Fork is retired; upstream has no
   `file://`/local-dataset support. Will Austin upstream it, or should we drive `babs
   init` directly against our OAK datalad datasets with a hand-supplied inclusion list?
4. **babs pin** — `PennLINC/babs@main` (has `--json` status + BIDS-study mode, both
   landed days ago) vs a more field-tested commit.
5. **Granularity** — per-(subject,session), `--no-sub` (confirmed in the pipeline yaml).
6. **Partition/QOS** — `russpold,normal` (default here) vs adding `--qos=high_p`.

## Provenance / drift notes
- Config schemas (`campaign.yaml`, cluster, pipeline, ledger) and the `bootstrap`/
  `configure`/`add-dataset` CLI are STABLE across the last 15 upstream commits — safe
  to commit as a draft now.
- The `iterate` decision path and BIDS-study mode are the actively-churning parts —
  re-pull upstream `main` the morning of the run.
- `docs/output_structure.md` upstream is aspirational (dot-dir config, BIDS-named
  ledger) and does NOT match current code — the tree above reflects the CODE.
