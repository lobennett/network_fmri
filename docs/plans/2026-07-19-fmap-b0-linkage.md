# fmap_link Stage — B0 Field-Map ↔ BOLD Linkage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a re-executable `fmap_link` pipeline stage that stamps BIDS
`B0FieldIdentifier`/`B0FieldSource` metadata so fMRIPrep/SDCFlows applies
susceptibility distortion correction (SDC) to every BOLD run.

**Architecture:** A pure function `link_b0_fields(cohort_dir)` walks each
`sub-*/ses-*`, and where a session has both a Hz field map and ≥1 BOLD, stamps a
session-scoped identifier (`<sub-label>_<ses>`, e.g. `s1035_ses-01`) onto the
`_fieldmap`+`_magnitude` sidecars (`B0FieldIdentifier`) and every BOLD echo
sidecar (`B0FieldSource`). Exposed as `fw2bids fmap-link <dir>` and wired into
the DAG as a dedicated single-job stage between `events` and `datalad`, running
for all three cohorts. Sidecar writes detect each file's native indent, append
the key, and write atomically → clean one-line diffs, idempotent, byte-deterministic.

**Tech Stack:** Python 3.11, stdlib `json`/`pathlib`/`dataclasses`, pytest;
network_fmri submit/template/CLI conventions (mirrors the existing `datalad` stage).

**Spec:** `docs/specs/2026-07-19-fmap-b0-linkage-design.md`

**Branch:** `feat/fmap-b0-linkage` (already created; spec committed at `b40905c`).

---

## File Structure

- **Create** `src/network_fmri/b0link.py` — `LinkSummary` dataclass, `_detect_indent`,
  `_set_sidecar_key`, `link_b0_fields`. One responsibility: the linkage logic.
- **Create** `tests/test_b0link.py` — unit tests on synthetic mini-trees.
- **Modify** `src/network_fmri/run.py` — add `_fmap_link_main` + dispatch on
  `argv[0] == "fmap-link"`.
- **Modify** `tests/test_run_cli.py` — dispatch test for `fmap-link`.
- **Create** `src/network_fmri/submit/fmap_link.py` — submit module (single job).
- **Create** `src/network_fmri/submit/templates/fmap_link.sbatch.tmpl` — sbatch template.
- **Modify** `src/network_fmri/submit/__init__.py` — add `fmap_link` to `_ROUTE_NAMES`.
- **Modify** `src/network_fmri/submit/pipeline.py` — import `fmap_link`; insert into
  `_STAGES` after `events`, before `datalad`.
- **Modify** `tests/test_submit.py` — fmap_link render test; update the two
  pipeline-order assertions to include `nf-fmap_link-<cohort>`.

Naming note: the Python module/stage is `fmap_link` (underscore — used for the
template file `fmap_link.sbatch.tmpl`, `STAGE="fmap_link"`, job name
`nf-fmap_link-<cohort>`, and `fw2bids submit fmap_link`). The user-facing payload
verb is `fw2bids fmap-link` (hyphen), matching the readable one-word `fw2bids`
verbs. These live in separate namespaces; keep them distinct.

---

## Task 1: Core module — `LinkSummary`, indent detection, sidecar writer

**Files:**
- Create: `src/network_fmri/b0link.py`
- Test: `tests/test_b0link.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for network_fmri.b0link — session-scoped B0Field* linkage."""

import json

from network_fmri.b0link import LinkSummary, _detect_indent, _set_sidecar_key


def test_detect_indent_two_space():
    text = '{\n  "A": 1,\n  "B": 2\n}\n'
    assert _detect_indent(text) == 2


def test_detect_indent_four_space():
    text = '{\n    "A": 1,\n    "B": {\n        "C": 3\n    }\n}\n'
    assert _detect_indent(text) == 4


def test_detect_indent_defaults_to_two_when_flat():
    assert _detect_indent('{}\n') == 2


def test_set_sidecar_key_appends_and_preserves_indent(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{\n  "A": 1,\n  "Z": 2\n}\n')
    wrote = _set_sidecar_key(p, "B0FieldSource", "s1_ses-01")
    assert wrote is True
    # key appended last, 2-space indent preserved, trailing newline
    assert p.read_text() == (
        '{\n  "A": 1,\n  "Z": 2,\n  "B0FieldSource": "s1_ses-01"\n}\n'
    )
    assert json.loads(p.read_text())["B0FieldSource"] == "s1_ses-01"


def test_set_sidecar_key_idempotent_noop(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{\n  "B0FieldSource": "s1_ses-01"\n}\n')
    before = p.read_text()
    wrote = _set_sidecar_key(p, "B0FieldSource", "s1_ses-01")
    assert wrote is False
    assert p.read_text() == before  # untouched


def test_set_sidecar_key_overwrites_different_value(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{\n  "B0FieldSource": "old"\n}\n')
    wrote = _set_sidecar_key(p, "B0FieldSource", "new")
    assert wrote is True
    assert json.loads(p.read_text())["B0FieldSource"] == "new"


def test_link_summary_defaults_zero():
    s = LinkSummary()
    assert (s.sessions_linked, s.bolds_stamped, s.no_fmap, s.orphan_fmap) == (0, 0, 0, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_b0link.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'network_fmri.b0link'`

