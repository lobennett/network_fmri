# Simplified BIDS Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved pipeline through three independently testable package changes in dependency order.

**Architecture:** `network_events` first establishes deterministic canonical behavior conversion. `network_qa` then produces the approved review manifest. `network_fmri` finally pins both reviewed commits and orchestrates the DataLad/Slurm workflow.

**Tech Stack:** Python, uv, pytest, DataLad, Slurm, Apptainer, MRIQC, fMRIPrep

**Spec:** `docs/superpowers/specs/2026-09-18-simplified-bids-pipeline-design.md`

## Global Constraints

- Execute plans in order because downstream packages pin reviewed upstream commits.
- Complete each package's full test/build verification before updating a downstream pin.
- Perform canonical behavioral cleanup once; runtime code only audits exact identities.
- Run the synthetic suite and a Sherlock pilot before the full 46-subject workload.

---

### Task 1: Simplify `network_events`

**Files:**
- Execute: `docs/superpowers/plans/2026-09-18-network-events-simplification.md`

**Interfaces:**
- Produces: reviewed `network_events` commit exposing `audit` and `create`

- [ ] **Step 1: Execute every task in the network-events plan**

Use its repository checkout and record the final full commit hash.

- [ ] **Step 2: Verify its release artifact**

Run: `uv run --frozen pytest -q && uv build`

Expected: PASS and successful wheel/sdist build.

### Task 2: Curate and Audit Canonical Behavioral Data

**Files:**
- DataLad dataset: `/oak/stanford/groups/russpold/data/network_grant/behavioral_data/canonical`
- Verify against: assembled 46-subject BIDS tree identified by `$NETWORK_FMRI_BIDS_DIR`

**Interfaces:**
- Consumes: `network-events audit`
- Produces: reviewed canonical DataLad commit with exact `_beh.csv` identities, `behavioral_exceptions.tsv`, and `provenance.tsv`

- [ ] **Step 1: Make canonical annexed content available**

Run: `datalad -C /oak/stanford/groups/russpold/data/network_grant/behavioral_data/canonical get .`

Expected: all canonical CSV content is present.

- [ ] **Step 2: Run the exact-identity audit**

```bash
network-events audit \
  --bids-dir "$NETWORK_FMRI_BIDS_DIR" \
  --behavioral-dir /oak/stanford/groups/russpold/data/network_grant/behavioral_data/canonical
```

Expected: a concrete list of unmatched, duplicate, or undocumented identities; no
heuristic proposals.

- [ ] **Step 3: Resolve every reported identity once**

Rename/reorganize real CSVs to canonical BIDS-addressed names. Record original paths and
SHA-256 values in `provenance.tsv`; add reviewed missing-data rows to
`behavioral_exceptions.tsv`. Use `docs/SCAN-NOTES.md` as evidence rather than automatic
truth. Do not create placeholder behavioral CSVs.

- [ ] **Step 4: Re-run audit and save the canonical state**

```bash
network-events audit \
  --bids-dir "$NETWORK_FMRI_BIDS_DIR" \
  --behavioral-dir /oak/stanford/groups/russpold/data/network_grant/behavioral_data/canonical
datalad -C /oak/stanford/groups/russpold/data/network_grant/behavioral_data/canonical save \
  -m "curate canonical behavior for 46-subject BIDS dataset"
```

Expected: audit exits zero and DataLad reports a clean dataset.

- [ ] **Step 5: Record and protect the canonical commit**

Run: `git -C /oak/stanford/groups/russpold/data/network_grant/behavioral_data/canonical rev-parse HEAD`

Expected: a 40-character commit for workflow configuration, with annexed content
available from a configured DataLad remote.

### Task 3: Implement `network_qa` Scan Decisions

**Files:**
- Execute: `docs/superpowers/plans/2026-09-18-network-qa-scan-decisions.md`

**Interfaces:**
- Produces: reviewed `network_qa` commit exposing decision generation and validation

- [ ] **Step 1: Execute every task in the network-qa plan**

Use its repository checkout and record the final full commit hash.

- [ ] **Step 2: Verify its release artifact**

Run: `uv run --frozen pytest -q && uv build`

Expected: PASS and successful wheel/sdist build.

### Task 4: Replace `network_fmri` Orchestration

**Files:**
- Execute: `docs/superpowers/plans/2026-09-18-network-fmri-orchestration.md`

**Interfaces:**
- Consumes: reviewed `network_events`, `network_qa`, `network_fw2bids`, and `global_signal_plots` commits
- Produces: simplified branch ready for Sherlock pilot

- [ ] **Step 1: Execute every task in the network-fmri plan**

Update dependency pins only after upstream full commit hashes are recorded.

- [ ] **Step 2: Run repository verification**

Run: `uv run --frozen pytest -q && uv build && git diff --check`

Expected: PASS, successful build, and clean diff validation.

- [ ] **Step 3: Inspect the dry-run plan**

Run: `uv run --frozen network-fmri pipeline plan config/workflow.local.toml`

Expected: one dataset, approved stage order, hard decision stop, no `datalad run`, and no
secret values.

### Task 5: Sherlock Pilot and Full Sample

**Files:**
- Configure: `config/workflow.local.toml` on Sherlock
- Follow: `docs/SHERLOCK.md`

**Interfaces:**
- Produces: validated pilot evidence followed by the complete 46-subject DataLad dataset

- [ ] **Step 1: Run the documented small pilot roster**

Expected: source assembly, behavior audit, preparation, MRIQC, decision generation,
approval validation, curation, final validation, and fMRIPrep complete for the pilot.

- [ ] **Step 2: Review pilot receipts and output integrity**

Require a clean DataLad state, no validator errors, complete MRIQC/fMRIPrep coverage, and
correct pre/post global-signal derivative roots.

- [ ] **Step 3: Submit the 46-subject workflow through decision generation**

Expected: the pipeline stops after saving `scan-decisions-generated`.

- [ ] **Step 4: Review and approve every flagged decision**

Require no `review` rows, complete drop reasons, reviewer identities/timestamps, and a
passing `network-fmri decisions validate` result.

- [ ] **Step 5: Resume curation and fMRIPrep**

Expected: curated validator has no errors, all approved subjects have successful fMRIPrep
outputs, consolidation saves `fmriprep-complete`, and the DataLad dataset is clean.
