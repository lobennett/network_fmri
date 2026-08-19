"""Trim dummy (non-steady-state) volumes from BOLD NIfTIs, in place and idempotently.

fMRIPrep is run with ``--dummy-scans 0``, so the dummy volumes must be gone from the
BIDS tree first. The sidecar's ``NumberOfVolumesDiscardedByUser`` records the count
and doubles as the idempotency check.

Ported from the previous pipeline. Each file is atomic (temp file + rename) and
independent, so ``--jobs`` is safe.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import sys
from pathlib import Path

from network_fmri.prepare.sidecar import path_for, read, update

log = logging.getLogger(__name__)

N_DUMMY = 7


def trim_one(nifti_path: Path) -> str:
    """Trim or skip one BOLD NIfTI. Returns trimmed / already / too_short / error."""
    import nibabel as nib

    json_path = path_for(nifti_path)
    tmp_path = nifti_path.parent / nifti_path.name.replace("_bold.nii.gz", "_bold_tmp.nii.gz")

    try:
        sidecar = read(json_path)
        if sidecar.get("NumberOfVolumesDiscardedByUser") == N_DUMMY:
            return "already"

        img = nib.load(str(nifti_path))
        n_vols = img.shape[3] if len(img.shape) > 3 else 1
        if n_vols <= N_DUMMY:
            log.warning("too short to trim (dim4=%d): %s", n_vols, nifti_path.name)
            return "too_short"

        nib.save(img.slicer[:, :, :, N_DUMMY:], str(tmp_path))
        tmp_path.rename(nifti_path)

        fields = {"NumberOfVolumesDiscardedByUser": N_DUMMY}
        if "NumVolumes" in sidecar:
            fields["NumVolumes"] = n_vols - N_DUMMY
        update(json_path, **fields)

        log.info("trimmed %d -> %d volumes: %s", n_vols, n_vols - N_DUMMY, nifti_path.name)
        return "trimmed"

    except Exception as e:
        log.error("failed on %s: %s", nifti_path.name, e)
        if tmp_path.exists():
            tmp_path.unlink()
        return "error"


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
    return 1 if summary["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
