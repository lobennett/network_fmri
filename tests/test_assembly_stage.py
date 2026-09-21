import sys
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from network_fmri.config import (
    BehaviorSource,
    ContainerConfig,
    SlurmConfig,
    VerifiedContainerConfig,
    WorkflowConfig,
    WorkflowPaths,
)
from network_fmri.stages import StageError
from network_fmri.stages.assembly import assemble_dataset, convert_subject


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.roster_snapshot: list[str] | None = None
        self.assembled_subjects: tuple[str, ...] | None = None
        self.receipt_mutator = None

    def __call__(self, args, **kwargs):
        command = [str(value) for value in args]
        self.calls.append(command)
        if "--subjects" in command:
            self.roster_snapshot = Path(command[command.index("--subjects") + 1]).read_text().splitlines()
        if "--output" in command and self.assembled_subjects is not None:
            write_verified_receipts(Path(command[command.index("--output") + 1]), self.assembled_subjects)
            if self.receipt_mutator is not None:
                self.receipt_mutator()
        return SimpleNamespace(stdout="")


def configuration(tmp_path: Path) -> WorkflowConfig:
    paths = WorkflowPaths(
        bids_dir=tmp_path / "bids",
        parts_dir=tmp_path / "parts",
        work_dir=tmp_path / "work",
        log_dir=tmp_path / "logs",
        templateflow_dir=tmp_path / "templateflow",
        freesurfer_license=tmp_path / "license.txt",
    )
    subjects = tuple(f"s{number}" for number in range(1, 47))
    subjects_file = tmp_path / "subjects.txt"
    subjects_file.write_text("\n".join(subjects) + "\n")
    return WorkflowConfig(
        paths=paths,
        subjects_file=subjects_file,
        subjects=subjects,
        flywheel_project="russpold/r01network",
        behavior=BehaviorSource(tmp_path / "canonical-behavior", "a" * 40),
        mriqc=ContainerConfig(tmp_path / "mriqc.sif", "24.0.2"),
        fmriprep=ContainerConfig(tmp_path / "fmriprep.sif", "25.2.5"),
        pydeface=VerifiedContainerConfig(tmp_path / "pydeface.sif", "2.1.0", "a" * 64),
        slurm=SlurmConfig("normal", 8, 32, 720, 4),
    )


def option(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def write_verified_receipts(destination: Path, subjects: tuple[str, ...]) -> None:
    for subject in subjects:
        receipt = destination / "code" / "network_fw2bids" / "defacing" / f"sub-{subject}.json"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps({
            "schema_version": 1,
            "subject": subject,
            "status": "success",
            "software": {
                "name": "PyDeface",
                "version": "2.1.0",
                "container": "pydeface.sif",
                "sha256": "a" * 64,
            },
            "images": [{
                "path": f"sub-{subject}/ses-01/anat/sub-{subject}_ses-01_T1w.nii.gz",
                "input_sha256": "b" * 64,
                "output_sha256": "c" * 64,
                "shape": [2, 2, 2],
                "zooms": [1.0, 1.0, 1.0],
                "affine_sha256": "d" * 64,
            }],
        }))