- [ ] **Step 3: Write minimal implementation**

Create `src/network_fmri/b0link.py`:

```python
"""Link session-scoped B0 field maps to their BOLD runs via BIDS metadata.

Each session has exactly one Hz field map (`_fieldmap` + `_magnitude`,
`Units: Hz`). fMRIPrep/SDCFlows groups the field map, its magnitude, and the
BOLD runs it corrects by a shared ``B0FieldIdentifier``. This module stamps a
per-session identifier (``<sub-label>_<ses>``) onto the two fmap sidecars and a
matching ``B0FieldSource`` onto every BOLD echo sidecar in the session.

Sidecar writes preserve each file's native indent and append the key, so a diff
is exactly one added line per file. Writes are atomic (temp + rename) and a pure
function of the input → byte-identical across runs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class LinkSummary:
    """Counts returned by :func:`link_b0_fields` (printed + asserted by tests)."""

    sessions_linked: int = 0
    bolds_stamped: int = 0
    no_fmap: int = 0
    orphan_fmap: int = 0


def _detect_indent(text: str) -> int:
    """Leading-space width of the first indented line; default 2 if none."""
    for line in text.splitlines():
        stripped = line.lstrip(" ")
        if stripped and stripped != line:
            return len(line) - len(stripped)
    return 2


def _set_sidecar_key(sidecar: Path, key: str, value: str) -> bool:
    """Set ``sidecar[key] = value`` (append), preserving native indent.

    No-op returning ``False`` if the key already equals ``value``. Overwrites a
    differing value. Atomic temp-file + rename; trailing newline. Returns
    ``True`` when the file was written.
    """
    text = sidecar.read_text()
    data = json.loads(text)
    if data.get(key) == value:
        return False
    indent = _detect_indent(text)
    data[key] = value
    tmp = sidecar.with_name(sidecar.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=indent, ensure_ascii=False) + "\n")
    tmp.rename(sidecar)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_b0link.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/network_fmri/b0link.py tests/test_b0link.py
git commit -m "feat(b0link): LinkSummary + indent-preserving atomic sidecar writer

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `link_b0_fields` — walk sessions and stamp identifiers

**Files:**
- Modify: `src/network_fmri/b0link.py`
- Test: `tests/test_b0link.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_b0link.py`:

```python
import pytest

from network_fmri.b0link import link_b0_fields


def _sidecar(path, obj=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj or {"RepetitionTime": 1.0}, indent=2) + "\n")


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def _make_session(root, sub, ses, *, fmap=True, tasks=("flanker", "rest"), echoes=(1, 2, 3)):
    """Build a minimal BIDS-ish session: optional fmap + multi-echo BOLD per task."""
    base = root / f"sub-{sub}" / f"ses-{ses}"
    if fmap:
        for suffix in ("fieldmap", "magnitude"):
            stem = f"sub-{sub}_ses-{ses}_run-1_{suffix}"
            _touch(base / "fmap" / f"{stem}.nii.gz")
            _sidecar(base / "fmap" / f"{stem}.json", {"Units": "Hz"})
    for task in tasks:
        for echo in echoes:
            stem = f"sub-{sub}_ses-{ses}_task-{task}_run-1_echo-{echo}_bold"
            _touch(base / "func" / f"{stem}.nii.gz")
            _sidecar(base / "func" / f"{stem}.json")
    return base


