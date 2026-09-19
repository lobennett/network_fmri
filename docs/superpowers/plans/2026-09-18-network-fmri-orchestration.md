# Network fMRI Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the cohort/campaign workflow with one readable Slurm pipeline that constructs, reviews, curates, and preprocesses the single 46-subject BIDS dataset.

**Architecture:** Typed configuration and stage results feed a fixed dependency graph. Focused stage modules call pinned packages or containers, while one milestone service owns receipts and serialized DataLad saves; MRIQC evidence flows through `network_qa` into a hard human-approval gate before curation and fMRIPrep.

**Tech Stack:** Python 3.13, tomllib, dataclasses, subprocess, nibabel, DataLad, Slurm, Apptainer, uv, pytest

**Spec:** `docs/superpowers/specs/2026-09-18-simplified-bids-pipeline-design.md`

## Global Constraints

- Build one 46-subject dataset; remove the discovery/validation split.
- Use `datalad save` at milestones and never use `datalad run`.
- Array workers never save the shared DataLad dataset.
- Remove seven BOLD volumes exactly once and use fMRIPrep `--dummy-scans 0`.
- Store global-signal derivatives at `derivatives/gs-pretrim` and `derivatives/gs-posttrim`.
- Save validator diagnostics before returning a validator error.
- Block fMRIPrep until `scan_decisions.tsv` passes approval validation.
- Keep `FLYWHEEL_API_TOKEN` in the environment and out of config, commands, and receipts.
- Initially configure MRIQC 24.0.2 and fMRIPrep 25.2.5.

---

### Task 1: Typed Single-Dataset Configuration

**Files:**
- Create: `src/network_fmri/config.py`
- Create: `src/network_fmri/models.py`
- Create: `tests/test_config.py`
- Create: `config/workflow.example.toml`

**Interfaces:**
- Produces: `WorkflowConfig.load(path: Path) -> WorkflowConfig`
- Produces: `WorkflowPaths`, `BehaviorSource`, `ContainerConfig`, and `SlurmConfig`
- Produces: `StageResult(name: str, outputs: tuple[Path, ...], details: dict[str, object])`
- Produces: `Runner` protocol matching `subprocess.run`

- [ ] **Step 1: Write failing configuration tests**

```python
def test_loads_single_dataset_configuration(tmp_path):
    config = WorkflowConfig.load(write_valid_config(tmp_path))
    assert config.paths.bids_dir == tmp_path / "bids"
    assert config.mriqc.version == "24.0.2"
    assert config.fmriprep.version == "25.2.5"

def test_rejects_short_behavior_commit(tmp_path):
    path = write_config(tmp_path, behavior_commit="445eba8")
    with pytest.raises(ValueError, match="40-character"):
        WorkflowConfig.load(path)
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `uv run --frozen pytest tests/test_config.py -q`

Expected: FAIL because `network_fmri.config` does not exist.

- [ ] **Step 3: Implement frozen config types and strict TOML parsing**

```python
@dataclass(frozen=True)
class WorkflowConfig:
    paths: WorkflowPaths
    subjects_file: Path
    flywheel_project: str
    behavior: BehaviorSource
    mriqc: ContainerConfig
    fmriprep: ContainerConfig
    slurm: SlurmConfig

    @classmethod
    def load(cls, path: Path) -> "WorkflowConfig":
        return parse_config(tomllib.loads(path.read_text()), base=path.parent)

@dataclass(frozen=True)
class StageResult:
    name: str
    outputs: tuple[Path, ...]
    details: dict[str, object] = field(default_factory=dict)
