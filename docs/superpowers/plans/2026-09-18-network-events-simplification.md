# Network Events Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `network_events` deterministically audit and convert a canonical one-file-per-BOLD behavioral tree without runtime pairing heuristics or silent empty event files.

**Architecture:** Exact BIDS identities are parsed into immutable value objects. An audit compares canonical `_beh.csv` files, logical BOLD acquisitions, and reviewed exceptions; conversion only receives audited exact pairs and emits structured success or failure evidence.

**Tech Stack:** Python 3.11+, argparse, dataclasses, pathlib, csv/json, pandas, nibabel, pytest, uv

**Spec:** `network_fmri/docs/superpowers/specs/2026-09-18-simplified-bids-pipeline-design.md`

## Global Constraints

- Canonical behavior lives at `<bids>/sourcedata/behavioral/sub-*/ses-*/beh/*_beh.csv`.
- Runtime code must never infer or remap behavioral identities.
- Every non-rest logical BOLD has exactly one behavior file or one reviewed exception.
- Multi-echo BOLD files represent one logical acquisition.
- Conversion failures produce no `_events.tsv` and must be recorded explicitly.
- Seven discarded volumes and the sidecar TR determine the onset shift.
- `network_events` records evidence and never makes exclusion decisions.

---

### Task 1: Exact Behavioral/BOLD Identity Audit

**Files:**
- Create: `src/network_events/identity.py`
- Create: `tests/test_identity.py`
- Modify: `src/network_events/__init__.py`

**Interfaces:**
- Produces: `RunIdentity(subject: str, session: str, task: str, run: str)`
- Produces: `BehaviorException(identity: RunIdentity, reason: str, detail: str)`
- Produces: `AuditResult(pairs: tuple[tuple[RunIdentity, Path], ...], exceptions: tuple[BehaviorException, ...], errors: tuple[str, ...])`
- Produces: `audit_dataset(bids_dir: Path, behavioral_dir: Path) -> AuditResult`

- [ ] **Step 1: Write failing identity and invariant tests**

```python
def test_audit_groups_three_echoes_as_one_logical_bold(tmp_path):
    bids, behavior = dataset_with_bold(tmp_path, task="nBack", echoes=(1, 2, 3))
    write_behavior(behavior, "sub-s01_ses-01_task-nBack_run-1_beh.csv")
    result = audit_dataset(bids, behavior)
    assert len(result.pairs) == 1
    assert result.errors == ()

def test_audit_requires_behavior_or_reviewed_exception(tmp_path):
    bids, behavior = dataset_with_bold(tmp_path, task="nBack", echoes=(1, 2, 3))
    result = audit_dataset(bids, behavior)
    assert result.errors == (
        "sub-s01/ses-01/task-nBack/run-1: missing behavior and exception",
    )

def test_rest_requires_no_behavior(tmp_path):
    bids, behavior = dataset_with_bold(tmp_path, task="rest", echoes=(1, 2, 3))
    assert audit_dataset(bids, behavior).errors == ()
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `uv run --frozen pytest tests/test_identity.py -q`

Expected: FAIL because `network_events.identity` does not exist.

- [ ] **Step 3: Implement strict identity parsing and auditing**

```python
@dataclass(frozen=True, order=True)
class RunIdentity:
    subject: str
    session: str
    task: str
    run: str

@dataclass(frozen=True)
class AuditResult:
    pairs: tuple[tuple[RunIdentity, Path], ...]
    exceptions: tuple[BehaviorException, ...]
    errors: tuple[str, ...]

def audit_dataset(bids_dir: Path, behavioral_dir: Path) -> AuditResult:
    """Require one canonical behavior file or reviewed exception per non-rest BOLD."""
```

Use anchored regexes for `_beh.csv` and `_bold.nii[.gz]`; reject unparseable CSVs,
duplicate behavior identities, orphan behavior files, malformed exception rows, duplicate
exceptions, and identities having both a file and an exception. Require exception columns
`subject`, `session`, `task`, `run`, `reason`, `detail`, `reviewed_by`, and `reviewed_at`.

- [ ] **Step 4: Run identity tests**

Run: `uv run --frozen pytest tests/test_identity.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the audited identity contract**

```bash
git add src/network_events/identity.py src/network_events/__init__.py tests/test_identity.py
git commit -m "feat: audit canonical behavioral identities"
```

### Task 2: Structured Event Conversion Results

**Files:**
- Modify: `src/network_events/create.py`
- Create: `tests/test_conversion_results.py`
- Modify: `tests/test_create.py`
- Modify: `tests/test_scanlength.py`

**Interfaces:**
- Consumes: `RunIdentity`, audited `(RunIdentity, Path)` pairs
- Produces: `EventResult(identity: RunIdentity, status: Literal["created", "failed"], behavior_file: Path, events_file: Path | None, qc_file: Path | None, error: str | None)`
- Produces: `create_events(bids_dir: Path, pairs: Iterable[tuple[RunIdentity, Path]]) -> tuple[EventResult, ...]`
- Produces: `<bids>/sourcedata/events_qc/conversion_errors.tsv`

- [ ] **Step 1: Write failing success and failure tests**