def test_happy_path_stamps_fmap_magnitude_and_all_bolds(tmp_path):
    base = _make_session(tmp_path, "s1035", "01", tasks=("flanker", "nBack", "rest"))
    summary = link_b0_fields(tmp_path)

    ident = "s1035_ses-01"
    for suffix in ("fieldmap", "magnitude"):
        j = json.loads((base / "fmap" / f"sub-s1035_ses-01_run-1_{suffix}.json").read_text())
        assert j["B0FieldIdentifier"] == ident
    bolds = sorted((base / "func").glob("*_bold.json"))
    assert len(bolds) == 9  # 3 tasks x 3 echoes
    for b in bolds:
        assert json.loads(b.read_text())["B0FieldSource"] == ident

    assert summary.sessions_linked == 1
    assert summary.bolds_stamped == 9
    assert summary.no_fmap == 0
    assert summary.orphan_fmap == 0


def test_no_fmap_session_leaves_bolds_untouched(tmp_path):
    base = _make_session(tmp_path, "s1258", "06", fmap=False)
    summary = link_b0_fields(tmp_path)
    for b in (base / "func").glob("*_bold.json"):
        assert "B0FieldSource" not in json.loads(b.read_text())
    assert summary.no_fmap == 1
    assert summary.sessions_linked == 0
    assert summary.bolds_stamped == 0


def test_orphan_fmap_not_stamped(tmp_path):
    base = _make_session(tmp_path, "s0", "01", tasks=())  # fmap, no bold
    summary = link_b0_fields(tmp_path)
    j = json.loads((base / "fmap" / "sub-s0_ses-01_run-1_fieldmap.json").read_text())
    assert "B0FieldIdentifier" not in j
    assert summary.orphan_fmap == 1
    assert summary.sessions_linked == 0


def test_idempotent_second_run_is_noop(tmp_path):
    _make_session(tmp_path, "s1035", "01")
    link_b0_fields(tmp_path)
    snapshot = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    summary2 = link_b0_fields(tmp_path)
    for p, content in snapshot.items():
        assert p.read_bytes() == content  # byte-identical, untouched
    assert summary2.bolds_stamped == 0  # nothing newly written


def test_deterministic_identical_trees_produce_identical_sidecars(tmp_path):
    a = tmp_path / "A"
    b = tmp_path / "B"
    _make_session(a, "s1035", "01")
    _make_session(b, "s1035", "01")
    link_b0_fields(a)
    link_b0_fields(b)
    for pa in a.rglob("*.json"):
        pb = b / pa.relative_to(a)
        assert pa.read_bytes() == pb.read_bytes()


