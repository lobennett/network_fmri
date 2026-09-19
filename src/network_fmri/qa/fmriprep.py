"""Direct fMRIPrep commands and completion checks for the curated BIDS dataset."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from pathlib import Path

from network_fmri.config import WorkflowConfig
from network_fmri.containers import (
    apptainer_prefix,
    bind,
    current_datalad_commit,
    job_tmpdir,
    receipt_path,
    subject_receipt,
    verify_subject_receipt,
)
from network_fmri.models import Runner, StageResult
from network_fmri.stages import StageError


def fmriprep_participant_command(config: WorkflowConfig, subject: str) -> tuple[str, ...]:
    """Build one all-sessions fMRIPrep invocation for a roster subject."""

    if subject not in config.subjects:
        raise StageError(f"fMRIPrep subject {subject!r} is outside the exact roster")
    root = config.paths.bids_dir / "derivatives" / "fmriprep"
    work = config.paths.work_dir / "fmriprep" / subject
    prefix = apptainer_prefix(
        config.fmriprep,
        binds=(
            bind(config.paths.bids_dir, "/data", read_only=True),
            bind(root, "/out"),
            bind(work, "/work"),
            bind(config.paths.templateflow_dir, "/templateflow", read_only=True),
            bind(config.paths.freesurfer_license, "/license.txt", read_only=True),
            bind(job_tmpdir(), "/tmp"),
        ),
        environment=(("TEMPLATEFLOW_HOME", "/templateflow"),),
    )
    return prefix + (
        "/data", "/out", "participant", "--participant-label", subject, "-w", "/work",
        "--dummy-scans", "0", "--no-submm-recon",
        "--output-spaces", "MNI152NLin2009cAsym:res-2", "T1w", "fsnative", "fsaverage6",
        "--cifti-output", "91k", "--me-output-echos", "--use-syn-sdc", "warn",
        "--random-seed", "12345", "--skull-strip-fixed-seed", "--skull-strip-t1w", "force",
        "--notrack", "--md-only-boilerplate", "--skip-bids-validation", "--stop-on-first-crash",
        "--fs-license-file", "/license.txt", "--nprocs", str(config.slurm.cpus),
        "--omp-nthreads", str(min(2, config.slurm.cpus)),
        "--mem-mb", str(config.slurm.memory_gb * 1024),
    )


def fmriprep_subject_receipt(config: WorkflowConfig, subject: str, input_commit: str) -> dict[str, object]:
    """Return the receipt a successful fMRIPrep worker must write before consolidation."""

    if subject not in config.subjects:
        raise StageError(f"fMRIPrep subject {subject!r} is outside the exact roster")
    return subject_receipt(
        subject=subject,
        input_datalad_commit=input_commit,
        container=config.fmriprep,
        invocation=fmriprep_participant_command(config, subject),
    )


def verify_fmriprep(config: WorkflowConfig, runner: Runner = subprocess.run) -> StageResult:
    """Require all approved roster outputs and reports before the final milestone."""

    root = config.paths.bids_dir / "derivatives" / "fmriprep"
    _require_derivative_description(root)
    try:
        input_commit = current_datalad_commit(config.paths.bids_dir, runner)
    except ValueError as error:
        raise StageError(str(error)) from error
    missing_outputs = [
        subject for subject in config.subjects
        if not (root / f"sub-{subject}").is_dir()
    ]
    if missing_outputs:
        raise StageError("fMRIPrep completion has missing subject outputs: " + ", ".join(missing_outputs))
    missing_reports = [
        subject for subject in config.subjects
        if not (root / f"sub-{subject}.html").is_file()
    ]
    if missing_reports:
        raise StageError("fMRIPrep completion has missing subject reports: " + ", ".join(missing_reports))
    raw_t1 = _raw_t1w(config.paths.bids_dir, config.subjects)
    no_raw_anat = [subject for subject in config.subjects if not raw_t1[subject]]
    if no_raw_anat:
        raise StageError("fMRIPrep has no eligible T1w anatomy for roster subjects: " + ", ".join(no_raw_anat))
    missing_anat = [
        subject for subject in config.subjects
        if not _has_preprocessed_anat(root, subject)
    ]
    if missing_anat:
        raise StageError("fMRIPrep completion has missing preprocessed T1w anatomy: " + ", ".join(missing_anat))
    expected_bold = _raw_bold(config.paths.bids_dir, config.subjects)
    no_raw_bold = [
        subject for subject in config.subjects
        if not any(path.parents[2].name == f"sub-{subject}" for path in expected_bold)
    ]
    if no_raw_bold:
        raise StageError("fMRIPrep has no eligible BOLD acquisition for roster subjects: " + ", ".join(no_raw_bold))
    missing_bold = [
        path for path in expected_bold
        if not _preprocessed_bold_path(root, config.paths.bids_dir, path).is_file()
        or _preprocessed_bold_path(root, config.paths.bids_dir, path).stat().st_size == 0
    ]
    if missing_bold:
        raise StageError("fMRIPrep completion has missing preprocessed BOLD files: " + _display(missing_bold, config.paths.bids_dir))
    bad_receipts = []
    for subject in config.subjects:
        path = receipt_path(root, "fmriprep", subject)
        try:
            verify_subject_receipt(
                path,
                subject=subject,
                input_datalad_commit=input_commit,
                container=config.fmriprep,
                invocation=fmriprep_participant_command(config, subject),
            )
        except ValueError as error:
            bad_receipts.append(str(error))
    if bad_receipts:
        raise StageError("fMRIPrep completion has stale or missing subject receipts: " + "; ".join(bad_receipts[:3]))
    crashes = _crash_files((root, config.paths.work_dir / "fmriprep"))
    if crashes:
        raise StageError("fMRIPrep completion found crash evidence: " + _display(crashes, config.paths.bids_dir))
    return StageResult(
        "fmriprep-complete", (root, root / "dataset_description.json"),
        {"subjects": len(config.subjects)},
    )


def _require_derivative_description(root: Path) -> None:
    description = root / "dataset_description.json"
    try:
        value = json.loads(description.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StageError(f"fMRIPrep derivative description is missing or malformed: {description}") from error
    if not isinstance(value, dict) or value.get("DatasetType") != "derivative":
        raise StageError(f"fMRIPrep derivative description is not a BIDS derivative: {description}")


def _has_preprocessed_anat(root: Path, subject: str) -> bool:
    candidates = (root / f"sub-{subject}").glob("**/anat/*_desc-preproc_T1w.nii*")
    return any(path.is_file() and path.stat().st_size > 0 for path in candidates)


def _raw_t1w(bids_dir: Path, subjects: Iterable[str]) -> dict[str, tuple[Path, ...]]:
    return {
        subject: tuple(sorted(
            path for path in (bids_dir / f"sub-{subject}").glob("ses-*/anat/*_T1w.nii*")
            if path.is_file()
        ))
        for subject in subjects
    }


def _raw_bold(bids_dir: Path, subjects: Iterable[str]) -> tuple[Path, ...]:
    return tuple(sorted(
        path
        for subject in subjects
        for path in (bids_dir / f"sub-{subject}").glob("ses-*/func/*_bold.nii*")
        if path.is_file()
    ))


def _preprocessed_bold_path(root: Path, bids_dir: Path, raw: Path) -> Path:
    relative = raw.relative_to(bids_dir)
    stem = raw.name.removesuffix(".nii.gz").removesuffix(".nii")
    name = stem.removesuffix("_bold") + "_desc-preproc_bold.nii.gz"
    return root / relative.with_name(name)


def _crash_files(roots: Iterable[Path]) -> tuple[Path, ...]:
    return tuple(sorted(
        path for root in roots if root.is_dir() for path in root.rglob("crash*")
        if path.is_file() or path.is_dir()
    ))


def _display(paths: Iterable[Path], bids_dir: Path, limit: int = 8) -> str:
    values = list(paths)
    shown = [str(path.relative_to(bids_dir)) if path.is_relative_to(bids_dir) else str(path) for path in values[:limit]]
    suffix = f" (+{len(values) - limit} more)" if len(values) > limit else ""
    return ", ".join(shown) + suffix


def record(argv: list[str] | None = None) -> int:
    """Legacy registry target retained until that registry is removed in Task 8."""

    del argv
    raise RuntimeError("legacy fMRIPrep archive assembly is unavailable in the single-dataset workflow")


def main(argv: list[str] | None = None) -> int:
    """Legacy registry target retained until that registry is removed in Task 8."""

    return record(argv)
