"""Contracts for standalone FreeSurfer and its human review gate."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from network_fmri.config import (
    BehaviorSource, BehaviorSources, ContainerConfig, ParticipantsSource, SlurmConfig,
    VerifiedContainerConfig, WorkflowConfig, WorkflowPaths,
)
from network_fmri.containers import receipt_path, write_subject_receipt
from network_fmri.qa.freesurfer import (
    freesurfer_participant_command, freesurfer_subject_receipt, generate_surface_review,
    validate_surface_review, verify_freesurfer,
)
from network_fmri.stages import StageError


def configuration(tmp_path: Path) -> WorkflowConfig:
    subjects = tuple(f"s{number}" for number in range(1, 47))
    subjects_file = tmp_path / "subjects.txt"
    subjects_file.write_text("\n".join(subjects) + "\n")
    return WorkflowConfig(
        paths=WorkflowPaths(
            bids_dir=tmp_path / "bids", parts_dir=tmp_path / "parts",
            work_dir=tmp_path / "work", log_dir=tmp_path / "logs",
            templateflow_dir=tmp_path / "templateflow",
            freesurfer_license=tmp_path / "license.txt",
        ),
        subjects_file=subjects_file, subjects=subjects, flywheel_project="russpold/r01network",
        behavior=BehaviorSources(
            BehaviorSource(tmp_path / "behavior", "a" * 40),
            BehaviorSource(tmp_path / "out-of-scanner", "b" * 40),
        ),
        participants=ParticipantsSource(tmp_path / "demographics", "c" * 40),
        validator=ContainerConfig(tmp_path / "validator.sif", "3.0.1"),
        mriqc=ContainerConfig(tmp_path / "mriqc.sif", "24.0.2"),
        fmriprep=ContainerConfig(tmp_path / "fmriprep.sif", "25.2.5"),
        pydeface=VerifiedContainerConfig(tmp_path / "pydeface.sif", "2.1.0", "a" * 64),
        slurm=SlurmConfig("normal", 8, 32, 720, 4),
    )


class GitRunner:
    def __call__(self, args, **kwargs):
        assert args[:2] == ["git", "-C"]
        return type("Completed", (), {"stdout": "a" * 40 + "\n"})()


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _complete(config: WorkflowConfig) -> Path:
    root = config.paths.bids_dir / "derivatives" / "freesurfer"
    for subject in config.subjects:
        _write(config.paths.bids_dir / f"sub-{subject}/ses-01/anat/sub-{subject}_ses-01_T1w.nii.gz")
        for relative in (
            "surf/lh.white", "surf/rh.white", "surf/lh.pial", "surf/rh.pial",
            "stats/aseg.stats", "mri/brain.mgz", "scripts/recon-all.done",
        ):
            _write(root / f"sub-{subject}" / relative, "complete")
        write_subject_receipt(
            receipt_path(root, "freesurfer", subject),
            freesurfer_subject_receipt(config, subject, "a" * 40),
        )
    _write(root / "dataset_description.json", json.dumps({"DatasetType": "derivative"}))
    return root


def test_command_runs_recon_all_on_the_single_curated_t1w(tmp_path):
    config = configuration(tmp_path)
    t1w = _write(config.paths.bids_dir / "sub-s3/ses-13/anat/sub-s3_ses-13_T1w.nii.gz")

    command = freesurfer_participant_command(config, "s3")

    assert command[command.index(str(config.fmriprep.image)) + 1] == "recon-all"
    assert command[command.index("-i") + 1] == f"/data/{t1w.relative_to(config.paths.bids_dir)}"
    assert command[command.index("-s") + 1] == "sub-s3"
    assert command[command.index("-openmp") + 1] == "8"


def test_command_rejects_ambiguous_curated_t1w(tmp_path):
    config = configuration(tmp_path)
    _write(config.paths.bids_dir / "sub-s3/ses-01/anat/sub-s3_ses-01_T1w.nii.gz")
    _write(config.paths.bids_dir / "sub-s3/ses-02/anat/sub-s3_ses-02_T1w.nii.gz")

    with pytest.raises(StageError, match="exactly one"):
        freesurfer_participant_command(config, "s3")


def test_completion_requires_surfaces_and_current_receipts(tmp_path):
    config = configuration(tmp_path)
    root = _complete(config)

    result = verify_freesurfer(config, GitRunner())
    assert result.name == "freesurfer-complete"
    assert result.details["subjects"] == 46

    (root / "sub-s3/surf/lh.pial").unlink()
    with pytest.raises(StageError, match="missing required outputs"):
        verify_freesurfer(config, GitRunner())


def test_surface_review_requires_explicit_approval_for_every_subject(tmp_path):
    config = configuration(tmp_path)
    _complete(config)
    review = generate_surface_review(config)
    manifest = review.outputs[0]
    assert review.name == "surface-review-generated"

    with pytest.raises(StageError, match="not approved"):
        validate_surface_review(config)

    lines = manifest.read_text().splitlines()
    header = lines[0].split("\t")
    approved = header.index("approved")
    reviewer = header.index("reviewer")
    reviewed_at = header.index("reviewed_at")
    output = [lines[0]]
    for line in lines[1:]:
        values = line.split("\t")
        values[approved] = "yes"
        values[reviewer] = "reviewer"
        values[reviewed_at] = "2026-09-23T12:00:00Z"
        output.append("\t".join(values))
    manifest.write_text("\n".join(output) + "\n")

    result = validate_surface_review(config)
    assert result.name == "surface-review-approved"
    assert result.details["subjects"] == 46


def test_surface_review_rejects_non_object_metadata(tmp_path):
    config = configuration(tmp_path)
    _complete(config)
    result = generate_surface_review(config)
    result.outputs[1].write_text("[]\n")

    with pytest.raises(StageError, match="missing or malformed"):
        validate_surface_review(config)


def test_surface_review_uses_wrapper_study_when_mechababs_is_configured(tmp_path):
    from network_fmri.qa.freesurfer import surface_review_directory

    config = SimpleNamespace(
        mechababs=SimpleNamespace(study_dir=tmp_path / "study"),
        paths=SimpleNamespace(bids_dir=tmp_path / "raw"),
    )

    assert surface_review_directory(config) == tmp_path / "study" / "code" / "network_fmri"
