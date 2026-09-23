"""Standalone FreeSurfer execution, verification, and surface review."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from pathlib import Path

from network_fmri.config import WorkflowConfig
from network_fmri.containers import (
    apptainer_prefix, bind, current_datalad_commit, job_tmpdir, prepare_bids_app_paths,
    receipt_path, subject_receipt, verify_subject_receipt,
)
from network_fmri.models import Runner, StageResult
from network_fmri.stages import StageError

REVIEW_COLUMNS = (
    "subject", "surface_dir", "status", "approved", "reviewer", "reviewed_at", "notes",
)
REQUIRED_OUTPUTS = (
    "surf/lh.white", "surf/rh.white", "surf/lh.pial", "surf/rh.pial",
    "stats/aseg.stats", "mri/brain.mgz", "scripts/recon-all.done",
)


def freesurfer_participant_command(config: WorkflowConfig, subject: str) -> tuple[str, ...]:
    """Build one standalone recon-all call from the curated T1w image."""

    if subject not in config.subjects:
        raise StageError(f"FreeSurfer subject {subject!r} is outside the exact roster")
    images = tuple(sorted(
        path for path in (config.paths.bids_dir / f"sub-{subject}").glob("ses-*/anat/*_T1w.nii*")
        if path.is_file()
    ))
    if len(images) != 1:
        raise StageError(
            f"FreeSurfer requires exactly one curated T1w for {subject}; found {len(images)}"
        )
    root = config.paths.bids_dir / "derivatives" / "freesurfer"
    work = config.paths.work_dir / "freesurfer" / subject
    prepare_bids_app_paths(root, work)
    prefix = apptainer_prefix(
        config.fmriprep,
        binds=(
            bind(config.paths.bids_dir, "/data", read_only=True),
            bind(root, "/subjects"), bind(work, "/work"),
            bind(config.paths.freesurfer_license, "/license.txt", read_only=True),
            bind(job_tmpdir(), "/tmp"),
        ),
        environment=(("FS_LICENSE", "/license.txt"), ("SUBJECTS_DIR", "/subjects")),
    )
    image = "/data/" + images[0].relative_to(config.paths.bids_dir).as_posix()
    return prefix + (
        "recon-all", "-sd", "/subjects", "-s", f"sub-{subject}", "-i", image,
        "-all", "-parallel", "-openmp", str(config.slurm.cpus),
    )


def freesurfer_subject_receipt(
    config: WorkflowConfig, subject: str, input_commit: str,
) -> dict[str, object]:
    return subject_receipt(
        subject=subject,
        input_datalad_commit=input_commit,
        container=config.fmriprep,
        invocation=freesurfer_participant_command(config, subject),
    )


def write_freesurfer_description(config: WorkflowConfig) -> Path:
    root = config.paths.bids_dir / "derivatives" / "freesurfer"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "dataset_description.json"
    path.write_text(json.dumps({
        "Name": "FreeSurfer cortical reconstruction",
        "BIDSVersion": "1.10.0",
        "DatasetType": "derivative",
        "GeneratedBy": [{"Name": "FreeSurfer", "Container": {
            "Type": "apptainer", "Tag": str(config.fmriprep.image),
        }}],
    }, indent=2) + "\n")
    return path


def verify_freesurfer(
    config: WorkflowConfig, runner: Runner = subprocess.run,
) -> StageResult:
    """Require complete standalone surfaces and current worker receipts."""

    root = config.paths.bids_dir / "derivatives" / "freesurfer"
    description = root / "dataset_description.json"
    try:
        value = json.loads(description.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StageError(f"FreeSurfer derivative description is missing or malformed: {description}") from error
    if not isinstance(value, dict) or value.get("DatasetType") != "derivative":
        raise StageError(f"FreeSurfer derivative description is not a BIDS derivative: {description}")
    missing = [
        f"sub-{subject}/{relative}"
        for subject in config.subjects for relative in REQUIRED_OUTPUTS
        if not _nonempty(root / f"sub-{subject}" / relative)
    ]
    if missing:
        raise StageError("FreeSurfer completion has missing required outputs: " + _display(missing))
    try:
        input_commit = current_datalad_commit(config.paths.bids_dir, runner)
    except ValueError as error:
        raise StageError(str(error)) from error
    bad_receipts = []
    for subject in config.subjects:
        try:
            verify_subject_receipt(
                receipt_path(root, "freesurfer", subject), subject=subject,
                input_datalad_commit=input_commit, container=config.fmriprep,
                invocation=freesurfer_participant_command(config, subject),
            )
        except ValueError as error:
            bad_receipts.append(str(error))
    if bad_receipts:
        raise StageError(
            "FreeSurfer completion has stale or missing subject receipts: "
            + "; ".join(bad_receipts[:3])
        )
    crashes = tuple(sorted(
        path for path in (config.paths.work_dir / "freesurfer").rglob("crash*")
        if path.is_file() or path.is_dir()
    )) if (config.paths.work_dir / "freesurfer").is_dir() else ()
    if crashes:
        raise StageError("FreeSurfer completion found crash evidence: " + _display(map(str, crashes)))
    return StageResult("freesurfer-complete", (root, description), {"subjects": len(config.subjects)})


def generate_surface_review(config: WorkflowConfig) -> StageResult:
    """Create the subject-level checklist that must be reviewed before fMRIPrep."""

    root = config.paths.bids_dir / "code" / "network_fmri"
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "surface_review.tsv"
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for subject in config.subjects:
            writer.writerow({
                "subject": f"sub-{subject}",
                "surface_dir": f"derivatives/freesurfer/sub-{subject}",
                "status": "complete", "approved": "no", "reviewer": "",
                "reviewed_at": "", "notes": "",
            })
    metadata = manifest.with_suffix(".meta.json")
    metadata.write_text(json.dumps({
        "schema_version": 1,
        "subjects": [f"sub-{subject}" for subject in config.subjects],
        "surface_root": "derivatives/freesurfer",
    }, indent=2) + "\n")
    return StageResult(
        "surface-review-generated", (manifest, metadata), {"subjects": len(config.subjects)},
    )


def validate_surface_review(config: WorkflowConfig) -> StageResult:
    """Require an explicit named approval for every expected subject."""

    manifest = config.paths.bids_dir / "code" / "network_fmri" / "surface_review.tsv"
    metadata = manifest.with_suffix(".meta.json")
    try:
        with manifest.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t", strict=True)
            if tuple(reader.fieldnames or ()) != REVIEW_COLUMNS:
                raise StageError("surface review has an invalid header")
            rows = list(reader)
        value = json.loads(metadata.read_text())
        if not isinstance(value, dict):
            raise StageError("surface review is missing or malformed")
    except StageError:
        raise
    except (OSError, UnicodeError, csv.Error, json.JSONDecodeError) as error:
        raise StageError("surface review is missing or malformed") from error
    expected = [f"sub-{subject}" for subject in config.subjects]
    if [row.get("subject") for row in rows] != expected or value.get("subjects") != expected:
        raise StageError("surface review does not match the exact subject roster")
    invalid = [
        row["subject"] for row in rows
        if row.get("status") != "complete" or row.get("approved") != "yes"
        or not row.get("reviewer", "").strip() or not row.get("reviewed_at", "").strip()
    ]
    if invalid:
        raise StageError("surface review is not approved for: " + ", ".join(invalid))
    return StageResult(
        "surface-review-approved", (manifest, metadata),
        {"subjects": len(rows), "manifest_sha256": _sha256(manifest),
         "metadata_sha256": _sha256(metadata)},
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _display(values, limit: int = 8) -> str:
    items = list(values)
    suffix = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return ", ".join(items[:limit]) + suffix
