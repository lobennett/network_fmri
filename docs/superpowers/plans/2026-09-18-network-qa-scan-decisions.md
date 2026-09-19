# Network QA Scan Decisions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate and validate the complete, review-gated `scan_decisions.tsv` that controls BIDS curation before fMRIPrep.

**Architecture:** Focused inventory modules produce typed evidence for logical acquisitions. A compiler merges echo/TR, MRIQC motion, anatomical, and behavioral evidence into one deterministic manifest; a separate approval validator guarantees that only current, fully reviewed decisions can drive curation.

**Tech Stack:** Python 3.11+, dataclasses, csv/json, pathlib, hashlib, nibabel, pytest, uv

**Spec:** `network_fmri/docs/superpowers/specs/2026-09-18-simplified-bids-pipeline-design.md`

## Global Constraints

- Every observed logical acquisition receives one row; missing T1w/T2w use synthetic rows.
- Echoes 1, 2, and 3 are expected for multi-echo BOLD.
- Echo 2 supplies motion evidence; there is no silent fallback.
- Rest flags at `fd_mean >= 0.2` mm.
- Task flags at `fd_mean >= 0.2` mm or `fd_perc >= 20%`, with MRIQC `fd_thres = 0.5` mm.
- `dvars_std` is evidence only.
- Short scans flag when original volumes are strictly less than 50% of the mean for the same BIDS task.
- Any T1w or T2w count other than exactly one per subject requires review.
- Recommendations never approve decisions.

---

### Task 1: Manifest Schema and Stable Acquisition Identities

**Files:**
- Create: `src/network_qa/manifest.py`
- Create: `tests/test_manifest.py`
- Replace: `src/network_qa/decisions.py`

**Interfaces:**
- Produces: `AcquisitionKey(record_type, subject, session, datatype, suffix, task, acquisition, direction, run)`
- Produces: `DecisionRow` with the columns specified by the design
- Produces: `write_manifest(path: Path, rows: Iterable[DecisionRow]) -> str` returning SHA-256
- Produces: `read_manifest(path: Path) -> tuple[DecisionRow, ...]`

- [ ] **Step 1: Write failing round-trip and uniqueness tests**

```python
def test_manifest_round_trip_preserves_blank_bids_entities(tmp_path):
    row = DecisionRow.clean(anatomical_key("sub-s01", "ses-01", "T1w"))
    digest = write_manifest(tmp_path / "scan_decisions.tsv", [row])
    assert read_manifest(tmp_path / "scan_decisions.tsv") == (row,)
    assert len(digest) == 64

def test_manifest_rejects_duplicate_keys(tmp_path):
    row = DecisionRow.clean(functional_key("sub-s01", "ses-01", "nBack", "1"))
    with pytest.raises(ValueError, match="duplicate acquisition"):
        write_manifest(tmp_path / "scan_decisions.tsv", [row, row])
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `uv run --frozen pytest tests/test_manifest.py -q`

Expected: FAIL because the manifest types do not exist.

- [ ] **Step 3: Implement the typed TSV contract**

```python
@dataclass(frozen=True, order=True)
class AcquisitionKey:
    record_type: Literal["acquisition", "missing_expected"]
    subject: str
    session: str
    datatype: str
    suffix: str
    task: str = ""
    acquisition: str = ""
    direction: str = ""
    run: str = ""

@dataclass(frozen=True)
class DecisionRow:
    key: AcquisitionKey
    flags: tuple[str, ...]
    decision: Literal["keep", "drop", "review"]
    approval_required: bool
    approved: bool
    reason_code: str
    reason_detail: str
```

Add explicit fields for echo sets, volume counts, MRIQC values/report, behavioral/event
status, recommendation/status/rationale, reviewer, and reviewed time. Serialize tuple
fields as comma-separated stable values and booleans as `yes`/`no`.

- [ ] **Step 4: Run schema tests**

Run: `uv run --frozen pytest tests/test_manifest.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the manifest contract**

```bash
git add src/network_qa/manifest.py src/network_qa/decisions.py tests/test_manifest.py
git commit -m "feat: define scan decisions manifest"
```