```

Require absolute runtime paths, exactly 46 unique `s[0-9]+` roster labels, a full
canonical behavior commit, positive resources, and distinct parts/work/log/BIDS paths.

- [ ] **Step 4: Run configuration tests**

Run: `uv run --frozen pytest tests/test_config.py -q`

Expected: PASS.

- [ ] **Step 5: Commit configuration**

```bash
git add src/network_fmri/config.py src/network_fmri/models.py tests/test_config.py config/workflow.example.toml
git commit -m "feat: define single-dataset workflow configuration"
```

### Task 2: Milestone Receipts and DataLad Saves

**Files:**
- Create: `src/network_fmri/milestones.py`
- Replace: `src/network_fmri/provenance.py`
- Create: `tests/test_milestones.py`

**Interfaces:**
- Produces: `MilestoneReceipt(stage, status, inputs, outputs, versions, jobs, validation)`
- Produces: `save_milestone(bids_dir, receipt, runner=subprocess.run) -> str`
- Produces: `save_diagnostic(bids_dir, stage, paths, runner=subprocess.run) -> str`

- [ ] **Step 1: Write failing receipt tests**

```python
def test_save_milestone_calls_one_datalad_save(tmp_path, runner):
    commit = save_milestone(tmp_path, receipt("bids-assembled"), runner)
    assert runner.calls == [["datalad", "save", "-d", str(tmp_path),
                             "-m", "bids-assembled"]]
    assert commit == "abc123"

def test_receipt_never_contains_token_value(tmp_path, runner, monkeypatch):
    monkeypatch.setenv("FLYWHEEL_API_TOKEN", "secret-value")
    save_milestone(tmp_path, receipt("stage"), runner)
    assert "secret-value" not in read_all_files(tmp_path)
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run --frozen pytest tests/test_milestones.py -q`

Expected: FAIL because milestone services do not exist.

- [ ] **Step 3: Implement atomic receipts and explicit saves**

```python
def save_milestone(
    bids_dir: Path,
    receipt: MilestoneReceipt,
    runner: Runner = subprocess.run,
) -> str:
    write_json_atomic(receipt_path(bids_dir, receipt.stage), asdict(receipt))
    runner(["datalad", "save", "-d", str(bids_dir), "-m", receipt.stage], check=True)
    return git_head(bids_dir, runner)
```

Write receipts beneath `code/network_fmri/milestones`. Diagnostic saves use message
`<stage>-failed-diagnostics` and never create a success receipt.

- [ ] **Step 4: Run milestone tests**

Run: `uv run --frozen pytest tests/test_milestones.py -q`

Expected: PASS and no executed command contains `datalad run`.

- [ ] **Step 5: Commit milestone services**

```bash
git add src/network_fmri/milestones.py src/network_fmri/provenance.py tests/test_milestones.py
git commit -m "feat: save explicit DataLad milestones"
```

### Task 3: Source Assembly and Canonical Behavior

**Files:**
- Create: `src/network_fmri/stages/__init__.py`
- Create: `src/network_fmri/stages/assembly.py`
- Create: `src/network_fmri/stages/behavior.py`
- Create: `tests/test_assembly_stage.py`
- Create: `tests/test_behavior_stage.py`

**Interfaces:**
- Produces: `convert_subject(config, subject, runner=subprocess.run) -> StageResult`
- Produces: `assemble_dataset(config, runner=subprocess.run) -> StageResult`
- Produces: `ingest_behavior(config, runner=subprocess.run) -> StageResult`

- [ ] **Step 1: Write failing source-stage tests**

```python
def test_conversion_does_not_put_token_on_command_line(config, runner):
    convert_subject(config, "s03", runner)
    command = runner.calls[0]
    assert command[:2] == ["network-fw2bids", "convert"]
    assert "FLYWHEEL_API_TOKEN" not in " ".join(command)

def test_ingest_refuses_wrong_canonical_commit(config, git_runner):
    git_runner.head = "f" * 40
    with pytest.raises(StageError, match="canonical behavior commit"):
        ingest_behavior(config, git_runner)
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `uv run --frozen pytest tests/test_assembly_stage.py tests/test_behavior_stage.py -q`

Expected: FAIL because source stages do not exist.