```python
def test_conversion_failure_writes_evidence_not_empty_events(tmp_path):
    bids, pair = invalid_behavior_pair(tmp_path)
    results = create_events(bids, [pair])
    assert results[0].status == "failed"
    assert results[0].events_file is None
    assert not expected_events_path(bids, pair[0]).exists()
    rows = read_tsv(bids / "sourcedata/events_qc/conversion_errors.tsv")
    assert rows[0]["subject"] == "sub-s01"

def test_success_is_atomic_and_writes_truncation_evidence(tmp_path):
    bids, pair = valid_behavior_pair(tmp_path)
    result, = create_events(bids, [pair])
    assert result.status == "created"
    assert result.events_file.is_file()
    assert result.qc_file.is_file()
```

- [ ] **Step 2: Run focused tests and confirm the old empty-file behavior fails**

Run: `uv run --frozen pytest tests/test_conversion_results.py -q`

Expected: FAIL because `create_events` and `EventResult` do not exist.

- [ ] **Step 3: Implement audited conversion and atomic output**

```python
@dataclass(frozen=True)
class EventResult:
    identity: RunIdentity
    status: Literal["created", "failed"]
    behavior_file: Path
    events_file: Path | None
    qc_file: Path | None
    error: str | None

def create_events(
    bids_dir: Path,
    pairs: Iterable[tuple[RunIdentity, Path]],
) -> tuple[EventResult, ...]:
    """Convert exact audited pairs and account for every pair in the returned results."""
```

Write TSV and JSON files to siblings with `.tmp` suffixes, flush them, then use
`Path.replace`. Remove stale events/QC output after a failed reconversion. Write the error
table once after processing all pairs, including identity, source path, exception class,
and message.

- [ ] **Step 4: Run conversion and existing timing tests**

Run: `uv run --frozen pytest tests/test_conversion_results.py tests/test_create.py tests/test_scanlength.py tests/test_nonmonotonic.py -q`

Expected: PASS.

- [ ] **Step 5: Commit structured conversion**

```bash
git add src/network_events/create.py tests/test_conversion_results.py tests/test_create.py tests/test_scanlength.py
git commit -m "feat: report event conversion failures explicitly"
```

### Task 3: Minimal Public CLI

**Files:**
- Modify: `src/network_events/cli.py`
- Delete: `src/network_events/run.py`
- Delete: `src/network_events/migrate.py`
- Delete: `src/network_events/qc_globals.py`
- Modify: `tests/test_cli.py`
- Delete: `tests/test_migrate.py`
- Delete: `tests/test_run.py`

**Interfaces:**
- Produces: `network-events audit --bids-dir PATH --behavioral-dir PATH [--json PATH]`
- Produces: `network-events create --bids-dir PATH --behavioral-dir PATH`
- Exit code: audit `0` only when `AuditResult.errors` is empty; create `0` when every pair is accounted for, including explicit conversion failures, and `2` when identity audit fails

- [ ] **Step 1: Replace CLI tests with the two-command contract**

```python
def test_audit_returns_two_for_identity_errors(tmp_path):
    assert main(["audit", "--bids-dir", str(tmp_path),
                 "--behavioral-dir", str(tmp_path / "behavioral")]) == 2

def test_create_refuses_unaudited_identity_errors(tmp_path):
    assert main(["create", "--bids-dir", str(tmp_path),
                 "--behavioral-dir", str(tmp_path / "behavioral")]) == 2
```

- [ ] **Step 2: Run the CLI tests and confirm failure**

Run: `uv run --frozen pytest tests/test_cli.py -q`

Expected: FAIL because the current CLI exposes migration and orchestration commands.

- [ ] **Step 3: Implement `audit` and `create`, then remove migration code**

```python
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)
```

Both commands print a JSON summary to stdout. `audit --json` writes the same payload
atomically. `create` calls `audit_dataset`, refuses any identity errors, invokes
`create_events`, and reports created and failed counts.

- [ ] **Step 4: Run the complete package suite**

Run: `uv run --frozen pytest -q`

Expected: PASS with no references to removed commands.

- [ ] **Step 5: Commit the minimal CLI**

```bash
git add -A src/network_events tests
git commit -m "refactor: reduce network-events to audit and create"
```

### Task 4: Document and Release the Contract

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: installable `network_events` release commit for `network_fmri` to pin

- [ ] **Step 1: Add a README verification test**

```python
def test_readme_documents_only_public_commands():
    text = Path("README.md").read_text()
    assert "network-events audit" in text
    assert "network-events create" in text
    assert "migrate-archive" not in text
```

- [ ] **Step 2: Run the documentation test and confirm failure**

Run: `uv run --frozen pytest tests/test_cli.py::test_readme_documents_only_public_commands -q`

Expected: FAIL against the old README.

- [ ] **Step 3: Rewrite README and bump version to `0.2.0`**

Document canonical layout, exception schema, exact matching, timing transformations,
structured conversion errors, command examples, and the division from `network_qa`.
Run `uv lock` after the version change.

- [ ] **Step 4: Verify build and full tests**

Run: `uv run --frozen pytest -q && uv build`

Expected: tests PASS and wheel/sdist build successfully.

- [ ] **Step 5: Commit the releasable package**

```bash
git add README.md pyproject.toml uv.lock tests/test_cli.py
git commit -m "docs: publish canonical event conversion contract"
```