### Task 2: Functional Echo and Volume Evidence

**Files:**
- Create: `src/network_qa/functional.py`
- Create: `tests/test_functional.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: `FunctionalEvidence(key, expected_echoes, observed_echoes, missing_echoes, representative_echo, tr_count, original_tr_count, expected_tr_count_mean, tr_count_fraction, flags)`
- Produces: `inspect_functionals(bids_dir: Path) -> tuple[FunctionalEvidence, ...]`

- [ ] **Step 1: Write failing boundary and grouping tests**

```python
def test_missing_any_expected_echo_flags_review(tmp_path):
    write_bold_group(tmp_path, task="nBack", echoes={1: 100, 3: 100})
    evidence, = inspect_functionals(tmp_path)
    assert evidence.missing_echoes == (2,)
    assert "missing_echo" in evidence.flags

def test_short_scan_boundary_is_strict(tmp_path):
    write_task_counts(tmp_path, "nBack", [50, 100, 100])
    rows = inspect_functionals(tmp_path)
    assert "short_scan" not in rows[0].flags
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run --frozen pytest tests/test_functional.py -q`

Expected: FAIL because `inspect_functionals` does not exist.

- [ ] **Step 3: Implement logical grouping and same-task means**

```python
EXPECTED_ECHOES = (1, 2, 3)
N_DUMMY = 7

def inspect_functionals(bids_dir: Path) -> tuple[FunctionalEvidence, ...]:
    """Group echoes, read NIfTI dim4, and calculate task-level expected lengths."""
```

Add `nibabel` as a direct dependency. Use echo 2 when present, the only image for
single-echo data, or the agreed count when observed echoes match. Flag unequal counts and
omit those rows from task means. Include countable short scans in their task mean.

- [ ] **Step 4: Run functional tests and lock dependencies**

Run: `uv lock && uv run --frozen pytest tests/test_functional.py -q`

Expected: PASS.

- [ ] **Step 5: Commit functional evidence**

```bash
git add src/network_qa/functional.py tests/test_functional.py pyproject.toml uv.lock
git commit -m "feat: inspect echo completeness and scan length"
```

### Task 3: MRIQC Motion Evidence

**Files:**
- Replace: `src/network_qa/exclusions/motion.py`
- Modify: `tests/exclusions/test_motion.py`

**Interfaces:**
- Consumes: `FunctionalEvidence`
- Produces: `MotionEvidence(key, fd_mean, fd_perc, dvars_std, fd_thres, report_path, flags)`
- Produces: `inspect_motion(functionals, mriqc_dir: Path) -> tuple[MotionEvidence, ...]`

- [ ] **Step 1: Write exact inclusive-threshold tests**

```python
@pytest.mark.parametrize("task,fd_mean,fd_perc,flagged", [
    ("rest", 0.2, 0.0, True),
    ("nBack", 0.2, 0.0, True),
    ("nBack", 0.1, 20.0, True),
    ("nBack", 0.199, 19.9, False),
])
def test_motion_boundaries(task, fd_mean, fd_perc, flagged, tmp_path):
    evidence = inspect_fixture(task, fd_mean, fd_perc, tmp_path)
    assert ("excessive_motion" in evidence.flags) is flagged
```

- [ ] **Step 2: Run tests and confirm old echo-1/strict-threshold logic fails**

Run: `uv run --frozen pytest tests/exclusions/test_motion.py -q`

Expected: FAIL on echo 2 and inclusive boundary assertions.

- [ ] **Step 3: Implement echo-2 motion inspection**

```python
FD_MEAN_THRESHOLD = 0.2
FD_PERCENT_THRESHOLD = 20.0
EXPECTED_FD_THRES = 0.5

def inspect_motion(
    functionals: Iterable[FunctionalEvidence], mriqc_dir: Path
) -> tuple[MotionEvidence, ...]:
    """Read echo-2 IQMs, validate fd_thres, and retain dvars_std as evidence."""