- [ ] **Step 3: Implement conversion, atomic assembly, ingestion, and audit**

```python
def ingest_behavior(
    config: WorkflowConfig,
    runner: Runner = subprocess.run,
) -> StageResult:
    require_git_head(config.behavior.source, config.behavior.commit, runner)
    destination = config.paths.bids_dir / "sourcedata/behavioral"
    copy_content(config.behavior.source, destination)
    run_checked(["network-events", "audit", "--bids-dir", str(config.paths.bids_dir),
                 "--behavioral-dir", str(destination)], runner)
    return StageResult("behavioral-sourcedata-ingested", outputs=(destination,))
```

Use `network-fw2bids` for subject conversion and assembly. Require exact roster coverage,
dereferenced canonical CSV content, and a provenance receipt carrying the source commit.

- [ ] **Step 4: Run source-stage tests**

Run: `uv run --frozen pytest tests/test_assembly_stage.py tests/test_behavior_stage.py -q`

Expected: PASS.

- [ ] **Step 5: Commit source stages**

```bash
git add src/network_fmri/stages tests/test_assembly_stage.py tests/test_behavior_stage.py
git commit -m "feat: assemble BIDS and ingest canonical behavior"
```

### Task 4: BIDS Preparation Stages

**Files:**
- Create: `src/network_fmri/stages/global_signal.py`
- Create: `src/network_fmri/stages/events.py`
- Modify: `src/network_fmri/prepare/trim.py`
- Modify: `src/network_fmri/prepare/sidecar.py`
- Modify: `src/network_fmri/prepare/b0link.py`
- Create: `tests/test_global_signal.py`
- Create: `tests/test_prepare.py`
- Create: `tests/test_events_stage.py`

**Interfaces:**
- Produces: `run_global_signal(bids_dir, label, runner=subprocess.run) -> StageResult`
- Produces: `trim_dataset(bids_dir, jobs) -> StageResult`
- Produces: `generate_events(bids_dir, runner=subprocess.run) -> StageResult`
- Produces: `link_b0(bids_dir) -> StageResult`

- [ ] **Step 1: Write failing atomicity and layout tests**

```python
def test_global_signal_creates_derivative_dataset(tmp_path, runner):
    run_global_signal(tmp_path, "pretrim", runner)
    root = tmp_path / "derivatives/gs-pretrim"
    assert json.loads((root / "dataset_description.json").read_text())["DatasetType"] == "derivative"

def test_any_trim_error_fails_the_stage(tmp_path):
    write_bad_bold(tmp_path)
    with pytest.raises(StageError, match="trim failed"):
        trim_dataset(tmp_path, jobs=1)
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run --frozen pytest tests/test_global_signal.py tests/test_prepare.py tests/test_events_stage.py -q`

Expected: FAIL because wrappers are absent and trim currently tolerates short files.

- [ ] **Step 3: Implement focused stages**

```python
def run_global_signal(
    bids_dir: Path,
    label: Literal["pretrim", "posttrim"],
    runner: Runner = subprocess.run,
) -> StageResult:
    output = bids_dir / "derivatives" / f"gs-{label}"
    write_derivative_description(output, f"Global signal {label}")
    run_checked(["nf-global-signal", "--bids-dir", str(bids_dir),
                 "--out-tsv", str(output / "gs_metrics.tsv"),
                 "--out-pdf", str(output / "gs.pdf")], runner)
    return StageResult(f"gs-{label}", outputs=(output,))
```

Make sidecar reads fail on absent/malformed JSON. Treat `too_short` and `error` as trim
failure. Remove all `record()` wrappers. Call audited `network-events create`; explicit
conversion findings remain review evidence. Keep B0 writes atomic and idempotent.

- [ ] **Step 4: Run preparation tests**

Run: `uv run --frozen pytest tests/test_global_signal.py tests/test_prepare.py tests/test_events_stage.py -q`

