"""Direct MRIQC commands and completion checks for the single BIDS dataset."""

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
    group_receipt_path,
    job_tmpdir,
    prepare_bids_app_paths,
    receipt_path,
    subject_receipt,
    verify_subject_receipt,
)
from network_fmri.models import Runner, StageResult
from network_fmri.stages import StageError


def mriqc_participant_command(config: WorkflowConfig, subject: str) -> tuple[str, ...]:
    """Build one participant-level MRIQC call for ``subject``."""

    _require_subject(config, subject)
    return _prefix(config, subject) + (
        "mriqc", "/data", "/out", "participant", "--participant-label", subject,
        "-w", "/work", "--n_cpus", str(config.slurm.cpus),
        "--mem_gb", str(config.slurm.memory_gb), "--fd_thres", "0.5",
        "--no-sub", "--no-datalad-get",
    )


def mriqc_group_command(config: WorkflowConfig) -> tuple[str, ...]:
    """Build the dependent MRIQC group-report call."""

    return _prefix(config, "group") + (
        "mriqc", "/data", "/out", "group", "--no-sub", "--no-datalad-get",
    )


def mriqc_subject_receipt(config: WorkflowConfig, subject: str, input_commit: str) -> dict[str, object]:
    """Return the receipt a successful MRIQC worker must write before consolidation."""

    _require_subject(config, subject)
    return subject_receipt(
        subject=subject,
        input_datalad_commit=input_commit,
        container=config.mriqc,
        invocation=mriqc_participant_command(config, subject),
    )


def mriqc_group_receipt(config: WorkflowConfig, input_commit: str) -> dict[str, object]:
    """Return the receipt a successful MRIQC group worker must write."""

    return subject_receipt(
        subject="group",
        input_datalad_commit=input_commit,
        container=config.mriqc,
        invocation=mriqc_group_command(config),
    )


def verify_mriqc(config: WorkflowConfig, runner: Runner = subprocess.run) -> StageResult:
    """Require complete IQMs/reports for the exact roster before consolidation.

    MRIQC writes one IQM JSON per image. For multi-echo BOLD, it writes one HTML
    report per run and omits the echo entity from that report's name.
    """

    root = config.paths.bids_dir / "derivatives" / "mriqc"
    _require_derivative_description(root)
    try:
        input_commit = current_datalad_commit(config.paths.bids_dir, runner)
    except ValueError as error:
        raise StageError(str(error)) from error
    missing_subjects = [
        subject for subject in config.subjects
        if not (config.paths.bids_dir / f"sub-{subject}").is_dir()
    ]
    if missing_subjects:
        raise StageError("MRIQC raw dataset is missing roster subjects: " + ", ".join(missing_subjects))
    images = _raw_images(config.paths.bids_dir, config.subjects)
    eligible = {subject: 0 for subject in config.subjects}
    for image in images:
        eligible[image.parents[2].name.removeprefix("sub-")] += 1
    no_eligible = [subject for subject, count in eligible.items() if not count]
    if no_eligible:
        raise StageError("MRIQC has no eligible acquisition for roster subjects: " + ", ".join(no_eligible))
    missing_iqms: list[Path] = []
    invalid_iqms: list[Path] = []
    for path in images:
        iqm = _derivative_companion(root, config.paths.bids_dir, path, ".json")
        if not iqm.is_file():
            missing_iqms.append(path)
        elif not _valid_iqm(iqm, require_fd_threshold=_nifti_suffix(path) == "bold"):
            invalid_iqms.append(iqm)
    reports = {_report_path(root, path) for path in images}
    missing_reports = [path for path in reports if not path.is_file()]
    if missing_iqms:
        raise StageError("MRIQC completion has missing IQMs: " + _display(missing_iqms, config.paths.bids_dir))
    if invalid_iqms:
        raise StageError("MRIQC IQMs are malformed or BOLD provenance lacks fd_thres=0.5: " + _display(invalid_iqms, config.paths.bids_dir))
    if missing_reports:
        raise StageError("MRIQC completion has missing individual reports: " + _display(missing_reports, config.paths.bids_dir))
    missing_group = [
        modality for modality in sorted({_nifti_suffix(path) for path in images})
        if not (root / f"group_{modality}.html").is_file()
        or not (root / f"group_{modality}.tsv").is_file()
    ]
    if missing_group:
        raise StageError("MRIQC completion is missing group report/table pairs for: " + ", ".join(missing_group))
    bad_receipts = []
    for subject in config.subjects:
        path = receipt_path(root, "mriqc", subject)
        try:
            verify_subject_receipt(
                path,
                subject=subject,
                input_datalad_commit=input_commit,
                container=config.mriqc,
                invocation=mriqc_participant_command(config, subject),
            )
        except ValueError as error:
            bad_receipts.append(str(error))
    if bad_receipts:
        raise StageError("MRIQC completion has stale or missing subject receipts: " + "; ".join(bad_receipts[:3]))
    try:
        verify_subject_receipt(
            group_receipt_path(root, "mriqc"),
            subject="group",
            input_datalad_commit=input_commit,
            container=config.mriqc,
            invocation=mriqc_group_command(config),
        )
    except ValueError as error:
        raise StageError(f"MRIQC completion has stale or missing group receipt: {error}") from error
    crashes = _crash_files((root, config.paths.work_dir / "mriqc"))
    if crashes:
        raise StageError("MRIQC completion found crash evidence: " + _display(crashes, config.paths.bids_dir))
    return StageResult(
        "mriqc-complete", (root, root / "dataset_description.json"),
        {"subjects": len(config.subjects), "iqms": len(images), "reports": len(reports)},
    )


def _prefix(config: WorkflowConfig, worker: str) -> tuple[str, ...]:
    root = config.paths.bids_dir / "derivatives" / "mriqc"
    work = config.paths.work_dir / "mriqc" / worker
    prepare_bids_app_paths(root, work)
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


def _report_path(root: Path, raw: Path) -> Path:
    """MRIQC 24 writes per-image HTML reports at the derivative root."""

    stem = _stem(raw)
    if _nifti_suffix(raw) == "bold":
        stem = "_".join(entity for entity in stem.split("_") if not entity.startswith("echo-"))
    return root / f"{stem}.html"


def _nifti_suffix(path: Path) -> str | None:
    stem = _stem(path)
    return stem.rsplit("_", 1)[-1] if "_" in stem else None


def _stem(path: Path) -> str:
    return path.name.removesuffix(".nii.gz").removesuffix(".nii")


def _valid_iqm(path: Path, *, require_fd_threshold: bool) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return False
        if not require_fd_threshold:
            return True
        threshold = value["provenance"]["settings"]["fd_thres"]
        return (
            isinstance(threshold, (int, float))
            and not isinstance(threshold, bool)
            and threshold == 0.5
        )
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        return False


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