def test_conversion_uses_environment_token_without_putting_it_on_command_line(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    runner = RecordingRunner()
    monkeypatch.setenv("FLYWHEEL_API_TOKEN", "private-token")

    result = convert_subject(config, "s3", runner)

    command = runner.calls[0]
    assert command[:1] == ["network-fw2bids"]
    assert "--subject" in command
    assert command[command.index("--subject") + 1] == "s3"
    assert "FLYWHEEL_API_TOKEN" not in " ".join(command)
    assert "private-token" not in " ".join(command)
    assert result.outputs == (tmp_path / "parts" / "s3",)


def test_conversion_passes_pinned_defacing_without_sensitive_paths(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    runner = RecordingRunner()
    monkeypatch.setenv("FLYWHEEL_API_TOKEN", "secret")
    monkeypatch.setenv("SLURM_TMPDIR", "/sensitive/node-local")

    convert_subject(config, "s3", runner)

    command = runner.calls[0]
    assert option(command, "--pydeface-image") == str(config.pydeface.image)
    assert option(command, "--pydeface-version") == "2.1.0"
    assert option(command, "--pydeface-sha256") == "a" * 64
    assert "secret" not in " ".join(command)
    assert "/sensitive/node-local" not in " ".join(command)


def test_conversion_rejects_a_subject_outside_the_exact_roster(tmp_path):
    with pytest.raises(StageError, match="46-subject roster"):
        convert_subject(configuration(tmp_path), "s999", RecordingRunner())


def test_assembly_uses_an_immutable_roster_snapshot_for_upstream_atomic_assembly(tmp_path):
    config = configuration(tmp_path)
    config.paths.parts_dir.mkdir()
    for subject in config.subjects:
        (config.paths.parts_dir / subject).mkdir()
    runner = RecordingRunner()
    runner.assembled_subjects = config.subjects

    result = assemble_dataset(config, runner)

    assert result.name == "bids-assembled"
    assert result.details["subject_count"] == 46
    command = runner.calls[0]
    assert command[:3] == [sys.executable, "-m", "network_fw2bids._assembly"]
    manifest = Path(command[command.index("--subjects") + 1])
    assert runner.roster_snapshot == list(config.subjects)
    assert manifest != config.subjects_file
    assert command[command.index("--parts") + 1] == str(config.paths.parts_dir)
    assert command[command.index("--output") + 1] == str(config.paths.bids_dir)
    assert not manifest.exists(), "the snapshot is removed after the assembly invocation"
    assert runner.calls[1] == ["datalad", "create", "-c", "text2git", "--force", str(config.paths.bids_dir)]


def test_assembled_result_records_verified_defacing(tmp_path):
    config = configuration(tmp_path)
    config.paths.parts_dir.mkdir()
    for subject in config.subjects:
        (config.paths.parts_dir / subject).mkdir()

    runner = RecordingRunner()
    runner.assembled_subjects = config.subjects
    result = assemble_dataset(config, runner)

    assert result.details["defacing"] == {
        "subjects": 46,
        "T1w": 46,
        "T2w": 0,
        "receipts": [f"code/network_fw2bids/defacing/sub-{subject}.json" for subject in config.subjects],
    }


def test_assembly_does_not_initialize_datalad_without_verified_defacing_receipts(tmp_path):
    config = configuration(tmp_path)
    config.paths.parts_dir.mkdir()
    for subject in config.subjects:
        (config.paths.parts_dir / subject).mkdir()
    runner = RecordingRunner()

    with pytest.raises(StageError, match="defacing receipt directory"):
        assemble_dataset(config, runner)

    assert len(runner.calls) == 1


def test_assembly_rejects_receipt_with_nonfinite_geometry(tmp_path):
    config = configuration(tmp_path)
    config.paths.parts_dir.mkdir()
    for subject in config.subjects:
        (config.paths.parts_dir / subject).mkdir()
    runner = RecordingRunner()
    runner.assembled_subjects = config.subjects

    def corrupt_receipt():
        receipt = config.paths.bids_dir / "code" / "network_fw2bids" / "defacing" / "sub-s1.json"
        value = json.loads(receipt.read_text())
        value["images"][0]["zooms"] = [float("nan"), 1.0, 1.0]
        receipt.write_text(json.dumps(value))

    runner.receipt_mutator = corrupt_receipt
    with pytest.raises(StageError, match="invalid image evidence"):
        assemble_dataset(config, runner)


def test_assembly_rejects_boolean_defacing_receipt_schema_version(tmp_path):
    config = configuration(tmp_path)
    config.paths.parts_dir.mkdir()
    for subject in config.subjects:
        (config.paths.parts_dir / subject).mkdir()
    runner = RecordingRunner()
    runner.assembled_subjects = config.subjects

    def corrupt_receipt():
        receipt = config.paths.bids_dir / "code" / "network_fw2bids" / "defacing" / "sub-s1.json"
        value = json.loads(receipt.read_text())
        value["schema_version"] = True
        receipt.write_text(json.dumps(value))

    runner.receipt_mutator = corrupt_receipt
    with pytest.raises(StageError, match="invalid schema"):
        assemble_dataset(config, runner)


def test_assembly_refuses_partial_or_extra_part_rosters(tmp_path):
    config = configuration(tmp_path)
    config.paths.parts_dir.mkdir()
    for subject in config.subjects[:-1]:
        (config.paths.parts_dir / subject).mkdir()
    (config.paths.parts_dir / "s999").mkdir()

    with pytest.raises(StageError, match="missing s46; unexpected s999"):
        assemble_dataset(config, RecordingRunner())


def test_assembly_rejects_a_directly_constructed_non_46_subject_config(tmp_path):
    config = configuration(tmp_path)
    object.__setattr__(config, "subjects", config.subjects[:-1])

    with pytest.raises(StageError, match="exactly 46"):
        assemble_dataset(config, RecordingRunner())