def test_multi_fmap_raises(tmp_path):
    base = _make_session(tmp_path, "s1", "01")
    # a second field map in the same session → assert-never → raise
    _touch(base / "fmap" / "sub-s1_ses-01_run-2_fieldmap.nii.gz")
    _sidecar(base / "fmap" / "sub-s1_ses-01_run-2_fieldmap.json", {"Units": "Hz"})
    with pytest.raises(ValueError, match="multiple field maps"):
        link_b0_fields(tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_b0link.py -k "happy_path or no_fmap or orphan or idempotent or deterministic or multi_fmap" -v`
Expected: FAIL — `ImportError: cannot import name 'link_b0_fields'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/network_fmri/b0link.py`:

```python
def _identifier(sub_dir: Path, ses_dir: Path) -> str:
    """``<sub-label>_<ses>`` — subject dir sans ``sub-`` prefix + session dir.

    e.g. ``sub-s1035`` / ``ses-01`` -> ``s1035_ses-01``.
    """
    sub_label = sub_dir.name[len("sub-"):] if sub_dir.name.startswith("sub-") else sub_dir.name
    return f"{sub_label}_{ses_dir.name}"


def link_b0_fields(cohort_dir: Path) -> LinkSummary:
    """Stamp session-scoped B0Field* metadata across a staged BIDS cohort tree.

    For each ``sub-*/ses-*`` with both a field map and ≥1 BOLD: stamp
    ``B0FieldIdentifier`` on the ``_fieldmap`` + ``_magnitude`` sidecars and
    ``B0FieldSource`` (same value) on every ``_bold`` sidecar. Sessions with BOLD
    but no field map are counted (``no_fmap``) and skipped; a field map with no
    BOLD is counted (``orphan_fmap``) and skipped. Raises ``ValueError`` if a
    session has more than one field map (asserted-never).
    """
    cohort_dir = Path(cohort_dir)
    summary = LinkSummary()

    for sub_dir in sorted(cohort_dir.glob("sub-*")):
        if not sub_dir.is_dir():
            continue
        for ses_dir in sorted(sub_dir.glob("ses-*")):
            if not ses_dir.is_dir():
                continue
            fmaps = sorted((ses_dir / "fmap").glob("*_fieldmap.nii.gz"))
            bolds = sorted((ses_dir / "func").glob("*_bold.json"))

            if len(fmaps) > 1:
                raise ValueError(
                    f"{ses_dir}: multiple field maps {[f.name for f in fmaps]} "
                    "— expected exactly one per session"
                )
            if not fmaps:
                if bolds:
                    summary.no_fmap += 1
                    log.warning("%s: BOLD present but no field map — no SDC", ses_dir)
                continue
            if not bolds:
                summary.orphan_fmap += 1
                log.info("%s: field map present but no BOLD — skipped", ses_dir)
                continue

            ident = _identifier(sub_dir, ses_dir)
            fieldmap = fmaps[0]
            magnitude = fieldmap.with_name(
                fieldmap.name.replace("_fieldmap.nii.gz", "_magnitude.nii.gz")
            )
            for nii in (fieldmap, magnitude):
                sidecar = nii.with_name(nii.name.replace(".nii.gz", ".json"))
                if sidecar.exists():
                    _set_sidecar_key(sidecar, "B0FieldIdentifier", ident)
            for bold in bolds:
                if _set_sidecar_key(bold, "B0FieldSource", ident):
                    summary.bolds_stamped += 1
            summary.sessions_linked += 1

    return summary
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_b0link.py -v`
Expected: PASS (all 13 tests)

- [ ] **Step 5: Commit**

```bash
git add src/network_fmri/b0link.py tests/test_b0link.py
git commit -m "feat(b0link): link_b0_fields walks sessions and stamps B0Field*

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: CLI — `fw2bids fmap-link <dir>`

**Files:**
- Modify: `src/network_fmri/run.py` (add `_fmap_link_main`; dispatch in `main`)
- Test: `tests/test_run_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_run_cli.py`:

```python
def test_fmap_link_routes_to_link_b0_fields(monkeypatch, capsys):
    from network_fmri import b0link, run

    seen = {}

    def fake_link(cohort_dir):
        seen["dir"] = cohort_dir
        return b0link.LinkSummary(sessions_linked=3, bolds_stamped=27, no_fmap=1, orphan_fmap=0)

    monkeypatch.setattr(run, "link_b0_fields", fake_link)
    # curate/client path must NOT be reached
    monkeypatch.setattr(run, "_client",
                        lambda: (_ for _ in ()).throw(AssertionError("wrong path")))

    rc = run.main(["fmap-link", "/some/discovery"])
    assert rc == 0
    assert seen["dir"] == "/some/discovery"
    out = capsys.readouterr().out
    assert "sessions_linked=3" in out
    assert "no_fmap=1" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_run_cli.py::test_fmap_link_routes_to_link_b0_fields -v`
Expected: FAIL — `AttributeError: module 'network_fmri.run' has no attribute 'link_b0_fields'`

- [ ] **Step 3: Write minimal implementation**

In `src/network_fmri/run.py`, add the import near the top (with the other
`from network_fmri import ...` imports):

```python
from network_fmri.b0link import link_b0_fields
```

Add this function next to `_trim_main`:

```python
def _fmap_link_main(argv):
    """Stamp B0FieldIdentifier/B0FieldSource across a staged BIDS cohort tree.

    Session-scoped: one Hz field map per session links to every BOLD run it
    corrects, so fMRIPrep/SDCFlows applies SDC. Idempotent and deterministic;
    safe to re-run. Must run before ``datalad`` so the linkage is committed.
    """
    ap = argparse.ArgumentParser(
        prog="fw2bids fmap-link",
        description="Link session field maps to BOLD via BIDS B0Field* metadata.",
    )
    ap.add_argument("bids_dir", help="staged BIDS cohort directory to link in place")
    args = ap.parse_args(argv)

    summary = link_b0_fields(args.bids_dir)
    print(
        f"[fmap-link] sessions_linked={summary.sessions_linked} "
        f"bolds_stamped={summary.bolds_stamped} "
        f"no_fmap={summary.no_fmap} orphan_fmap={summary.orphan_fmap}"
    )
    return 0
```

In `main`, add the dispatch line alongside the other subcommands (before the
`submit`/`pipeline` blocks is fine):

```python
    if argv and argv[0] == "fmap-link":
        return _fmap_link_main(argv[1:])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_run_cli.py -v`
Expected: PASS (existing tests + new one)

- [ ] **Step 5: Commit**

```bash
git add src/network_fmri/run.py tests/test_run_cli.py
git commit -m "feat(cli): fw2bids fmap-link dispatch to link_b0_fields

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Submit module + sbatch template

**Files:**
- Create: `src/network_fmri/submit/fmap_link.py`
- Create: `src/network_fmri/submit/templates/fmap_link.sbatch.tmpl`
- Modify: `src/network_fmri/submit/__init__.py` (add `fmap_link` to `_ROUTE_NAMES`)
- Test: `tests/test_submit.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_submit.py` — first add `fmap_link` to the `from
network_fmri.submit import (...)` block, then append this test:

```python
def test_fmap_link_render(tmp_path):
    script = _render(fmap_link, ["--cohort", "validation", "--staging", str(tmp_path)])
    assert "--array=" not in script  # single job
    assert "--cpus-per-task=2" in script
    assert "--mem=8G" in script
    assert "--time=00:20:00" in script
    assert f"fw2bids fmap-link {tmp_path}/validation" in script


def test_fmap_link_container_swaps_run_prefix(tmp_path):
    sif = _common.DEFAULT_CONTAINER_IMAGE
    script = _render(fmap_link, ["--cohort", "discovery", "--staging", str(tmp_path),
                                 "--container", sif])
    assert f"apptainer exec {sif} fw2bids fmap-link" in script
    assert "uv run --no-sync" not in script
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_submit.py::test_fmap_link_render -v`
Expected: FAIL — `ImportError: cannot import name 'fmap_link'`

- [ ] **Step 3: Write minimal implementation**

Create `src/network_fmri/submit/fmap_link.py`:

```python
"""Submit the ``fmap_link`` stage: stamp B0Field* metadata on the staged tree.

Single job: ``fw2bids fmap-link <staging>/<cohort>``. Lightweight (edits small
JSON sidecars); runs for all cohorts. Must precede ``datalad`` so the linkage is
committed into the tracked tree.
"""

from __future__ import annotations

import argparse
import sys

from network_fmri.submit import _common

STAGE = "fmap_link"
DEFAULT_RESOURCES = {"nthreads": 2, "mem_gb": 8, "time": "00:20:00"}


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit the network_fmri fmap_link SLURM job")
    _common.add_common_args(parser)
    return parser


def render(args: argparse.Namespace) -> str:
    ctx = _common.single_context(args, DEFAULT_RESOURCES, stage=STAGE)
    return _common.render(STAGE, ctx)


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    return _common.finish(render(args), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
```

Create `src/network_fmri/submit/templates/fmap_link.sbatch.tmpl`:

```
#!/bin/bash
#SBATCH -J {job_name}
#SBATCH --cpus-per-task={nthreads}
#SBATCH --mem={mem_gb}G
#SBATCH --time={time}
#SBATCH -p {partition}
#SBATCH -o {log_dir}/%x-%j.out
#SBATCH -e {log_dir}/%x-%j.err
{mail_line}

set -euo pipefail

{env_exports}

# Stamp session-scoped B0FieldIdentifier/B0FieldSource so fMRIPrep/SDCFlows
# applies SDC. Idempotent + deterministic; must run before the datalad stage so
# the linkage is committed into the tracked tree.
{run_prefix} fw2bids fmap-link {cohort_dir}
```

In `src/network_fmri/submit/__init__.py`, add `"fmap_link"` to `_ROUTE_NAMES`
(after `"events"`, matching DAG order):

```python
_ROUTE_NAMES = ("curate", "export", "merge", "trim", "events", "fmap_link", "datalad", "select")
```

Also update the module docstring's stage list in `__init__.py` to include
`fmap_link` (the `fw2bids submit <...>` usage line).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_submit.py::test_fmap_link_render tests/test_submit.py::test_fmap_link_container_swaps_run_prefix -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/network_fmri/submit/fmap_link.py src/network_fmri/submit/templates/fmap_link.sbatch.tmpl src/network_fmri/submit/__init__.py tests/test_submit.py
git commit -m "feat(submit): fmap_link stage module + sbatch template

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Wire `fmap_link` into the pipeline DAG

**Files:**
- Modify: `src/network_fmri/submit/pipeline.py` (import + `_STAGES`)
- Test: `tests/test_submit.py` (update the two pipeline-order assertions)

- [ ] **Step 1: Update the failing tests**

In `tests/test_submit.py`, update `test_pipeline_chains_afterok_in_order` to
expect the new stage between events and datalad:

```python
    assert stages == [
        "nf-curate-discovery",
        "nf-export-discovery",
        "nf-merge-discovery",
        "nf-trim-discovery",
        "nf-events-discovery",
        "nf-fmap_link-discovery",
        "nf-datalad-discovery",
        "nf-select-discovery",
    ]
```

And update `test_pipeline_skips_events_for_excluded` — `fmap_link` is NOT
cohort-gated, so it DOES run for excluded (between trim and datalad, since events
is skipped):

```python
    assert stages == [
        "nf-curate-excluded",
        "nf-export-excluded",
        "nf-merge-excluded",
        "nf-trim-excluded",
        "nf-fmap_link-excluded",
        "nf-datalad-excluded",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_submit.py -k pipeline -v`
Expected: FAIL — actual stage lists lack `nf-fmap_link-*`

- [ ] **Step 3: Write minimal implementation**

In `src/network_fmri/submit/pipeline.py`, add `fmap_link` to the import:

```python
from network_fmri.submit import _common, curate, datalad, events, export, fmap_link, merge, select, trim
```

Insert into `_STAGES` after the `events` entry and before `datalad`:

```python
    ("events", events, False),
    ("fmap_link", fmap_link, False),
    ("datalad", datalad, False),
```

(Do NOT add `fmap_link` to `_COHORT_GATED` — it runs for all cohorts.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_submit.py -v`
Expected: PASS — including `test_all_stage_templates_render_without_leftover_placeholders`
(auto-covers the new template) and both pipeline-order tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (all tests green).

- [ ] **Step 6: Commit**

```bash
git add src/network_fmri/submit/pipeline.py tests/test_submit.py
git commit -m "feat(pipeline): wire fmap_link stage (events -> fmap_link -> datalad, all cohorts)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Operational validation on real data (manual)

**Files:** none (execution + verification only). Run on Sherlock with the
scratch venv (`module load uv`; `UV_PROJECT_ENVIRONMENT`/`UV_CACHE_DIR` set).

- [ ] **Step 1: Dry-run the linkage on a COPY of one OPT cohort (non-destructive)**

Copy discovery's staged tree to a scratch sandbox and run the CLI directly (venv,
no container needed for the logic):

```bash
cp -r /scratch/users/logben/bids_repro/OPT/discovery /scratch/users/logben/bids_repro/fmaplink_test
uv run fw2bids fmap-link /scratch/users/logben/bids_repro/fmaplink_test
```

Expected summary: `sessions_linked=60 no_fmap=0 orphan_fmap=1` and `bolds_stamped`
equal to the total number of `_bold.json` sidecars across the 60 linked sessions
(a positive number; cross-check with
`find /scratch/users/logben/bids_repro/fmaplink_test -path '*/func/*_bold.json' | wc -l`).

- [ ] **Step 2: Spot-check one linked session's sidecars**

```bash
cd /scratch/users/logben/bids_repro/fmaplink_test
sub=$(ls -d sub-*/ses-*/fmap | head -1 | cut -d/ -f1)
grep -l B0FieldIdentifier sub-*/ses-*/fmap/*.json | head
grep -h B0FieldSource sub-*/ses-*/func/*_bold.json | sort -u | head
```

Expected: both `_fieldmap.json` and `_magnitude.json` carry `B0FieldIdentifier`;
every bold in the session carries the SAME `B0FieldSource` value; the value
matches `<sub-label>_<ses>` (e.g. `s03_ses-01`).

- [ ] **Step 3: Confirm idempotency + clean diff**

```bash
uv run fw2bids fmap-link /scratch/users/logben/bids_repro/fmaplink_test   # 2nd run
```

Expected: 2nd run reports `bolds_stamped=0` (idempotent no-op). Confirm the added
key is a clean one-line addition (compare a bold sidecar against the OPT source):

```bash
b=sub-s03/ses-01/func/sub-s03_ses-01_task-rest_run-1_echo-1_bold.json
diff /scratch/users/logben/bids_repro/OPT/discovery/$b \
     /scratch/users/logben/bids_repro/fmaplink_test/$b
```

Expected: a single added `"B0FieldSource": "s03_ses-01"` line, no reflow. Then
clean up: `rm -rf /scratch/users/logben/bids_repro/fmaplink_test`.

- [ ] **Step 4: Rebuild the container (new code runs inside it)**

```bash
sbatch --wrap="apptainer build --fakeroot --force \
  /home/groups/russpold/singularity_images/network_fmri.sif \
  /home/users/logben/network_fmri/network_fmri.def" \
  --partition=russpold,normal --mem=8G --time=00:30:00
```

Wait for COMPLETED (`squeue --me`), then confirm the CLI exists in the image:

```bash
apptainer exec /home/groups/russpold/singularity_images/network_fmri.sif \
  fw2bids fmap-link --help
```

Expected: help text prints (verb registered inside the container).

- [ ] **Step 5: Prove the new stage is byte-deterministic (discovery A≡B, with fmap_link)**

Run the full optimized DAG twice fresh from `export`, each to its own staging dir,
now that `fmap_link` is in the chain:

```bash
for RUN in NEWA NEWB; do
  uv run fw2bids pipeline --cohort discovery --container \
    --staging /scratch/users/logben/bids_repro/$RUN --start-stage export
done
```

After both DAGs finish (`squeue --me` clear), generate tree manifests and diff
(same method as the repro campaign — exclude the 2 known-benign fields):

```bash
sif=/home/groups/russpold/singularity_images/network_fmri.sif
man=/scratch/users/logben/bids_repro/manifests
for RUN in NEWA NEWB; do
  O=/scratch/users/logben/bids_repro/$RUN/discovery
  apptainer exec --pwd "$O" "$sif" git ls-tree -r HEAD | awk '{print $4"\t"$3}' | sort > "$man/${RUN}_discovery.tsv"
done
diff "$man/NEWA_discovery.tsv" "$man/NEWB_discovery.tsv" \
  | grep -E '^[<>]' | awk '{print $2}' | sort -u \
  | grep -vE 'exclusions_lock\.json|\.datalad/config' | wc -l
```

Expected: `0` — the `fmap_link` stage is byte-deterministic; NEWA ≡ NEWB.

- [ ] **Step 6: Confirm BIDS validity is preserved**

```bash
apptainer exec /home/groups/russpold/singularity_images/bids-validator_1.14.6.simg \
  bids-validator /scratch/users/logben/bids_repro/NEWA/discovery --ignoreWarnings
```

Expected: 0 errors (`B0FieldIdentifier`/`B0FieldSource` are valid BIDS metadata).

- [ ] **Step 7: Record + open PR**

Update the repro-campaign memory (new stage landed; discovery NEWA≡NEWB with
fmap_link; container rebuilt). Push `feat/fmap-b0-linkage` and open a PR against
`main` summarizing the stage, tests, and the determinism/BIDS-validity evidence.

---

## Notes / Out of Scope

- Automated anat QC (MRIQC IQMs) and encoding manual anat exclusions are a
  separate, MRIQC-gated design — not part of this plan.
- After merge, the canonical discovery/validation/excluded runs must be
  regenerated with `fmap_link` in the DAG before OAK promotion (the current OPT
  trees lack B0 linkage). That regeneration is tracked in the repro campaign, not
  here.
