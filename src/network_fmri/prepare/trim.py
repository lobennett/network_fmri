"""Trim dummy (non-steady-state) volumes from BOLD NIfTIs, in place and idempotently.

fMRIPrep is run with ``--dummy-scans 0``, so the dummy volumes must be gone from the
BIDS tree first. The sidecar's ``NumberOfVolumesDiscardedByUser`` records the count
and doubles as the idempotency check.

Ported from the previous pipeline. Each file is atomic (temp file + rename) and
independent, so ``--jobs`` is safe.
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import shutil
from pathlib import Path

from network_fmri.models import StageResult
from network_fmri.prepare.sidecar import path_for, read
from network_fmri.stages import StageError

log = logging.getLogger(__name__)

N_DUMMY = 7


def trim_one(nifti_path: Path) -> str:
    """Trim or skip one BOLD NIfTI. Returns trimmed / already / too_short / error."""
    json_path = path_for(nifti_path)
    paths = _transaction_paths(nifti_path, json_path)

    try:
        _recover_interrupted_trim(nifti_path, json_path, paths)
        import nibabel as nib

        sidecar = read(json_path)
        discarded = sidecar.get("NumberOfVolumesDiscardedByUser")
        if discarded == N_DUMMY:
            return "already"
        if discarded is not None:
            raise ValueError(
                "NumberOfVolumesDiscardedByUser is "
                f"{discarded!r}, expected {N_DUMMY!r} for an already trimmed scan"
            )

        img = nib.load(str(nifti_path))
        n_vols = img.shape[3] if len(img.shape) > 3 else 1
        if n_vols <= N_DUMMY:
            log.warning("too short to trim (dim4=%d): %s", n_vols, nifti_path.name)
            return "too_short"

        fields = {"NumberOfVolumesDiscardedByUser": N_DUMMY}
        if "NumVolumes" in sidecar:
            fields["NumVolumes"] = n_vols - N_DUMMY
        updated_sidecar = {**sidecar, **fields}
        nib.save(img.slicer[:, :, :, N_DUMMY:], str(paths.temporary_nifti))
        paths.temporary_sidecar.write_text(
            json.dumps(updated_sidecar, indent=2) + "\n", encoding="utf-8"
        )
        _publish_trim_pair(nifti_path, json_path, paths)

        log.info("trimmed %d -> %d volumes: %s", n_vols, n_vols - N_DUMMY, nifti_path.name)
        return "trimmed"

    except Exception as e:
        log.error("failed on %s: %s", nifti_path.name, e)
        _cleanup_pending(paths)
        return "error"


class _TransactionPaths:
    """Persistent paths for one recoverable NIfTI/sidecar publication."""

    def __init__(self, nifti_path: Path, sidecar_path: Path) -> None:
        self.temporary_nifti = nifti_path.with_name(f".{nifti_path.name}.trim.pending.nii.gz")
        self.temporary_sidecar = sidecar_path.with_name(f".{sidecar_path.name}.trim.pending.json")
        self.nifti_backup = nifti_path.with_name(f".{nifti_path.name}.trim.backup")
        self.sidecar_backup = sidecar_path.with_name(f".{sidecar_path.name}.trim.backup")
        self.marker = sidecar_path.with_name(f".{sidecar_path.name}.trim.transaction.json")


def _transaction_paths(nifti_path: Path, sidecar_path: Path) -> _TransactionPaths:
    return _TransactionPaths(nifti_path, sidecar_path)


def _publish_trim_pair(
    nifti_path: Path,
    sidecar_path: Path,
    paths: _TransactionPaths,
) -> None:
    """Publish a trimmed pair through a transaction recoverable after process death."""
    _write_transaction_marker(paths)
    try:
        os.replace(nifti_path, paths.nifti_backup)
        os.replace(paths.temporary_nifti, nifti_path)
        os.replace(sidecar_path, paths.sidecar_backup)
        os.replace(paths.temporary_sidecar, sidecar_path)
    except Exception:
        _recover_interrupted_trim(nifti_path, sidecar_path, paths)
        raise
    _complete_transaction(paths)


def _write_transaction_marker(paths: _TransactionPaths) -> None:
    """Durably announce a transaction before replacing either live file."""
    if paths.marker.exists() or paths.nifti_backup.exists() or paths.sidecar_backup.exists():
        raise RuntimeError(f"unfinished trim transaction: {paths.marker}")
    temporary = paths.marker.with_name(paths.marker.name + ".pending")
    payload = {
        "nifti_backup": paths.nifti_backup.name,
        "sidecar_backup": paths.sidecar_backup.name,
    }
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, paths.marker)
    finally:
        temporary.unlink(missing_ok=True)


def _recover_interrupted_trim(
    nifti_path: Path, sidecar_path: Path, paths: _TransactionPaths
) -> None:
    """Restore an incomplete trim before inspecting or changing a BOLD file."""
    if not paths.marker.exists():
        if paths.nifti_backup.exists() or paths.sidecar_backup.exists():
            raise RuntimeError(f"trim backups exist without a transaction marker: {nifti_path}")
        return
    _validate_transaction_marker(paths)
    try:
        sidecar = read(sidecar_path)
    except Exception:
        sidecar = None
    if isinstance(sidecar, dict) and sidecar.get("NumberOfVolumesDiscardedByUser") == N_DUMMY:
        _complete_transaction(paths)
        return

    # The NIfTI may already be shortened while its sidecar marker is missing.
    # Restore both originals, retaining all recovery material if either rename
    # fails so a later invocation can safely retry recovery.
    try:
        if paths.nifti_backup.exists():
            _restore_from_backup(paths.nifti_backup, nifti_path)
        elif not nifti_path.exists():
            raise RuntimeError(f"missing NIfTI and backup: {nifti_path}")
        if paths.sidecar_backup.exists():
            _restore_from_backup(paths.sidecar_backup, sidecar_path)
        elif not sidecar_path.exists():
            raise RuntimeError(f"missing sidecar and backup: {sidecar_path}")
    except Exception as error:
        raise RuntimeError(f"could not restore interrupted trim: {nifti_path}") from error
    _complete_transaction(paths)


def _restore_from_backup(backup: Path, destination: Path) -> None:
    """Copy a backup into place while retaining it until all recovery succeeds."""
    temporary = destination.with_name(f".{destination.name}.trim.restore.pending")
    try:
        shutil.copyfile(backup, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_transaction_marker(paths: _TransactionPaths) -> None:
    try:
        payload = json.loads(paths.marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"malformed trim transaction marker: {paths.marker}") from error
    expected = {
        "nifti_backup": paths.nifti_backup.name,
        "sidecar_backup": paths.sidecar_backup.name,
    }
    if payload != expected:
        raise RuntimeError(f"unsafe trim transaction marker: {paths.marker}")


def _complete_transaction(paths: _TransactionPaths) -> None:
    """Discard backups only after a sidecar proves the pair was fully published."""
    _cleanup_pending(paths)
    paths.nifti_backup.unlink(missing_ok=True)
    paths.sidecar_backup.unlink(missing_ok=True)
    paths.marker.unlink(missing_ok=True)


def _cleanup_pending(paths: _TransactionPaths) -> None:
    paths.temporary_nifti.unlink(missing_ok=True)
    paths.temporary_sidecar.unlink(missing_ok=True)


def trim_tree(bids_dir: Path, subjects: list[str] | None = None, jobs: int = 1) -> dict:
    """Trim every BOLD under ``bids_dir``, or only the given subjects."""
    if subjects:
        paths: list[Path] = []
        for s in subjects:
            sub = s if s.startswith("sub-") else f"sub-{s}"
            paths += bids_dir.glob(f"{sub}/ses-*/func/*_bold.nii.gz")
        paths = sorted(paths)
    else:
        paths = sorted(bids_dir.glob("sub-*/ses-*/func/*_bold.nii.gz"))

    if jobs > 1:
        with multiprocessing.Pool(jobs) as pool:
            statuses = pool.map(trim_one, paths)
    else:
        statuses = [trim_one(p) for p in paths]

    summary = {k: 0 for k in ("trimmed", "already", "too_short", "error")}
    for s in statuses:
        summary[s] += 1
    return summary


def trim_dataset(bids_dir: Path, jobs: int = 1) -> StageResult:
    """Trim exactly seven volumes from every BOLD, or fail the stage.

    ``too_short`` is evidence of a broken source acquisition, not a successful
    no-op.  The caller therefore gets one clear stage error for any unreadable,
    malformed, or insufficiently long BOLD file.
    """
    bids_dir = Path(bids_dir)
    if jobs < 1:
        raise StageError("trim jobs must be positive")
    _require_trim_input(bids_dir)
    summary = trim_tree(bids_dir, jobs=jobs)
    failures = summary["too_short"] + summary["error"]
    if failures:
        raise StageError(f"trim failed for {failures} BOLD file(s): {summary}")
    return StageResult("dummy-volumes-trimmed", (bids_dir,), summary)


def _require_trim_input(bids_dir: Path) -> None:
    """Reject missing, non-BIDS, and empty inputs before an in-place edit."""
    description = bids_dir / "dataset_description.json"
    if not bids_dir.is_dir() or bids_dir.is_symlink():
        raise StageError(f"BIDS directory is missing or unsafe: {bids_dir}")
    try:
        metadata = json.loads(description.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StageError(f"BIDS dataset description is missing or malformed: {description}") from error
    if not isinstance(metadata, dict):
        raise StageError(f"BIDS dataset description is not an object: {description}")
    if not any(path.is_dir() for path in bids_dir.glob("sub-*/ses-*")):
        raise StageError(f"BIDS directory has no subject sessions: {bids_dir}")
    if not any(bids_dir.glob("sub-*/ses-*/func/*_bold.nii.gz")):
        raise StageError(f"BIDS directory has no BOLD scans to trim: {bids_dir}")


def get_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="network_fmri trim-bold")
    p.add_argument("--bids-dir", required=True)
    p.add_argument("--subjects", nargs="+", default=None)
    p.add_argument("--jobs", type=int, default=1)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = get_parser().parse_args(argv)
    summary = trim_tree(Path(args.bids_dir), args.subjects, args.jobs)
    print(f"[trim] {summary}", flush=True)
    return 1 if summary["too_short"] or summary["error"] else 0


def record(argv: list[str] | None = None) -> int:
    """Deprecated legacy route retained until the registry is removed in Task 8."""
    raise RuntimeError("legacy cohort trim is unavailable in the single-dataset workflow")