Expected: PASS.

- [ ] **Step 5: Commit preparation stages**

```bash
git add src/network_fmri/stages src/network_fmri/prepare tests/test_global_signal.py tests/test_prepare.py tests/test_events_stage.py
git commit -m "feat: add deterministic BIDS preparation stages"
```

### Task 5: Validation, Decisions, and Curation

**Files:**
- Replace: `src/network_fmri/qa/validate.py`
- Create: `src/network_fmri/stages/decisions.py`
- Create: `src/network_fmri/curation.py`
- Create: `tests/test_validator_stage.py`
- Create: `tests/test_decisions_stage.py`
- Create: `tests/test_curation_stage.py`
- Delete: `src/network_fmri/fw2bids/qa_reject.py`

**Interfaces:**
- Produces: `validate_bids(bids_dir, label, runner=subprocess.run) -> ValidationResult`
- Produces: `generate_decisions(bids_dir, runner=subprocess.run) -> StageResult`
- Produces: `validate_decisions(bids_dir, runner=subprocess.run) -> StageResult`
- Produces: `apply_curation(bids_dir, manifest, runner=subprocess.run) -> StageResult`

- [ ] **Step 1: Write failing persistence and gate tests**

```python
def test_validator_failure_keeps_reports(tmp_path, runner):
    runner.returncode = 1
    with pytest.raises(ValidationError):
        validate_bids(tmp_path, "precuration", runner)
    assert (tmp_path / "derivatives/bids-validator/desc-precuration_validation.json").is_file()

def test_drop_removes_echo_bundle_but_keeps_behavior(tmp_path):
    dataset = multi_echo_dataset(tmp_path, with_events=True, with_behavior=True)
    apply_curation(dataset.root, approved_drop_manifest(dataset.identity))
    assert not list(dataset.func.glob(f"{dataset.stem}*"))
    assert dataset.behavior_file.is_file()
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run --frozen pytest tests/test_validator_stage.py tests/test_decisions_stage.py tests/test_curation_stage.py -q`

Expected: FAIL against legacy validation/source-mutation behavior.

- [ ] **Step 3: Implement validator, QA seam, and bundle removal**

```python
def generate_decisions(
    bids_dir: Path,
    runner: Runner = subprocess.run,
) -> StageResult:
    manifest = bids_dir / "code/network_fmri/scan_decisions.tsv"
    run_checked(["network-qa", "decisions", "generate", "--bids-dir", str(bids_dir),
                 "--mriqc-dir", str(bids_dir / "derivatives/mriqc"),
                 "--output", str(manifest)], runner)
    return StageResult("scan-decisions-generated", outputs=(manifest, manifest.with_suffix(".meta.json")))
```

Run `bids-validator <bids-dir> --outfile <labelled-validation-json> --format json_pp
--prune`; the implementation supplies the concrete paths derived from `bids_dir` and
`label`. Retain JSON and logs before raising. Curation first delegates approval validation
to `network-qa`, resolves bundles
from BIDS entities, rejects zero/ambiguous matches before mutation, removes all echoes,
sidecars and generated events, retains raw behavior, rebuilds B0 links, and validates.
The public `network-fmri decisions validate` command invokes `network-qa decisions
approve` to validate and seal the reviewed TSV, then saves `scan-decisions-approved`.
Curation invokes the read-only `network-qa decisions validate` command so a later edit
invalidates the seal.

- [ ] **Step 4: Run validation/decision/curation tests**

