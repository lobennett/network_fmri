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
from uuid import uuid4
from pathlib import Path

from network_fmri.models import StageResult
from network_fmri.prepare.sidecar import path_for, read
from network_fmri.stages import StageError

log = logging.getLogger(__name__)

N_DUMMY = 7


def trim_one(nifti_path: Path) -> str:
    """Trim or skip one BOLD NIfTI. Returns trimmed / already / too_short / error."""
    import nibabel as nib

    json_path = path_for(nifti_path)
    temporary_nifti = nifti_path.with_name(f".{nifti_path.name}.{uuid4().hex}.trim.nii.gz")
    temporary_sidecar = json_path.with_name(f".{json_path.name}.{uuid4().hex}.trim.json")

    try:
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
        nib.save(img.slicer[:, :, :, N_DUMMY:], str(temporary_nifti))
        temporary_sidecar.write_text(
            json.dumps(updated_sidecar, indent=2) + "\n", encoding="utf-8"
        )
        _publish_trim_pair(nifti_path, json_path, temporary_nifti, temporary_sidecar)

        log.info("trimmed %d -> %d volumes: %s", n_vols, n_vols - N_DUMMY, nifti_path.name)
        return "trimmed"

    except Exception as e:
        log.error("failed on %s: %s", nifti_path.name, e)
        temporary_nifti.unlink(missing_ok=True)
        temporary_sidecar.unlink(missing_ok=True)
        return "error"


def _publish_trim_pair(
    nifti_path: Path,
    sidecar_path: Path,
    temporary_nifti: Path,
    temporary_sidecar: Path,
) -> None:
    """Publish a trimmed NIfTI and its marker as one recoverable transaction."""
    nifti_backup = nifti_path.with_name(f".{nifti_path.name}.{uuid4().hex}.trim.backup")
    sidecar_backup = sidecar_path.with_name(f".{sidecar_path.name}.{uuid4().hex}.trim.backup")
    moved_nifti = False
    moved_sidecar = False
    try:
        os.replace(nifti_path, nifti_backup)
        moved_nifti = True
        os.replace(temporary_nifti, nifti_path)
        os.replace(sidecar_path, sidecar_backup)
        moved_sidecar = True
        os.replace(temporary_sidecar, sidecar_path)
    except Exception:
        # Restore the complete original pair before exposing the failure.  This
        # prevents a retry from interpreting a shortened but unmarked NIfTI as
        # untrimmed and removing another seven volumes.
        if moved_sidecar and sidecar_backup.exists():
            os.replace(sidecar_backup, sidecar_path)
        if moved_nifti and nifti_backup.exists():
            os.replace(nifti_backup, nifti_path)
        raise
    finally:
        temporary_nifti.unlink(missing_ok=True)
        temporary_sidecar.unlink(missing_ok=True)
        nifti_backup.unlink(missing_ok=True)
        sidecar_backup.unlink(missing_ok=True)


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
