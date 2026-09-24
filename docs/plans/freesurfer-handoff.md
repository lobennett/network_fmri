# Standalone FreeSurfer implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` for native execution
> or `superpowers:subagent-driven-development` if the user selects delegation.

**Goal:** Reconstruct with FreeSurfer 8.2.0, pause for surface approval, and have
fMRIPrep reuse the approved reconstruction without resuming recon-all.

**Architecture:** Keep upstream MechaBABS/BABS responsible for jobs and merging.
Replace the anatomical app, strengthen the existing surface evidence gate, and
extend the current handoff controller to advance through manual review boundaries.

**Stack:** Python 3.13, uv, pytest, DataLad, pinned MechaBABS/BABS, Apptainer, Slurm.

**Spec:** [Approved design](freesurfer-dashboard-design.md).
Companion: [provenance and dashboard](provenance-dashboard.md).

## Global constraints

- FreeSurfer 8.2.0; fMRIPrep 25.2.5; container checksum and build recorded.
- Fresh reconstruction; preserve FreeSurfer 7 evidence as superseded.
- License: `/home/users/logben/license.txt`; never commit its contents.
- Surface-review criteria remain deferred until Seda's guidance.
- Anatomical images must already be defaced before durable publication.
- No upstream fork, replacement scheduler, automatic approval, or automatic retry.

## Review focus

- Multiple anatomical candidates must block rather than select lexicographically (task 1).
- Session naming must match the FreeSurfer subject ID used by fMRIPrep (task 2).
- Archive repacking must not change a content-based approval fingerprint (task 3).
- Controller restart after submission must not submit duplicate jobs (task 4).
- Changed inputs or surfaces must invalidate approval before fMRIPrep starts (tasks 3–5).

## 1. A reproducible standalone application

**Files:** create `src/network_fmri/freesurfer_app.py`,
`containers/freesurfer-8.2.0.def`, `tests/test_freesurfer_app.py`;
modify `config.py`, `containers.py`, `pyproject.toml` under the existing package.

**Interface:** `select_anatomy(bids: Path, subject: str) -> tuple[Path, Path | None]`
returns the single retained T1w and optional retained T2w. `recon_command(t1: Path,
t2: Path | None, subject: str, subjects_dir: Path, threads: int) -> tuple[str, ...]`
builds the command without executing it. A participant CLI runs it and writes its
input inventory, command, container identity, and exit status into the derivative.

- [ ] Add tests for one T1w, optional T2w, missing T1w, duplicate T1w/T2w, unsafe
  subject IDs, and an existing reconstruction directory. For example:
  ```python
  def test_ambiguous_anatomy_is_rejected(tmp_path):
      import pytest
      from network_fmri.freesurfer_app import select_anatomy
      anat = tmp_path / "sub-s03" / "anat"
      anat.mkdir(parents=True)
      for run in (1, 2):
          (anat / f"sub-s03_run-{run}_T1w.nii.gz").touch()
      with pytest.raises(ValueError, match="T1w"):
          select_anatomy(tmp_path, "s03")
  ```
- [ ] Run `uv run pytest tests/test_freesurfer_app.py`; confirm failures identify
  the absent selection/command behavior.
- [ ] Implement deterministic input selection and argument-list execution.
  Use `recon-all -s sub-s03 -sd SUBJECTS_DIR -i T1 -all -openmp N`, adding
  `-T2 T2 -T2pial` when one reviewed T2w exists. Record this explicit protocol;
  do not introduce longitudinal processing or automatic anatomical averaging.
- [ ] Build the Linux image from official 8.2.0 distribution assets, checking
  release patches before locking the definition. Include the participant adapter
  only if a verified existing BIDS app cannot provide this contract. Register the
  resulting checksum-pinned image in the existing container dataset.
- [ ] Rerun the focused tests, verify `recon-all -version` inside the image, and
  commit `Add standalone FreeSurfer 8.2.0 application`.

## 2. Wire the upstream application dependency

**Files:** create `src/network_fmri/mechababs/apps/FreeSurfer-8.2.0.yaml`;
modify `src/network_fmri/mechababs/apps/fMRIPrep-25.2.5+full.yaml`,
`config/workflow.example.toml`, `study.py`, `tests/test_mechababs_configs.py`,
`tests/test_study.py`. Retain legacy templates only to inspect old campaigns.

**Interface:** retain the public `anatomical` stage name; its app becomes
`FreeSurfer-8.2.0`. Archive contents are rooted at
`FreeSurfer-8.2.0/subjects/sub-SUBJECT/`. Full fMRIPrep depends on that app and
consumes the unzipped derivative through BABS's `input_datasets` contract.

- [ ] Add YAML/rendering tests asserting the new dependency, zipped input path,
  license mount, and these reuse options:
  ```yaml
  --fs-subjects-dir: '"${PWD}"/sourcedata/FreeSurfer-8.2.0/FreeSurfer-8.2.0/subjects'
  --fs-no-resume: ""
  --no-track-sessions: ""
  ```
- [ ] Run `uv run pytest tests/test_mechababs_configs.py tests/test_study.py`;
  confirm old anatomy configuration fails the new contracts.