Run: `uv run --frozen pytest tests/test_validator_stage.py tests/test_decisions_stage.py tests/test_curation_stage.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the review gate**

```bash
git add -A src/network_fmri/qa/validate.py src/network_fmri/stages/decisions.py src/network_fmri/curation.py src/network_fmri/fw2bids/qa_reject.py tests/test_validator_stage.py tests/test_decisions_stage.py tests/test_curation_stage.py
git commit -m "feat: gate curation on approved scan decisions"
```

### Task 6: MRIQC and fMRIPrep Applications

**Files:**
- Create: `src/network_fmri/containers.py`
- Replace: `src/network_fmri/qa/mriqc.py`
- Replace: `src/network_fmri/qa/fmriprep.py`
- Create: `tests/test_mriqc_stage.py`
- Create: `tests/test_fmriprep_stage.py`

**Interfaces:**
- Produces: participant/group command builders and `verify_mriqc(config)`
- Produces: participant command builder and `verify_fmriprep(config)`

- [ ] **Step 1: Write failing command tests**

```python
def test_mriqc_uses_approved_threshold(config):
    command = mriqc_participant_command(config, "s03")
    assert option(command, "--fd_thres") == "0.5"
    assert "--no-sub" in command

def test_fmriprep_uses_trimmed_contract(config):
    command = fmriprep_participant_command(config, "s03")
    assert option(command, "--dummy-scans") == "0"
    assert "--no-submm-recon" in command
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run --frozen pytest tests/test_mriqc_stage.py tests/test_fmriprep_stage.py -q`

Expected: FAIL because current modules unpack BABS archives.

- [ ] **Step 3: Implement Apptainer commands and consolidation checks**

```python
def mriqc_participant_command(config: WorkflowConfig, subject: str) -> tuple[str, ...]:
    return apptainer_prefix(config.mriqc) + (
        "/data", "/out", "participant", "--participant-label", subject,
        "--fd_thres", "0.5", "--no-sub", "--no-datalad-get",
    )
```

Bind BIDS read-only, derivatives/work writable, TemplateFlow read-only, and job-local
`/tmp`. Run fMRIPrep once per subject across sessions with fixed seeds, established output
spaces, FreeSurfer license, `--no-submm-recon`, and `--skip-bids-validation`.
Consolidation requires every roster subject, reports, derivative descriptions, and no
crash files.

- [ ] **Step 4: Run application tests**

Run: `uv run --frozen pytest tests/test_mriqc_stage.py tests/test_fmriprep_stage.py -q`

Expected: PASS.

- [ ] **Step 5: Commit application stages**

```bash
git add src/network_fmri/containers.py src/network_fmri/qa/mriqc.py src/network_fmri/qa/fmriprep.py tests/test_mriqc_stage.py tests/test_fmriprep_stage.py
git commit -m "feat: run MRIQC and fMRIPrep directly"
```

### Task 7: Fixed Slurm Graph and Minimal CLI

**Files:**
- Replace: `src/network_fmri/pipeline.py`
- Create: `src/network_fmri/slurm.py`
- Replace: `src/network_fmri/cli.py`
- Create: `tests/test_stage_graph.py`
- Create: `tests/test_slurm.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `build_plan(config) -> tuple[PlannedJob, ...]`
- Produces: `submit_plan(plan, dry_run) -> SubmissionRecord`
- Produces: `network-fmri pipeline plan|submit|status`, `decisions validate`, and `curate`

- [ ] **Step 1: Write failing graph and public-surface tests**

```python
def test_approval_precedes_curation_and_fmriprep(config):
    names = [job.name for job in build_plan(config)]
    assert names.index("scan-decisions-approved") < names.index("mriqc-curated")
    assert names.index("mriqc-curated") < names.index("fmriprep-array")

def test_array_workers_never_call_datalad(config):
    assert all("datalad" not in " ".join(job.command)
               for job in build_plan(config) if job.array)
```

- [ ] **Step 2: Run graph tests and confirm failure**

Run: `uv run --frozen pytest tests/test_stage_graph.py tests/test_slurm.py tests/test_cli.py -q`

Expected: FAIL because the old graph is cohort/campaign based.

- [ ] **Step 3: Implement fixed dependencies and operator stop**

