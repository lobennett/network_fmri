"""Direct fMRIPrep commands and completion checks for the curated BIDS dataset."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from network_fmri.config import WorkflowConfig
from network_fmri.containers import apptainer_prefix, bind, job_tmpdir
from network_fmri.models import StageResult
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
        "--omp-nthreads", "2", "--mem-mb", str(config.slurm.memory_gb * 1024),
    )


def verify_fmriprep(config: WorkflowConfig) -> StageResult:
    """Require all approved roster outputs and reports before the final milestone."""

    root = config.paths.bids_dir / "derivatives" / "fmriprep"
    _require_derivative_description(root)
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
