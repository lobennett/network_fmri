# Integrated Anatomical Defacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deface every T1w and T2w image with PyDeface inside node-local temporary storage before any subject part or assembled BIDS dataset is written to persistent storage.

**Architecture:** `network_fw2bids` owns the privacy boundary: it downloads, converts, defaces, validates, and receipts anatomy beneath `$SLURM_TMPDIR`, then copies only the safe tree to a same-filesystem publication stage. Its assembler independently verifies receipts and checksums. `network_fmri` supplies the pinned PyDeface container identity, records it in milestone provenance, and refuses legacy or incomplete parts.

**Tech Stack:** Python 3.13, PyDeface 2.1.0, FSL 6.0.7.18, Apptainer, dcm2niix, NiBabel, Slurm, DataLad, uv, pytest

**Spec:** `docs/superpowers/specs/2026-09-20-integrated-anatomical-defacing-design.md`

## Global Constraints

- Undefaced DICOMs and NIfTI images may exist only beneath a validated `$SLURM_TMPDIR`.
- Oak, persistent scratch, subject parts, DataLad, and the assembled BIDS dataset receive only defaced anatomy.
- Run PyDeface for every T1w and T2w image and for no functional, fieldmap, or diffusion image.
- Use a pinned Apptainer image containing PyDeface 2.1.0 and FSL 6.0.7.18.
- Publish no subject part unless all anatomy validates and the sensitive workspace has been removed.
- Never overwrite an existing subject part or assembled BIDS dataset.
- Never store credentials, DICOM metadata, voxel data, or temporary paths in commands, logs, or receipts.
- Array workers never perform DataLad saves.
- Follow strict red-green-refactor TDD for every behavioral change.

---

### Task 1: Node-Local Sensitive Workspace and Defacing Contracts

**Repository:** `network_fw2bids`

**Files:**
- Create: `src/network_fw2bids/defacing.py`
- Create: `src/network_fw2bids/sensitive_workspace.py`
- Modify: `src/network_fw2bids/errors.py`
- Create: `tests/test_sensitive_workspace.py`
- Create: `tests/test_defacing.py`

**Interfaces:**
- Produces: `DefaceConfig(image: Path, version: str, sha256: str)`
- Produces: `DefacedImage(path: str, input_sha256: str, output_sha256: str, shape: tuple[int, ...], zooms: tuple[float, ...], affine_sha256: str)`
- Produces: `DefacingReceipt(schema_version: int, subject: str, status: str, software: dict[str, str], images: tuple[DefacedImage, ...])`
- Produces: `sensitive_workspace(environ: Mapping[str, str] = os.environ) -> ContextManager[Path]`
- Produces: `deface_dataset(dataset_root: Path, subject: str, config: DefaceConfig, runner=subprocess.run) -> DefacingReceipt`

- [ ] **Step 1: Write failing workspace boundary tests**

```python
def test_sensitive_workspace_requires_slurm_tmpdir(monkeypatch):
    monkeypatch.delenv("SLURM_TMPDIR", raising=False)
    with pytest.raises(ConversionError, match="SLURM_TMPDIR"):
        with sensitive_workspace():
            pass


def test_sensitive_workspace_is_private_and_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_TMPDIR", str(tmp_path))
    with sensitive_workspace() as workspace:
        assert workspace.parent == tmp_path.resolve()
        assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
        marker = workspace / "undefaced.nii.gz"
        marker.write_bytes(b"sensitive")
    assert not workspace.exists()
```

- [ ] **Step 2: Run the workspace tests and verify RED**

Run: `uv run --frozen pytest tests/test_sensitive_workspace.py -q`

Expected: FAIL because `network_fw2bids.sensitive_workspace` does not exist.

- [ ] **Step 3: Implement strict workspace validation and cleanup**

