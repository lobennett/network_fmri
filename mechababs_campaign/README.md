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

**Provision it once** (prerequisite, not yet done). Options, in order of preference:
```bash
# a) datalad-installer (pip) fetches a standalone build to a prefix:
pip install datalad-installer
datalad-installer --sudo=no git-annex \
  -m datalad/git-annex:release \
  --install-dir /home/groups/russpold/sw/git-annex-standalone
# b) or copy the standalone git-annex 10 already baked in the network_fmri
#    container (git-annex 10.20260624) out to that prefix.
```
Then `git annex version` from that prefix must report `>= 10`. (The preamble hard-fails
if it doesn't.) **This is the #1 question for Austin** — how does mechababs/BABS expect
git-annex to be provisioned on a cluster whose module is too old?

### 2. mechababs ref — rebase, don't use `sherlock-run` as-is
Our fork branch `sherlock-run@52b1844` is **~15 commits behind** upstream `main`.
Upstream since then: automated `iterate` (reads `babs status --json`, no interactive
prompt), `babs merge` auto-pulls results into the campaign, BIDS-study mode. The
config-composition model, `bootstrap.sh`, `merge_config.py`, and these YAML schemas
are unchanged. **The right base is upstream `main` + our local-BIDS-selection patch
(`7d61f9c`) rebased on top** — this gets the automation for free. Local-BIDS support
is still fork-only (upstream `select.py` has no `file://` awareness), so the campaign
must pin OUR mechababs, not `asmacdo/mechababs@main`.

---

## Runbook (execute AFTER the meeting / green-light)

```bash
# 0. PREREQUISITES
#    - git-annex 10 provisioned at the prefix above (see §1).
#    - build the container shim as a SIBLING of the campaign (uses our local sif):
cd /scratch/users/logben/mechababs_campaigns
export PATH=/home/groups/russpold/sw/git-annex-standalone/bin:$PATH
bash /home/users/logben/network_fmri/mechababs_campaign/mriqc-24.0.2-local-shim.sh
#      → creates ./mriqc-24.0.2-shim (a container DataLad dataset)

# 1. BOOTSTRAP the campaign (pin rebased fork ref + vanilla babs main)
./mechababs/bootstrap.sh mriqc-network \
  --babs https://github.com/PennLINC/babs.git@main \
  --mechababs https://github.com/lobennett/mechababs.git@<rebased-local-bids-ref>
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
3. **mechababs pin** — rebase local-BIDS-selection onto current `main` and pin that
   (§2); is it worth upstreaming the local-BIDS + Sherlock configs now?
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