```

Missing echo-2 IQMs and mismatched `fd_thres` add review flags. Never apply a DVARS
threshold.

- [ ] **Step 4: Run motion tests**

Run: `uv run --frozen pytest tests/exclusions/test_motion.py -q`

Expected: PASS.

- [ ] **Step 5: Commit motion evidence**

```bash
git add src/network_qa/exclusions/motion.py tests/exclusions/test_motion.py
git commit -m "feat: apply approved MRIQC motion flags"
```

### Task 4: Anatomical Counts and Recommendations

**Files:**
- Create: `src/network_qa/anatomical.py`
- Create: `tests/test_anatomical.py`

**Interfaces:**
- Produces: `AnatomicalEvidence(key, metrics, report_path, flags, recommendation, recommendation_status, recommendation_rationale)`
- Produces: `inspect_anatomicals(bids_dir: Path, mriqc_dir: Path, subjects: Iterable[str]) -> tuple[AnatomicalEvidence, ...]`

- [ ] **Step 1: Write failing count and ranking tests**

```python
@pytest.mark.parametrize("suffix,count", [("T1w", 0), ("T1w", 2), ("T2w", 0), ("T2w", 2)])
def test_any_count_other_than_one_requires_review(suffix, count, tmp_path):
    rows = anatomical_fixture(tmp_path, suffix=suffix, count=count)
    assert any("anatomical_count" in row.flags for row in rows)

def test_primary_metrics_agree_on_recommendation(tmp_path):
    rows = duplicate_fixture(tmp_path, cjv=(0.4, 0.7), cnr=(3.0, 2.0))
    assert {row.recommendation for row in rows} == {"keep-first"}
    assert all(row.recommendation_status == "clear" for row in rows)
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `uv run --frozen pytest tests/test_anatomical.py -q`

Expected: FAIL because anatomical inspection is absent.

- [ ] **Step 3: Implement counts and documented ranking**

```python
SECONDARY_DIRECTIONS = {
    "snr": "higher", "efc": "lower", "fber": "higher", "qi_2": "lower"
}

def inspect_anatomicals(
    bids_dir: Path, mriqc_dir: Path, subjects: Iterable[str]
) -> tuple[AnatomicalEvidence, ...]:
    """Flag missing/duplicate anatomy and recommend among duplicates without approval."""
```

Generate `missing_expected` records for zero counts. Ignore FBER `-1`; rank WM2MAX by
distance to `[0.6, 0.8]` (zero inside the interval). Use secondary majority only when CJV
and CNR disagree. Emit `indeterminate` for ties or insufficient data.

- [ ] **Step 4: Run anatomical tests**

Run: `uv run --frozen pytest tests/test_anatomical.py -q`

Expected: PASS.

- [ ] **Step 5: Commit anatomical evidence**

```bash
git add src/network_qa/anatomical.py tests/test_anatomical.py
git commit -m "feat: recommend anatomical scan selections"
```

### Task 5: Behavioral Evidence and Manifest Compilation

**Files:**
- Replace: `src/network_qa/exclusions/behavioral.py`
- Create: `src/network_qa/compiler.py`
- Create: `tests/test_compiler.py`
- Modify: `src/network_qa/cli.py`
- Modify: `tests/exclusions/test_behavioral.py`
- Modify: `tests/exclusions/test_cli.py`

**Interfaces:**
- Produces: `compile_decisions(bids_dir: Path, mriqc_dir: Path, output: Path) -> Path`
- CLI: `network-qa decisions generate --bids-dir PATH --mriqc-dir PATH --output PATH`

- [ ] **Step 1: Write failing merge and missing-evidence tests**

```python
def test_known_behavior_exception_is_evidence_not_drop(tmp_path):
    row = compile_fixture(tmp_path, behavior_status="reviewed_exception")
    assert row.decision == "keep"
    assert row.behavioral_status == "reviewed_exception"

def test_conversion_error_requires_review(tmp_path):
    row = compile_fixture(tmp_path, event_status="failed")
    assert row.decision == "review"
    assert "event_conversion_failed" in row.flags
```

- [ ] **Step 2: Run compiler tests and confirm failure**

Run: `uv run --frozen pytest tests/test_compiler.py tests/exclusions/test_behavioral.py -q`

Expected: FAIL because the compiler does not exist and missing QC currently means zero loss.

