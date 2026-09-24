"""Generate and validate the human review of MechaBABS surface outputs."""

from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from pathlib import Path

from network_fmri.config import WorkflowConfig
from network_fmri.models import StageResult
from network_fmri.stages import StageError

REVIEW_COLUMNS = (
    "subject", "surface_dir", "status", "surface_fingerprint", "approved", "reviewer",
    "reviewed_at", "notes",
)
REQUIRED_OUTPUTS = (
    "surf/lh.white", "surf/rh.white", "surf/lh.pial", "surf/rh.pial",
    "stats/aseg.stats", "mri/brain.mgz", "scripts/recon-all.done",
)


def generate_surface_review(
    config: WorkflowConfig, anatomical_derivative: Path
) -> StageResult:
    """Regenerate the checklist from the installed MechaBABS anatomical result."""

    derivative = Path(anatomical_derivative).resolve()
    if not derivative.is_dir():
        raise StageError(f"anatomical derivative does not exist: {derivative}")
    root = surface_review_directory(config)
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "surface_review.tsv"
    evidence = surface_fingerprints(config, derivative)
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=REVIEW_COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for subject in config.subjects:
            artifacts = _subject_artifacts(derivative, subject)
            if len(artifacts) > 1:
                raise StageError(
                    f"anatomical derivative has multiple artifacts for sub-{subject}: "
                    + ", ".join(map(str, artifacts))
                )
            fingerprint = evidence.get(f"sub-{subject}")
            writer.writerow({
                "subject": f"sub-{subject}",
                "surface_dir": str(artifacts[0]) if artifacts else "",
                "status": "complete" if fingerprint else "missing",
                "surface_fingerprint": fingerprint or "",
                "approved": "no", "reviewer": "", "reviewed_at": "", "notes": "",
            })
    metadata = manifest.with_suffix(".meta.json")
    metadata.write_text(json.dumps({
        "schema_version": 1,
        "subjects": [f"sub-{subject}" for subject in config.subjects],
        "surface_root": str(derivative),
    }, indent=2) + "\n")
    return StageResult(
        "surface-review-generated", (manifest, metadata), {"subjects": len(config.subjects)},
    )


def surface_fingerprints(
    config: WorkflowConfig, anatomical_derivative: Path
) -> dict[str, str]:
    """Return verified, content-derived surface evidence by BIDS subject."""

    derivative = Path(anatomical_derivative).resolve()
    evidence = {}
    for subject in config.subjects:
        artifacts = _subject_artifacts(derivative, subject)
        if len(artifacts) > 1:
            raise StageError(f"anatomical derivative has multiple artifacts for sub-{subject}")
        if artifacts:
            fingerprint = _surface_fingerprint(artifacts[0], subject)
            if fingerprint:
                evidence[f"sub-{subject}"] = fingerprint
    return evidence


def validate_surface_review(
    config: WorkflowConfig, manifest: Path | None = None
) -> StageResult:
    """Require an explicit named approval for every expected subject."""

    manifest = Path(manifest) if manifest is not None else surface_review_directory(config) / "surface_review.tsv"
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


def surface_review_directory(config: WorkflowConfig) -> Path:
    """Place cross-derivative review records in the wrapper study."""

    root = config.mechababs.study_dir if config.mechababs is not None else config.paths.bids_dir
    return root / "code" / "network_fmri"


def _subject_artifacts(root: Path, subject: str) -> tuple[Path, ...]:
    prefix = f"sub-{subject}"
    matches = {
        path.resolve() for path in root.rglob(f"{prefix}*")
        if path.name == prefix or path.name.startswith(prefix + "_")
    }
    # A subject directory represents the artifact; omit its descendants.
    return tuple(sorted(
        path for path in matches
        if not any(parent in matches for parent in path.parents if parent != root)
    ))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _surface_fingerprint(artifact: Path, subject: str) -> str | None:
    digest = hashlib.sha256()
    if artifact.is_dir():
        subject_root = artifact if artifact.name == f"sub-{subject}" else artifact / f"sub-{subject}"
        paths = [subject_root / relative for relative in REQUIRED_OUTPUTS]
        if any(not path.is_file() for path in paths):
            return None
        for relative, path in zip(REQUIRED_OUTPUTS, paths, strict=True):
            digest.update(relative.encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()
    if not artifact.is_file() or artifact.suffix != ".zip":
        return None
    try:
        with zipfile.ZipFile(artifact) as archive:
            names = archive.namelist()
            for relative in REQUIRED_OUTPUTS:
                matches = [
                    name for name in names
                    if f"sub-{subject}/" in name and name.endswith("/" + relative)
                ]
                if len(matches) != 1:
                    return None
                info = archive.getinfo(matches[0])
                digest.update(relative.encode())
                digest.update(f"{info.CRC}:{info.file_size}".encode())
    except (OSError, zipfile.BadZipFile):
        return None
    return digest.hexdigest()