```python
@contextmanager
def sensitive_workspace(environ: Mapping[str, str] = os.environ) -> Iterator[Path]:
    raw = environ.get("SLURM_TMPDIR")
    if not raw:
        raise ConversionError("SLURM_TMPDIR is required for sensitive conversion")
    root = Path(raw)
    if root.is_symlink() or not root.is_dir() or not os.access(root, os.W_OK | os.X_OK):
        raise ConversionError(f"SLURM_TMPDIR is missing or unsafe: {root}")
    resolved = root.resolve(strict=True)
    if resolved == Path(resolved.anchor):
        raise ConversionError("SLURM_TMPDIR cannot be a filesystem root")
    with TemporaryDirectory(prefix="network-fw2bids-sensitive-", dir=resolved) as name:
        workspace = Path(name)
        workspace.chmod(0o700)
        yield workspace
    if workspace.exists():
        raise ConversionError("sensitive workspace cleanup did not complete")
```

Reject NUL/newline-containing paths and a workspace that resolves outside the configured root. Derive the workspace prefix from validated `SLURM_JOB_ID` and `SLURM_ARRAY_TASK_ID` values. Before creating it, remove only a same-user directory with that exact job-owned name; refuse symlinks, ownership mismatches, or broader prefix cleanup. Install `SIGINT` and `SIGTERM` handlers only while the context is active; each handler raises a private cleanup exception so the context unwinds. Restore prior handlers on exit. Do not claim support for uncatchable `SIGKILL`.

- [ ] **Step 4: Write failing defacing contract tests with real NIfTI fixtures**

```python
def test_deface_dataset_replaces_only_anatomy(tmp_path):
    bids = write_dataset_with_t1w_t2w_and_bold(tmp_path)
    original_bold = (bids / BOLD_PATH).read_bytes()
    receipt = deface_dataset(bids, "s03", CONFIG, runner=FakePyDeface())
    assert [item.path for item in receipt.images] == [T1W_PATH, T2W_PATH]
    assert nib.load(bids / T1W_PATH).shape == (8, 9, 10)
    assert (bids / BOLD_PATH).read_bytes() == original_bold
    assert json.loads((bids / T1W_JSON).read_text())["Defaced"] is True


def test_deface_dataset_rejects_unchanged_output(tmp_path):
    bids = write_dataset_with_t1w(tmp_path)
    with pytest.raises(DefacingError, match="unchanged"):
        deface_dataset(bids, "s03", CONFIG, runner=CopyInputRunner())
```

Use small valid NiBabel images with hand-set shapes, affines, zooms, finite values, and a face-region voxel difference. Mock only the external Apptainer process; keep discovery, validation, sidecar updates, checksums, and receipt construction real.

- [ ] **Step 5: Run the defacing tests and verify RED**

Run: `uv run --frozen pytest tests/test_defacing.py -q`

Expected: FAIL because the defacing contracts are absent.

- [ ] **Step 6: Implement minimal defacing, validation, and receipt construction**

```python
def deface_dataset(
    dataset_root: Path,
    subject: str,
    config: DefaceConfig,
    runner: Runner = subprocess.run,
) -> DefacingReceipt:
    config.verify_image_checksum()
    records = []
    for source in discover_anatomy(dataset_root, subject):
        output = temporary_output_for(source)
        runner(pydeface_command(config, dataset_root, source, output), check=True)
        record = validate_defaced_output(source, output)
        publish_defaced_image_and_sidecar(source, output, config)
        records.append(record)
    return DefacingReceipt.current(subject, config, records)
```

The command uses `apptainer exec --cleanenv --containall`, mounts only `dataset_root` at `/work`, sets container `HOME` and `TMPDIR` beneath `/work`, and runs a command such as `pydeface /work/sub-s03/ses-01/anat/sub-s03_ses-01_T1w.nii.gz --outfile /work/sub-s03/ses-01/anat/.sub-s03_ses-01_T1w.defaced.nii.gz --force`. It contains no host temporary path in the receipt. Validate image readability, exact geometry, finite/nonempty data, changed bytes and voxels, and valid JSON before atomically replacing the staged image and sidecar.

- [ ] **Step 7: Run focused tests and commit**

Run: `uv run --frozen pytest tests/test_sensitive_workspace.py tests/test_defacing.py -q`

Expected: PASS.

