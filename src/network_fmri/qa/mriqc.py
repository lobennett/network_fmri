"""Direct MRIQC commands and completion checks for the single BIDS dataset."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from network_fmri.config import WorkflowConfig
from network_fmri.containers import apptainer_prefix, bind, job_tmpdir
from network_fmri.models import StageResult
from network_fmri.stages import StageError


def default_campaign(environ: Mapping[str, str] = os.environ) -> Path:
    """Return the former campaign root during the planned legacy-surface transition."""

    if configured := environ.get("NETWORK_FMRI_CAMPAIGN"):
        return Path(configured)
    return Path(environ.get("SCRATCH", str(Path.home()))) / "mechababs_campaigns" / "r01network"


# Imported by legacy command modules that are removed with the old registry in Task 8.
CAMPAIGN = default_campaign()


def mriqc_participant_command(config: WorkflowConfig, subject: str) -> tuple[str, ...]:
    """Build one participant-level MRIQC call for ``subject``."""

    _require_subject(config, subject)
    return _prefix(config, subject) + (
        "/data", "/out", "participant", "--participant-label", subject,
        "-w", "/work", "--fd_thres", "0.5", "--no-sub", "--no-datalad-get",
    )


def mriqc_group_command(config: WorkflowConfig) -> tuple[str, ...]:
    """Build the dependent MRIQC group-report call."""

    return _prefix(config, "group") + (
        "/data", "/out", "group", "--no-sub", "--no-datalad-get",
    )


def verify_mriqc(config: WorkflowConfig) -> StageResult:
    """Require complete IQMs/reports for the exact roster before consolidation.

    MRIQC preserves each raw BIDS image's relative path while changing its suffix to
    ``.json``/``.html``. Comparing against the current raw tree detects missing echoes,
    sessions, and modalities without maintaining a second inventory format.
    """

    root = config.paths.bids_dir / "derivatives" / "mriqc"
    _require_derivative_description(root)
    missing_subjects = [
        subject for subject in config.subjects
        if not (config.paths.bids_dir / f"sub-{subject}").is_dir()
    ]
    if missing_subjects:
        raise StageError("MRIQC raw dataset is missing roster subjects: " + ", ".join(missing_subjects))
    images = _raw_images(config.paths.bids_dir, config.subjects)
    missing_iqms = [
        path for path in images if not _derivative_companion(root, config.paths.bids_dir, path, ".json").is_file()
    ]
    missing_reports = [
        path for path in images if not _derivative_companion(root, config.paths.bids_dir, path, ".html").is_file()
    ]
    if missing_iqms:
        raise StageError("MRIQC completion has missing IQMs: " + _display(missing_iqms, config.paths.bids_dir))
    if missing_reports:
        raise StageError("MRIQC completion has missing individual reports: " + _display(missing_reports, config.paths.bids_dir))
    if not any(root.glob("group_*.html")) or not any(root.glob("group_*.tsv")):
        raise StageError("MRIQC completion is missing group reports or tables")
    crashes = _crash_files((root, config.paths.work_dir / "mriqc"))
    if crashes:
        raise StageError("MRIQC completion found crash evidence: " + _display(crashes, config.paths.bids_dir))
    return StageResult(
        "mriqc-complete", (root, root / "dataset_description.json"),
        {"subjects": len(config.subjects), "iqms": len(images), "reports": len(images)},
    )


def _prefix(config: WorkflowConfig, worker: str) -> tuple[str, ...]:
    root = config.paths.bids_dir / "derivatives" / "mriqc"
    work = config.paths.work_dir / "mriqc" / worker
    return apptainer_prefix(
        config.mriqc,
        binds=(
            bind(config.paths.bids_dir, "/data", read_only=True),
            bind(root, "/out"),
            bind(work, "/work"),
            bind(config.paths.templateflow_dir, "/templateflow", read_only=True),
            bind(job_tmpdir(), "/tmp"),
        ),
        environment=(("TEMPLATEFLOW_HOME", "/templateflow"),),
    )


def _require_subject(config: WorkflowConfig, subject: str) -> None:
    if subject not in config.subjects:
        raise StageError(f"MRIQC subject {subject!r} is outside the exact roster")


def _require_derivative_description(root: Path) -> None:
    description = root / "dataset_description.json"
    try:
        value = json.loads(description.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StageError(f"MRIQC derivative description is missing or malformed: {description}") from error
    if not isinstance(value, dict) or value.get("DatasetType") != "derivative":
        raise StageError(f"MRIQC derivative description is not a BIDS derivative: {description}")


def _raw_images(bids_dir: Path, subjects: Iterable[str]) -> tuple[Path, ...]:
    images: list[Path] = []
    for subject in subjects:
        root = bids_dir / f"sub-{subject}"
        for path in root.glob("ses-*/*/*.nii*"):
            if path.is_file() and _nifti_suffix(path) in {"bold", "T1w", "T2w"}:
                images.append(path)
    return tuple(sorted(images))


def _derivative_companion(root: Path, bids_dir: Path, raw: Path, suffix: str) -> Path:
    relative = raw.relative_to(bids_dir)
    return root / relative.with_name(_stem(raw) + suffix)


def _nifti_suffix(path: Path) -> str | None:
    stem = _stem(path)
    return stem.rsplit("_", 1)[-1] if "_" in stem else None


def _stem(path: Path) -> str:
    return path.name.removesuffix(".nii.gz").removesuffix(".nii")


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
    raise RuntimeError("legacy MRIQC archive assembly is unavailable in the single-dataset workflow")


def main(argv: list[str] | None = None) -> int:
    """Legacy registry target retained until that registry is removed in Task 8."""

    return record(argv)