```python
STAGE_ORDER = (
    "fw2bids-array", "bids-assembled", "behavioral-sourcedata-ingested",
    "gs-pretrim", "dummy-volumes-trimmed", "bids-events-generated",
    "gs-posttrim", "b0-fieldmaps-linked", "bids-precuration-validated",
    "mriqc-array", "mriqc-complete", "scan-decisions-generated",
    "scan-decisions-approved", "mriqc-curated", "bids-curated-validated",
    "fmriprep-array", "fmriprep-complete",
)
```

Submit with `sbatch --parsable`, `afterok`, per-stage resources, array throttles, and
separate log patterns. First submission stops at generated decisions. Resume refuses to
submit curation until approval validation passes. Dry-run has no filesystem effects.

- [ ] **Step 4: Run graph, Slurm, and CLI tests**

Run: `uv run --frozen pytest tests/test_stage_graph.py tests/test_slurm.py tests/test_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Commit orchestration surface**

```bash
git add src/network_fmri/pipeline.py src/network_fmri/slurm.py src/network_fmri/cli.py tests/test_stage_graph.py tests/test_slurm.py tests/test_cli.py
git commit -m "feat: orchestrate simplified Slurm pipeline"
```

### Task 8: Remove Legacy Surface, Pin Dependencies, and Verify End to End

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Replace: `README.md`
- Create: `docs/SHERLOCK.md`
- Modify: `docs/SCAN-NOTES.md`
- Create: `tests/test_pipeline_e2e.py`
- Delete: `src/network_fmri/cohorts.py`, `workflow.py`, `registry.py`
- Delete: `src/network_fmri/fw2bids/`, `glm/`, `integrations/`
- Delete: obsolete campaign/archive/shim/exclusions/check modules and their tests

**Interfaces:**
- Produces: reduced installable package and synthetic full-flow acceptance test

- [ ] **Step 1: Write failing replacement-surface test**

```python
def test_synthetic_pipeline_stops_then_resumes(tmp_path, fake_apps):
    first = run_until_review(synthetic_config(tmp_path))
    assert first.last_stage == "scan-decisions-generated"
    approve_all_flagged(first.manifest)
    second = resume_pipeline(first.config)
    assert second.last_stage == "fmriprep-complete"
    assert datalad_messages(tmp_path / "bids") == EXPECTED_MILESTONES

def test_source_contains_no_datalad_run():
    assert "datalad run" not in "\n".join(p.read_text() for p in Path("src").rglob("*.py"))
```

- [ ] **Step 2: Run the end-to-end test and confirm failure**

Run: `uv run --frozen pytest tests/test_pipeline_e2e.py -q`

Expected: FAIL until legacy surfaces and dependency pins are replaced.

- [ ] **Step 3: Remove legacy code and pin reviewed packages**

Keep DataLad, `global-signal-plots[plot]`, `network-events`, and `network-qa`; add
`network-fw2bids`, `nibabel`, and `bids-validator-deno`; remove `fw-heudiconv` and
`network-glm`. Pin `network_fw2bids` at
`46fc59edbc90a1332b4cbd82796b59336a75f999` and `global_signal_plots` at
`ee5ad00ad2f51586d0379d2fe5a6a5fa3e2e60d7`. Pin `network_events` and `network_qa` to
the full reviewed HEADs produced by their preceding plans.

Document dry run, pilot, approval, resume, DataLad remote safety, diagnostics, and the
full 46-subject run. Preserve scientific history in `SCAN-NOTES.md` while removing old
cohort/campaign instructions.

- [ ] **Step 4: Lock, test, build, and check the tree**

Run: `uv lock && uv sync --frozen && uv run --frozen pytest -q && uv build && git diff --check`

Expected: all tests PASS, distributions build, and the tree has no whitespace errors.

- [ ] **Step 5: Commit the completed replacement**

```bash
git add -A
git commit -m "refactor: replace legacy workflow with BIDS pipeline"
```