```bash
git add src/network_fw2bids/defacing.py src/network_fw2bids/sensitive_workspace.py \
  src/network_fw2bids/errors.py tests/test_sensitive_workspace.py tests/test_defacing.py
git commit -m "feat: define node-local anatomical defacing"
```

### Task 2: Convert and Publish Only Defaced Subject Parts

**Repository:** `network_fw2bids`

**Files:**
- Modify: `src/network_fw2bids/conversion.py`
- Modify: `src/network_fw2bids/api.py`
- Modify: `src/network_fw2bids/cli.py`
- Modify: `tests/test_conversion.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_cli.py`
- Create: `tests/test_private_publication.py`

**Interfaces:**
- Consumes: `DefaceConfig`, `DefacingReceipt`, `sensitive_workspace`, `deface_dataset`
- Produces: `DicomConverter(runner=subprocess.run, deface_config: DefaceConfig | None = None)`
- Produces: `FlywheelBIDS.from_token(..., deface_config: DefaceConfig) -> FlywheelBIDS`
- CLI requires: `--pydeface-image PATH --pydeface-version VERSION --pydeface-sha256 SHA256`

- [ ] **Step 1: Write a failing end-to-end publication-boundary test**

```python
def test_conversion_publishes_only_after_sensitive_workspace_is_gone(tmp_path, monkeypatch):
    node_tmp = tmp_path / "node-tmp"
    persistent = tmp_path / "persistent"
    node_tmp.mkdir()
    persistent.mkdir()
    monkeypatch.setenv("SLURM_TMPDIR", str(node_tmp))
    converter = DicomConverter(runner=ConversionRunner(), deface_config=CONFIG)
    converter.convert([T1_PLAN], persistent / "s03", "russpold/r01network")
    assert not list(node_tmp.iterdir())
    image = persistent / "s03/sub-s03/ses-01/anat/sub-s03_ses-01_T1w.nii.gz"
    assert image.is_file()
    assert json.loads(image.with_name(image.name[:-7] + ".json").read_text())["Defaced"] is True
    assert not find_undefaced_voxels(persistent)
```

The fake external runner must create a realistic dcm2niix output and a different but geometrically identical PyDeface output. Assert observable files and cleanup rather than mock call counts.

- [ ] **Step 2: Run the publication test and verify RED**

Run: `uv run --frozen pytest tests/test_private_publication.py -q`

Expected: FAIL because conversion stages undefaced data beside the persistent destination.

- [ ] **Step 3: Refactor conversion into sensitive and safe publication phases**

```python
with TemporaryDirectory(prefix=".network-fw2bids-safe-", dir=destination.parent) as safe_name:
    safe_stage = Path(safe_name) / "bids"
    with sensitive_workspace() as sensitive:
        staged = sensitive / "bids"
        convert_archives(plans, staged, sensitive)
        receipt = deface_dataset(staged, subject_from_plans(plans), self.deface_config, self._runner)
        write_receipt_atomic(staged, receipt)
        shutil.copytree(staged, safe_stage, symlinks=False)
        verify_safe_copy(safe_stage, receipt)
    publish_directory(safe_stage, destination)
```

Create the persistent parent before opening either context. The persistent stage may contain only validated defaced anatomy. Ensure cleanup of the sensitive context finishes before `publish_directory` runs. If sensitive cleanup or safe-copy verification fails, publish nothing.

- [ ] **Step 4: Require defacing configuration in API and CLI execution**

Add the three CLI flags as an all-or-none required group for `--execute`. Reject an unpinned image, a non-absolute or symlinked image path, non-64-character lowercase SHA-256, or a missing version before authenticating or downloading. Dry-run planning remains possible without the image because it writes no data.

- [ ] **Step 5: Add failure-path tests**

Add one focused test for each behavior:

- PyDeface nonzero exit publishes no destination;
- malformed or geometry-changing output publishes no destination;
- missing or invalid sidecar publishes no destination;
- sensitive cleanup failure publishes no destination;
- safe-copy checksum mismatch publishes no destination;
- existing destination is never replaced;
- functional-only conversion still writes a successful empty receipt; and
- commands and receipts exclude `FLYWHEEL_API_TOKEN` and sensitive host paths.

