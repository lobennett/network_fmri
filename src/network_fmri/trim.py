"""Trim 7 dummy (non-steady-state) volumes from BOLD NIfTIs, idempotently.

Ported verbatim (behavior-preserving) from the neuro_workflow monolith's
``scripts/trim_bold.py``. This closes the export -> trim gap: ``fw2bids export``
produces un-trimmed BIDS, but fMRIPrep is run with ``--dummy-scans 0``, so the
dummy volumes must be removed from the staged BIDS tree before fMRIPrep runs.
"""

import json
import logging
import multiprocessing
from pathlib import Path

import nibabel as nib

log = logging.getLogger(__name__)

N_DUMMY = 7


def _trim_one_file(nifti_path: Path) -> str:
    """Trim (or skip) a single BOLD NIfTI in place; return a status string.

    One of ``"trimmed"``, ``"skipped_already_trimmed"``, ``"skipped_too_short"``,
    or ``"error"``. Module-level and picklable (path-only argument) so it can be
    mapped across a :class:`multiprocessing.Pool` — each file is fully
    independent (atomic temp-file + rename, no shared state), so this is safe
    to run concurrently across files.
    """
    json_path = nifti_path.with_name(nifti_path.name.replace(".nii.gz", ".json"))

    # Idempotency check: skip if sidecar already records trimming
    if json_path.exists():
        sidecar = json.loads(json_path.read_text())
        if sidecar.get("NumberOfVolumesDiscardedByUser") == N_DUMMY:
            log.debug("Already trimmed: %s", nifti_path.name)
            return "skipped_already_trimmed"
    else:
        sidecar = {}

    try:
        img = nib.load(str(nifti_path))
        n_vols = img.shape[3] if len(img.shape) > 3 else 1

        if n_vols <= N_DUMMY:
            log.warning("Too short to trim (dim4=%d): %s", n_vols, nifti_path.name)
            return "skipped_too_short"

        # Trim first N_DUMMY volumes (write to temp file, then atomic rename)
        trimmed_data = img.slicer[:, :, :, N_DUMMY:]
        tmp_path = nifti_path.parent / nifti_path.name.replace(
            "_bold.nii.gz", "_bold_tmp.nii.gz"
        )
        nib.save(trimmed_data, str(tmp_path))
        tmp_path.rename(nifti_path)

        # Update sidecar
        sidecar["NumberOfVolumesDiscardedByUser"] = N_DUMMY
        if "NumVolumes" in sidecar:
            sidecar["NumVolumes"] = n_vols - N_DUMMY
        json_path.write_text(json.dumps(sidecar, indent=2) + "\n")

        log.info("Trimmed %d -> %d volumes: %s", n_vols, n_vols - N_DUMMY, nifti_path.name)
        return "trimmed"

    except Exception as e:
        log.error("Failed to process %s: %s", nifti_path.name, e)
        # Clean up temp file if it exists
        tmp_path = nifti_path.parent / nifti_path.name.replace(
            "_bold.nii.gz", "_bold_tmp.nii.gz"
        )
        if tmp_path.exists():
            tmp_path.unlink()
        return "error"


def trim_bold_directory(
    bids_dir: Path, subjects: list[str] | None = None, jobs: int = 1
) -> dict:
    """Trim dummy volumes from all BOLD NIfTIs in a BIDS directory.

    ``subjects`` (bare IDs like ``s10`` or ``sub-s10``), when given, restricts
    processing to those subjects — so a large cohort can be sharded across a
    SLURM array with each task owning a disjoint set of files (no write races).
    Default ``None`` processes every ``sub-*`` (unchanged behavior).

    ``jobs`` controls within-subject file parallelism: each file trim is fully
    independent (atomic temp-file + rename, no shared state), so ``jobs > 1``
    maps ``_trim_one_file`` across a :class:`multiprocessing.Pool`. Default
    ``1`` is serial (unchanged behavior).

    Returns summary dict with counts of trimmed, skipped_already_trimmed,
    skipped_too_short.
    """
    bids_dir = Path(bids_dir)
    summary = {"trimmed": 0, "skipped_already_trimmed": 0, "skipped_too_short": 0, "errors": 0}

    if subjects:
        nifti_paths: list[Path] = []
        for s in subjects:
            sub = s if s.startswith("sub-") else f"sub-{s}"
            nifti_paths.extend(bids_dir.glob(f"{sub}/ses-*/func/*_bold.nii.gz"))
        nifti_paths = sorted(nifti_paths)
    else:
        nifti_paths = sorted(bids_dir.glob("sub-*/ses-*/func/*_bold.nii.gz"))

    if jobs > 1:
        with multiprocessing.Pool(jobs) as pool:
            statuses = pool.map(_trim_one_file, nifti_paths)
    else:
        statuses = [_trim_one_file(nifti_path) for nifti_path in nifti_paths]

    _status_to_key = {
        "trimmed": "trimmed",
        "skipped_already_trimmed": "skipped_already_trimmed",
        "skipped_too_short": "skipped_too_short",
        "error": "errors",
    }
    for status in statuses:
        summary[_status_to_key[status]] += 1

    return summary