- [ ] **Step 3: Implement evidence merge and metadata sidecar**

```python
def compile_decisions(bids_dir: Path, mriqc_dir: Path, output: Path) -> Path:
    functionals = inspect_functionals(bids_dir)
    motion = inspect_motion(functionals, mriqc_dir)
    anatomicals = inspect_anatomicals(bids_dir, mriqc_dir, dataset_subjects(bids_dir))
    rows = merge_evidence(functionals, motion, anatomicals, behavioral_evidence(bids_dir))
    write_manifest(output, rows)
    write_metadata(output.with_suffix(".meta.json"), rows, bids_dir, mriqc_dir)
    return output
```

Missing/unreadable truncation evidence is `unknown` and reviewable. Preserve both
non-monotonic and scan-length trial-loss metrics without adding a new automatic drop rule.

- [ ] **Step 4: Run compiler and CLI tests**

Run: `uv run --frozen pytest tests/test_compiler.py tests/exclusions/test_behavioral.py tests/exclusions/test_cli.py -q`

Expected: PASS.

- [ ] **Step 5: Commit decision generation**

```bash
git add src/network_qa tests
git commit -m "feat: compile scan review manifest"
```

### Task 6: Approval Gate and Release

**Files:**
- Create: `src/network_qa/approval.py`
- Create: `tests/test_approval.py`
- Modify: `src/network_qa/cli.py`
- Modify: `README.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: `seal_approval(manifest: Path, metadata: Path, bids_dir: Path) -> ApprovalResult`
- Produces: `validate_approval(manifest: Path, metadata: Path, bids_dir: Path) -> ApprovalResult`
- CLI: `network-qa decisions approve --manifest PATH --metadata PATH --bids-dir PATH`
- CLI: `network-qa decisions validate --manifest PATH --metadata PATH --bids-dir PATH`

- [ ] **Step 1: Write failing gate tests**

```python
def test_review_row_blocks_approval(tmp_path):
    result = validate_fixture(tmp_path, decision="review", approved="no")
    assert result.ok is False
    assert "unresolved review" in result.errors[0]

def test_changed_manifest_invalidates_checksum(tmp_path):
    manifest, meta, bids = approved_fixture(tmp_path)
    manifest.write_text(manifest.read_text().replace("keep", "drop", 1))
    assert validate_approval(manifest, meta, bids).ok is False

def test_seal_records_approved_manifest_checksum(tmp_path):
    manifest, meta, bids = resolved_fixture(tmp_path)
    result = seal_approval(manifest, meta, bids)
    assert result.ok is True
    assert json.loads(meta.read_text())["approved_manifest_sha256"] == result.manifest_sha256
```

- [ ] **Step 2: Run gate tests and confirm failure**

Run: `uv run --frozen pytest tests/test_approval.py -q`

Expected: FAIL because approval validation is absent.

- [ ] **Step 3: Implement approval validation and CLI exit codes**

```python
@dataclass(frozen=True)
class ApprovalResult:
    ok: bool
    errors: tuple[str, ...]
    manifest_sha256: str

def validate_approval(manifest: Path, metadata: Path, bids_dir: Path) -> ApprovalResult:
    """Reject unresolved, unexplained, stale, or inventory-mismatched decisions."""
```

Require approved flagged rows to have decision `keep` or `drop`, `approved=yes`, a valid
reason code for drops, nonempty `reason_detail`, reviewer, and timestamp. Recompute the
BIDS inventory and compare its hash and source commit metadata. `seal_approval` performs
those checks and atomically adds `approved_manifest_sha256` to the metadata sidecar;
`validate_approval` is read-only and requires the current TSV hash to equal that sealed
value. Any later TSV edit therefore invalidates approval.

- [ ] **Step 4: Run the complete suite and build**

Run: `uv lock && uv run --frozen pytest -q && uv build`

Expected: all tests PASS and distributions build.

- [ ] **Step 5: Document, bump to `0.2.0`, and commit**

```bash
git add src/network_qa tests README.md pyproject.toml uv.lock
git commit -m "feat: gate curation on approved scan decisions"
```