- [ ] **Step 6: Run conversion/API/CLI tests and commit**

Run: `uv run --frozen pytest tests/test_conversion.py tests/test_api.py tests/test_cli.py tests/test_private_publication.py -q`

Expected: PASS.

```bash
git add src/network_fw2bids/conversion.py src/network_fw2bids/api.py \
  src/network_fw2bids/cli.py tests/test_conversion.py tests/test_api.py \
  tests/test_cli.py tests/test_private_publication.py
git commit -m "feat: publish only defaced subject exports"
```

### Task 3: Verify Defacing Evidence During Assembly

**Repository:** `network_fw2bids`

**Files:**
- Modify: `src/network_fw2bids/defacing.py`
- Modify: `src/network_fw2bids/_assembly.py`
- Modify: `tests/test_defacing.py`
- Modify: `tests/test_batch.py`

**Interfaces:**
- Produces: `receipt_path(dataset_root: Path, subject: str) -> Path`
- Produces: `write_receipt_atomic(dataset_root: Path, receipt: DefacingReceipt) -> Path`
- Produces: `verify_subject_defacing(part: Path, subject: str) -> DefacingReceipt`
- `assemble_subject_parts(...)` copies verified receipts into `code/network_fw2bids/defacing/`

- [ ] **Step 1: Write failing assembly-integrity tests**

```python
@pytest.mark.parametrize("mutation", [
    "missing_receipt", "extra_entry", "missing_entry", "bad_checksum",
    "defaced_false", "symlinked_image", "malformed_receipt",
])
def test_assembly_rejects_unverified_anatomy(tmp_path, mutation):
    part = write_valid_defaced_part(tmp_path, "s03")
    mutate_part(part, mutation)
    with pytest.raises(ConversionError, match="defacing"):
        assemble_subject_parts(write_roster(tmp_path, ["s03"]), tmp_path / "parts", tmp_path / "bids")
    assert not (tmp_path / "bids").exists()


def test_assembly_copies_verified_receipt(tmp_path):
    write_valid_defaced_part(tmp_path, "s03")
    assemble_subject_parts(write_roster(tmp_path, ["s03"]), tmp_path / "parts", tmp_path / "bids")
    assert (tmp_path / "bids/code/network_fw2bids/defacing/sub-s03.json").is_file()
```

- [ ] **Step 2: Run assembly tests and verify RED**

Run: `uv run --frozen pytest tests/test_batch.py -q`

Expected: FAIL because assembly currently copies only subject directories and ignores defacing evidence.

- [ ] **Step 3: Implement exact inventory and checksum verification**

```python
def verify_subject_defacing(part: Path, subject: str) -> DefacingReceipt:
    receipt = load_receipt(receipt_path(part, subject))
    anatomy = inventory_anatomy(part / f"sub-{subject}")
    if set(anatomy) != {item.path for item in receipt.images}:
        raise DefacingError("defacing receipt does not match anatomical inventory")
    for item in receipt.images:
        verify_published_image(part, item)
    return receipt
```

Perform all receipt validation before opening the assembly staging directory. Copy each validated receipt into the final `code/network_fw2bids/defacing/` tree and re-run checksum verification against the staged assembly before publication.

- [ ] **Step 4: Run the complete `network_fw2bids` suite and commit**

Run: `uv run --frozen pytest -q`

Expected: PASS.

```bash
git add src/network_fw2bids/defacing.py src/network_fw2bids/_assembly.py \
  tests/test_defacing.py tests/test_batch.py
git commit -m "feat: verify defacing before BIDS assembly"
```

### Task 4: Configure and Orchestrate Pinned Defacing

**Repository:** `network_fmri`

**Files:**
- Modify: `src/network_fmri/config.py`
- Modify: `src/network_fmri/models.py`
- Modify: `src/network_fmri/stages/assembly.py`
- Modify: `src/network_fmri/pipeline.py`
- Modify: `config/workflow.example.toml`
- Modify: `tests/test_config.py`
- Modify: `tests/test_assembly_stage.py`
- Modify: `tests/test_stage_graph.py`

