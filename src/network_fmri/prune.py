"""Physically remove excluded scans from a staged BIDS tree, then renumber the
survivors so run indices are contiguous from ``run-1``.

This replaces per-scan selection-by-filter for the preprocessing pipelines. A
``bids-filter-file`` is one rule set per data type: it can say "these 19 tasks", but
never "goNogo run-2 but not run-1, and only in ses-01" — so it cannot express a
per-scan quality call. Deleting the file has exactly that precision, and it leaves a
tree that maps 1:1 onto the derivatives, with no filter plumbing anywhere (at
subject level babs generates no filter of its own either).

What keeps this reproducible + auditable rather than manual surgery:

* it runs as a DAG stage, so ``Flywheel -> BIDS -> prune`` rebuilds the same tree;
* ``code/exclusions_lock.json`` stays the reason-of-record (this reads it, never
  invents an exclusion);
* the old->new mapping is written to ``code/pruned.tsv``;
* DataLad keeps the removed content recoverable from history — as long as nobody
  runs ``git annex dropunused`` on the tree.

Default ``prune_sources=("short-run",)``: a truncated 4-D file is what actually
breaks the BIDS apps. A ``behavioral-qc`` exclusion means the events logfile is
defective while the BOLD is fine, so it stays in for preprocessing and is enforced
downstream at lev1.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PRUNE_SOURCES = ("short-run",)
RECORD_RELPATH = "code/pruned.tsv"

# Sidecar key stamped on a renumbered scan, naming the run label it was acquired
# under. This is what makes the stage idempotent: after pruning, the surviving good
# scan may SIT at a run label the lockfile marks excluded (run-1 excluded, run-2
# renamed to run-1), so a second pass would otherwise delete the good data. Reading
# the stamp off the tree — rather than trusting an external record — also survives a
# crash mid-apply, and keeps the acquisition-order provenance with the file, the same
# way trim.py stamps NumberOfVolumesDiscardedByUser.
ORIGINAL_RUN_KEY = "OriginalRun"

_RUN_RE = re.compile(r"_run-(\d+)")


@dataclass
class PrunePlan:
    """What the stage will do. Built without touching the tree."""

    bids_dir: Path
    deletions: list[Path] = field(default_factory=list)
    # (old, new) — always applied after deletions, low run index first.
    renames: list[tuple[Path, Path]] = field(default_factory=list)
    reasons: dict[Path, str] = field(default_factory=dict)


def _strip(value: str, prefix: str) -> str:
    return value[len(prefix):] if value.startswith(prefix) else value


def _run_label(path: Path) -> str | None:
    m = _RUN_RE.search(path.name)
    return m.group(1) if m else None


def _scan_files(func: Path, sub: str, ses: str, task: str, run: str) -> list[Path]:
    """Every file belonging to one (sub, ses, task, run): all echoes, sidecars, events.

    The trailing ``_`` after the run label keeps ``run-1`` from matching ``run-10``.
    """
    if not func.is_dir():
        return []
    return sorted(func.glob(f"{sub}_{ses}_{task}_run-{run}_*"))


def _runs_present(func: Path, sub: str, ses: str, task: str) -> list[str]:
    if not func.is_dir():
        return []
    runs = {r for p in func.glob(f"{sub}_{ses}_{task}_run-*") if (r := _run_label(p))}
    return sorted(runs, key=int)


def plan_prune(
    bids_dir,
    exclusions: list[dict],
    *,
    prune_sources: tuple[str, ...] = DEFAULT_PRUNE_SOURCES,
    anat_keep: dict[str, str] | None = None,
    anat_acquisition: str | None = None,
) -> PrunePlan:
    """Plan the deletions + renumbering for one cohort tree.

    ``exclusions`` is the ``exclusions`` list of a compiled lockfile. ``anat_keep``
    maps ``sub-XX -> ses-YY``, the anat-QC-selected session for that subject; every
    other ``anat_acquisition`` T1w of that subject is deleted (both required
    together). Raises ``ValueError`` if a deletion would remove the only
    ``events.tsv`` of a task whose other run survives — that would silently drop the
    good run from lev1, and the fix belongs in the reconciliation manifest
    (``dest_run``), not here.
    """
    bids_dir = Path(bids_dir)
    plan = PrunePlan(bids_dir=bids_dir)

    # --- functional: delete excluded scans -----------------------------------
    targets: dict[tuple[str, str, str], set[str]] = {}
    for entry in exclusions:
        if entry.get("source") not in prune_sources:
            continue
        if entry.get("action") not in (None, "exclude"):
            continue
        sub, ses, task = entry["subject"], entry["session"], entry["task"]
        targets.setdefault((sub, ses, task), set()).add(_strip(entry["run"], "run-"))

    for (sub, ses, task), runs in sorted(targets.items()):
        func = bids_dir / sub / ses / "func"
        if _already_pruned(func, sub, ses, task):
            continue
        present = _runs_present(func, sub, ses, task)
        surviving = [r for r in present if r not in runs]
        for run in sorted(runs, key=int):
            doomed = _scan_files(func, sub, ses, task, run)
            if not doomed:
                continue  # already pruned — idempotent
            if surviving and any(p.name.endswith("_events.tsv") for p in doomed):
                others = [
                    p
                    for r in surviving
                    for p in _scan_files(func, sub, ses, task, r)
                    if p.name.endswith("_events.tsv")
                ]
                if not others:
                    raise ValueError(
                        f"refusing to prune {sub}/{ses}/{task} run-{run}: it holds the "
                        f"only events.tsv for that task, and run-{surviving[0]} "
                        f"survives without one. The behavioural data is paired with "
                        f"the excluded run — fix dest_run in the reconciliation "
                        f"manifest and regenerate events first."
                    )
            for path in doomed:
                plan.deletions.append(path)
                plan.reasons[path] = _reason_of(exclusions, sub, ses, task, run)

    # --- functional: renumber survivors to 1..N ------------------------------
    for (sub, ses, task), runs in sorted(targets.items()):
        func = bids_dir / sub / ses / "func"
        if _already_pruned(func, sub, ses, task):
            continue
        surviving = [r for r in _runs_present(func, sub, ses, task) if r not in runs]
        for new_index, old_run in enumerate(surviving, start=1):
            if str(new_index) == old_run:
                continue
            for path in _scan_files(func, sub, ses, task, old_run):
                new_name = path.name.replace(f"_run-{old_run}_", f"_run-{new_index}_", 1)
                plan.renames.append((path, path.with_name(new_name)))
                plan.reasons[path] = (
                    f"renumbered after pruning run-{','.join(sorted(runs, key=int))}"
                )

    # --- anat: keep only the QC-selected T1w per subject ----------------------
    if anat_keep:
        if not anat_acquisition:
            raise ValueError("anat_keep requires anat_acquisition")
        for sub, keep_ses in sorted(anat_keep.items()):
            for path in sorted(bids_dir.glob(
                f"{sub}/ses-*/anat/{sub}_ses-*_acq-{anat_acquisition}_*T1w.*"
            )):
                ses = path.parent.parent.name
                if ses == keep_ses:
                    continue
                plan.deletions.append(path)
                plan.reasons[path] = f"anat QC selected {keep_ses} for {sub}"

    return plan


def _already_pruned(func: Path, sub: str, ses: str, task: str) -> bool:
    """True if this (sub, ses, task) has already been renumbered by a previous run.

    Detected from the ``OriginalRun`` sidecar stamp, so a second pass can't mistake a
    renumbered survivor for the excluded scan that used to hold its run label.
    """
    if not func.is_dir():
        return False
    for sidecar in func.glob(f"{sub}_{ses}_{task}_run-*_bold.json"):
        try:
            if ORIGINAL_RUN_KEY in json.loads(sidecar.read_text()):
                return True
        except (json.JSONDecodeError, OSError):
            continue
    return False


def _reason_of(exclusions, sub, ses, task, run) -> str:
    for e in exclusions:
        if (e["subject"], e["session"], e["task"], _strip(e["run"], "run-")) == (
            sub, ses, task, run
        ):
            return e.get("reason", "excluded")
    return "excluded"


def apply_prune(plan: PrunePlan) -> dict:
    """Delete, then rename, then append the old->new record to code/pruned.tsv.

    Deletions run first so a renumber can reuse a freed index. Renames are ordered
    low-index-first by construction, so run-2 -> run-1 happens before run-3 -> run-2
    and nothing is clobbered.
    """
    for path in plan.deletions:
        path.unlink(missing_ok=True)

    for old, new in plan.renames:
        if new.exists():
            raise FileExistsError(f"refusing to overwrite {new} (renaming {old})")
        old.rename(new)
        if new.suffix == ".json":
            _stamp_original_run(new, _run_label(old))

    if plan.deletions or plan.renames:
        _fix_scans_tsv(plan)
        _write_record(plan)

    return {"deleted": len(plan.deletions), "renamed": len(plan.renames)}


def _fix_scans_tsv(plan: PrunePlan) -> list[Path]:
    """Make every ``*_scans.tsv`` match the tree again.

    scans.tsv carries the human-readable ``why`` for excluded scans, so after pruning
    its rows point at files that no longer exist — which the BIDS validator rejects
    (SCANS_FILENAME_NOT_MATCH_DATASET). Rows for renumbered scans are rewritten to the
    new name; rows for deleted scans are dropped. Their ``why`` is not lost: it stays in
    code/exclusions_lock.json and code/pruned.tsv.
    """
    renamed = {old.name: new.name for old, new in plan.renames}
    touched = []
    for scans in sorted(plan.bids_dir.glob("sub-*/ses-*/*_scans.tsv")):
        ses_dir = scans.parent
        lines = scans.read_text().splitlines()
        if not lines:
            continue
        header, rows = lines[0], lines[1:]
        kept = []
        for row in rows:
            if not row.strip():
                continue
            relpath, _, rest = row.partition("\t")
            name = Path(relpath).name
            if name in renamed:
                relpath = str(Path(relpath).with_name(renamed[name]))
            if not (ses_dir / relpath).exists():
                continue
            kept.append(f"{relpath}\t{rest}" if rest else relpath)
        new_text = "\n".join([header, *kept]) + "\n"
        if new_text != scans.read_text():
            scans.write_text(new_text)
            touched.append(scans)
    return touched


def _stamp_original_run(sidecar: Path, original_run: str | None) -> None:
    """Record the acquisition run label on a renumbered scan's sidecar.

    Append-only and indentation-preserving in spirit (same approach as b0link): read,
    add the key, write back. Non-JSON or unreadable sidecars are left alone rather
    than failing the stage.
    """
    if original_run is None:
        return
    try:
        data = json.loads(sidecar.read_text())
    except (json.JSONDecodeError, OSError):
        return
    if ORIGINAL_RUN_KEY in data:
        return
    data[ORIGINAL_RUN_KEY] = f"run-{original_run}"
    tmp = sidecar.with_name(sidecar.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.rename(sidecar)


def _write_record(plan: PrunePlan) -> Path:
    record = plan.bids_dir / RECORD_RELPATH
    record.parent.mkdir(parents=True, exist_ok=True)
    is_new = not record.exists()
    with record.open("a", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
        if is_new:
            writer.writerow(["action", "old", "new", "reason"])
        for path in plan.deletions:
            writer.writerow(
                ["delete", path.relative_to(plan.bids_dir), "", plan.reasons.get(path, "")]
            )
        for old, new in plan.renames:
            writer.writerow(
                ["rename", old.relative_to(plan.bids_dir),
                 new.relative_to(plan.bids_dir), plan.reasons.get(old, "")]
            )
    return record