- [ ] Implement the new template and render settings. Start the standalone pilot
  with 4 CPUs, 48 GB, and 24 hours; use measured peak memory/runtime before scaling.
  Preserve full fMRIPrep's existing output-space, echo, and dummy-scan settings.
- [ ] Verify the generated BABS script stages the approved subject directory in
  the actual container path. Use a job-local writable copy if required; never
  allow the canonical reviewed derivative to be modified by fMRIPrep.
- [ ] Rerun tests and commit `Use standalone surfaces in the MechaBABS campaign`.

## 3. Extract and seal surface evidence

**Files:** create `src/network_fmri/surface_evidence.py`,
`tests/test_surface_evidence.py`; modify `qa/freesurfer.py`, `pipeline.py`,
`tests/test_freesurfer_stage.py`, `tests/test_processing.py`.

**Interface:** `prepare_surface_evidence(config: WorkflowConfig) -> Path` returns
an immutable extracted subjects directory. Metadata binds its sorted file
SHA-256 inventory to the source derivative commit, input commit, and container.
Existing `surface_fingerprints`, `generate_surface_review`, and
`require_committed_surface_approval` use that inventory.

- [ ] Test equivalent ZIP/directory contents produce the same fingerprint;
  changing a surface byte produces a different one. Also test missing required
  reconstruction outputs, traversal entries, duplicate archive paths, and unsafe
  links. Resolve safe internal links for hashing; reject links escaping the tree.
  ```python
  # In the archive fixture test, compare content identities, not ZIP CRCs.
  assert surface_fingerprints(config, unpacked) == surface_fingerprints(config, zipped)
  ```
- [ ] Run `uv run pytest tests/test_surface_evidence.py tests/test_freesurfer_stage.py`
  and confirm the current CRC-based archive fingerprint fails equivalence.
- [ ] Implement streamed hashing, safe staged extraction, atomic publication,
  and source inventory checks. Reuse MRIQC extraction policy where practical
  without turning MRIQC-specific handling into a generic workflow framework.
- [ ] Preserve existing reviewed TSVs when the bound evidence is identical;
  otherwise stop with an evidence-changed error. Legacy approvals remain historical
  and cannot approve the new reconstruction.
- [ ] Rerun tests including `tests/test_processing.py`; commit
  `Bind surface approval to verified reconstruction evidence`.

## 4. Advance automatically to each review gate

**Files:** modify `handoff.py`, `cli.py`, `processing.py`, `tests/test_handoff.py`,
`tests/test_cli.py`; create `scripts/run_processing.sh`.

**Interface:** `run_processing(config, *, interval=300, manager=None,
sleep=time.sleep) -> dict` returns a structured pause/completion reason.
Expose `network-fmri processing run CONFIG`; preserve `run-mriqc` for existing jobs.

- [ ] Add runner-injected tests covering MRIQC merge/extraction, scan-review pause,
  approved curation, anatomy submission/merge/extraction, surface-review pause,
  approved fMRIPrep, final output-review pause, failures, and restart at each boundary.
  ```python
  # Fake manager is already merged at anatomy, with unapproved surface review.
  result = run_processing(config, manager=manager, sleep=lambda _: None)
  assert result["state"] == "awaiting-surface-review"
  assert "fmriprep" not in manager.advanced_stages
  ```
- [ ] Run `uv run pytest tests/test_handoff.py tests/test_cli.py` before implementing.
- [ ] Use the existing study Git-directory lock for one controller. Dispatch only
  stages reported ready/active upstream; persist evidence milestones before advancing.
  Surface or scan approval is always checked against the current inputs. Exit on
  failed/intervention-required jobs without resetting or automatically retrying them.
- [ ] Have the controller refresh records after transitions when an external index
  path is configured. Index failure must be visible but must not resubmit processing.
  Submit the controller with `--propagate=NONE`; heavy work remains in BABS workers.
- [ ] Rerun tests and commit `Automate processing through manual review boundaries`.

## 5. Verify and migrate the sub-s03 pilot

**Files:** update `docs/mechababs.md`, `docs/sherlock.md`, and a short pilot result
record under the study's `code/network_fmri/`; no participant data in the source repo.

- [ ] Run `uv run pytest` and `uv build` in the supported Linux environment.
- [ ] Inspect the old pilot's current job state. Stop only superseded running anatomy
  work after capturing its job/log identity; retain completed results and commits.
- [ ] Initialize a fresh campaign label. Reuse MRIQC only through supported upstream
  mechanisms with matching input/configuration/review seals. If import is unsupported,
  report that blocker rather than fabricate a merged state or silently rerun MRIQC.
- [ ] Submit standalone FreeSurfer for sub-s03; verify actual build, input selection,
  completeness, resource use, automated merge/extraction, and unapproved review pause.
- [ ] Wait for actual surface approval. Then pilot fMRIPrep and compare the approved
  surface inventory before/after; inspect logs to ensure recon-all was not resumed.
  Check requested volumetric, surface, CIFTI, confounds, echo outputs, and reports.
- [ ] Record failures or compatibility changes explicitly; do not expand to all
  subjects until the handoff passes. Push verified code to the existing feature
  branch and record the deployed commit and durable DataLad backup status.