**Interfaces:**
- Produces: `VerifiedContainerConfig(image: Path, version: str, sha256: str)`
- `WorkflowConfig` adds `pydeface: VerifiedContainerConfig`
- `convert_subject(...)` passes the pinned image, version, and checksum to `network-fw2bids`
- `assemble_dataset(...)` returns defacing counts and receipt paths in `StageResult.details`

- [ ] **Step 1: Write failing strict-configuration tests**

```python
def test_loads_pinned_pydeface_container(tmp_path):
    config = WorkflowConfig.load(write_config(tmp_path, pydeface_sha256="a" * 64))
    assert config.pydeface.version == "2.1.0"
    assert config.pydeface.sha256 == "a" * 64


@pytest.mark.parametrize("digest", ["", "abc", "A" * 64, "g" * 64])
def test_rejects_invalid_pydeface_digest(tmp_path, digest):
    with pytest.raises(ValueError, match="pydeface.*sha256"):
        WorkflowConfig.load(write_config(tmp_path, pydeface_sha256=digest))
```

- [ ] **Step 2: Run config tests and verify RED**

Run: `PYTHONPATH=src python3 -m pytest tests/test_config.py -q`

Expected: FAIL because `WorkflowConfig` has no PyDeface configuration.

- [ ] **Step 3: Implement the verified container configuration**

Require an absolute, non-symlinked image path, nonempty version, and exactly 64 lowercase hexadecimal checksum characters. Add the approved PyDeface 2.1.0/FSL 6.0.7.18 example section to `workflow.example.toml`; leave the image checksum as a syntactically valid example and instruct operators to replace it with `sha256sum` output.

- [ ] **Step 4: Write failing worker-command and milestone tests**

```python
def test_conversion_passes_pinned_defacing_without_sensitive_paths(config, runner, monkeypatch):
    monkeypatch.setenv("FLYWHEEL_API_TOKEN", "secret")
    convert_subject(config, "s03", runner)
    command = runner.calls[0]
    assert option(command, "--pydeface-image") == str(config.pydeface.image)
    assert option(command, "--pydeface-version") == "2.1.0"
    assert option(command, "--pydeface-sha256") == "a" * 64
    assert "secret" not in " ".join(command)
    assert "SLURM_TMPDIR" not in " ".join(command)


def test_assembled_result_records_verified_defacing(config, runner):
    write_verified_parts(config.paths.parts_dir, config.subjects)
    result = assemble_dataset(config, runner)
    assert result.details["defacing"]["subjects"] == 46
    assert result.details["defacing"]["T1w"] == 46
```

- [ ] **Step 5: Run worker tests and verify RED**

Run: `PYTHONPATH=src python3 -m pytest tests/test_assembly_stage.py tests/test_stage_graph.py -q`

Expected: FAIL because commands and milestone details do not carry defacing provenance.

- [ ] **Step 6: Implement command propagation and provenance**

Add the three PyDeface flags to `convert_subject`. After assembly, load the copied receipts from the final dataset, verify all configured subjects are represented, count T1w/T2w records, and put only relative receipt paths and counts in `StageResult.details`. Add PyDeface image/version/checksum to `_stage_versions`; never record `$SLURM_TMPDIR`.

- [ ] **Step 7: Run focused tests and commit**

Run: `PYTHONPATH=src python3 -m pytest tests/test_config.py tests/test_assembly_stage.py tests/test_stage_graph.py -q`

Expected: PASS.

```bash
git add src/network_fmri/config.py src/network_fmri/models.py \
  src/network_fmri/stages/assembly.py src/network_fmri/pipeline.py \
  config/workflow.example.toml tests/test_config.py \
  tests/test_assembly_stage.py tests/test_stage_graph.py
git commit -m "feat: require pinned anatomical defacing"
```

### Task 5: End-to-End Privacy Acceptance and Operational Documentation

**Repositories:** `network_fw2bids`, `network_fmri`

**Files:**
- Modify: `network_fw2bids/README.md`
- Modify: `network_fw2bids/pyproject.toml`
- Modify: `network_fw2bids/uv.lock`
- Modify: `network_fmri/README.md`
- Modify: `network_fmri/docs/SHERLOCK.md`
- Modify: `network_fmri/pyproject.toml`
- Modify: `network_fmri/uv.lock`
- Modify: `network_fmri/tests/test_pipeline_e2e.py`
- Create: `network_fmri/tests/test_defacing_privacy_e2e.py`

**Interfaces:**
- Consumes: all Tasks 1–4 interfaces
- Produces: a pinned `network_fw2bids` revision and a synthetic privacy-boundary acceptance test

- [ ] **Step 1: Write a failing full-flow privacy test**

```python
def test_persistent_pipeline_never_contains_undefaced_anatomy(tmp_path, monkeypatch):
    runtime = synthetic_runtime(tmp_path, anatomy=True)
    monkeypatch.setenv("SLURM_TMPDIR", str(runtime.node_tmp))
    run_conversion_and_assembly(runtime)
    assert runtime.published_anatomy_is_defaced()
    assert runtime.receipts_match_all_anatomy()
    assert not runtime.node_tmp_contains_sensitive_files()
    assert not runtime.persistent_tree_contains_undefaced_fixture_hash()
    assert runtime.milestone("bids-assembled")["versions"]["pydeface"]["version"] == "2.1.0"
```

The test uses real small NiBabel files, real receipt/checksum validation, real safe-copy publication, and real assembly. Replace only Flywheel download, dcm2niix, Apptainer/PyDeface execution, and DataLad subprocesses with specific fakes.

- [ ] **Step 2: Run the privacy test and verify RED**

Run: `PYTHONPATH=src python3 -m pytest tests/test_defacing_privacy_e2e.py -q`

Expected: FAIL until the two package revisions and synthetic runner are integrated.

- [ ] **Step 3: Pin the reviewed `network_fw2bids` commit and refresh locks**

From the `network_fw2bids` implementation worktree, record the reviewed full SHA:

```bash
git rev-parse HEAD
```

Replace the existing `network_fw2bids` revision in `network_fmri/pyproject.toml` with that exact 40-character SHA. Do not use a branch name. Run `uv lock` in both repositories using a writable task-specific cache directory.

- [ ] **Step 4: Document the operational privacy check**

Document:

- the required PyDeface/FSL Apptainer image and checksum command;
- why `dcm2niix -ba y` is necessary but insufficient for facial removal;
- the `$SLURM_TMPDIR` containment rule;
- the absence of original anatomy from DataLad and `sourcedata`;
- the one-subject pilot procedure;
- receipt inspection commands; and
- mandatory visual review of pilot T1w and T2w images before the 46-subject run.

- [ ] **Step 5: Run both full suites, locks, builds, and source checks**

Run in `network_fw2bids`:

```bash
uv lock --check
uv sync --frozen
uv run --frozen pytest -q
uv build
git diff --check
```

Run in `network_fmri` on Linux x86_64:

```bash
uv lock --check
uv sync --frozen
uv run --frozen pytest -q
uv build
git diff --check
```

Also search both source trees for persistent sensitive staging, an unpinned PyDeface invocation, credentials in commands, and `datalad run`. Any match must be explained or removed before completion.

- [ ] **Step 6: Commit the integrated acceptance surface**

In `network_fw2bids`:

```bash
git add README.md pyproject.toml uv.lock
git commit -m "docs: document defaced conversion boundary"
```

In `network_fmri`:

```bash
git add README.md docs/SHERLOCK.md pyproject.toml uv.lock \
  tests/test_pipeline_e2e.py tests/test_defacing_privacy_e2e.py
git commit -m "test: verify anatomical privacy boundary"
```

## Completion Criteria

- Every reviewed test passes in both repositories.
- A conversion cannot execute without `$SLURM_TMPDIR` and a verified PyDeface image.
- No persistent path ever receives an undefaced anatomical NIfTI.
- Every persistent T1w/T2w has a matching checksum-verified receipt and `Defaced: true` sidecar.
- Assembly rejects all legacy or incomplete subject parts.
- The `bids-assembled` DataLad milestone records PyDeface provenance without sensitive paths.
- A one-subject Sherlock pilot passes automated checks and manual visual defacing review before the full sample is submitted.
