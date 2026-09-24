from pathlib import Path
from types import SimpleNamespace

import pytest

from network_fmri.config import MechaBABSAppConfig, MechaBABSConfig
from network_fmri.study import StudyManager


class Runner:
    def __init__(self, *, raw_commit="a" * 40, raw_id="raw-dataset-id"):
        self.commands = []
        self.raw_commit = raw_commit
        self.raw_id = raw_id

    def __call__(self, command, **kwargs):
        command = tuple(str(value) for value in command)
        self.commands.append((command, kwargs.get("cwd")))
        if command[:3] == ("git", "status", "--porcelain"):
            return SimpleNamespace(stdout="")
        if command[:3] == ("git", "rev-parse", "HEAD"):
            return SimpleNamespace(stdout=self.raw_commit + "\n")
        if command[:4] == ("git", "config", "--get", "datalad.dataset.id"):
            return SimpleNamespace(stdout=self.raw_id + "\n")
        return SimpleNamespace(stdout="")


def mechababs(tmp_path: Path) -> MechaBABSConfig:
    bootstrap = tmp_path / "mechababs" / "bootstrap.sh"
    bootstrap.parent.mkdir()
    bootstrap.write_text("#!/bin/bash\n")
    return MechaBABSConfig(
        study_dir=tmp_path / "study",
        campaign_dir=tmp_path / "campaign",
        durable_sibling=tmp_path / "oak-study",
        bootstrap_script=bootstrap,
        campaign="network-v1",
        raw_slot="raw",
        container_dataset=tmp_path / "containers",
        mechababs_commit="d" * 40,
        babs_commit="e" * 40,
        cluster_file=Path("sherlock.yaml"),
        apps=(
            MechaBABSAppConfig("mriqc", Path("MRIQC-24.0.2.yaml")),
            MechaBABSAppConfig("anatomical", Path("fMRIPrep-25.2.5+anat.yaml")),
            MechaBABSAppConfig("fmriprep", Path("fMRIPrep-25.2.5+full.yaml")),
        ),
    )


def raw_dataset(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    (raw / ".datalad").mkdir(parents=True)
    (raw / "dataset_description.json").write_text('{}\n')
    for subject in ("s01", "s02"):
        for session in ("01", "02"):
            anat = raw / f"sub-{subject}" / f"ses-{session}" / "anat"
            func = raw / f"sub-{subject}" / f"ses-{session}" / "func"
            anat.mkdir(parents=True)
            func.mkdir()
            (anat / f"sub-{subject}_ses-{session}_T1w.nii.gz").write_bytes(b"x")
            (func / f"sub-{subject}_ses-{session}_task-rest_bold.nii.gz").write_bytes(b"x")
    return raw


def test_initialize_creates_study_metadata_raw_slot_campaign_and_sibling(tmp_path):
    runner = Runner()
    manager = StudyManager(mechababs(tmp_path), raw_dataset(tmp_path), runner=runner)

    result = manager.initialize(subjects=("s01", "s02"))

    assert result.created is True
    assert result.raw_commit == "a" * 40
    assert (result.study_dir / "dataset_description.json").is_file()
    sessions = (result.study_dir / "sourcedata/sourcedata+subjects+sessions.tsv").read_text()
    assert sessions.splitlines()[0] == "subject_id\tsession_id\tdatatypes\tt1w_num\tbold_num"
    assert "s01\t01\tanat,func\t1\t1" in sessions
    commands = [command for command, _ in runner.commands]
    assert any(command[:3] == ("datalad", "create", "-c") for command in commands)
    assert any(command[:2] == ("datalad", "clone") and "sourcedata/raw" in command for command in commands)
    assert any(command[:2] == ("datalad", "create-sibling") for command in commands)
    assert any(command[0:2] == ("bash", str(manager.config.bootstrap_script)) for command in commands)
    assert any(command[-2:] == ("--processing-level", "session") for command in commands)


def test_initialize_matching_rerun_is_a_noop(tmp_path):
    runner = Runner()
    manager = StudyManager(mechababs(tmp_path), raw_dataset(tmp_path), runner=runner)
    first = manager.initialize(subjects=("s01", "s02"))
    runner.commands.clear()

    second = manager.initialize(subjects=("s01", "s02"))

    assert first.study_id == second.study_id
    assert second.created is False
    assert not any(command[0] in {"datalad", "bash"} for command, _ in runner.commands)


def test_initialize_rejects_dirty_raw_dataset(tmp_path):
    class DirtyRunner(Runner):
        def __call__(self, command, **kwargs):
            result = super().__call__(command, **kwargs)
            if tuple(command[:3]) == ("git", "status", "--porcelain"):
                return SimpleNamespace(stdout=" M changed\n")
            return result

    with pytest.raises(RuntimeError, match="raw BIDS dataset is dirty"):
        StudyManager(mechababs(tmp_path), raw_dataset(tmp_path), runner=DirtyRunner()).initialize(
            subjects=("s01", "s02")
        )


@pytest.mark.parametrize("field", ["raw_commit", "raw_dataset_id", "subjects"])
def test_initialize_rejects_conflicting_existing_identity(tmp_path, field):
    runner = Runner()
    manager = StudyManager(mechababs(tmp_path), raw_dataset(tmp_path), runner=runner)
    manager.initialize(subjects=("s01", "s02"))
    manifest = manager.manifest_path
    text = manifest.read_text()
    replacement = {
        "raw_commit": '"raw_commit": "' + "b" * 40 + '"',
        "raw_dataset_id": '"raw_dataset_id": "other"',
        "subjects": '"subjects": [\n    "s99"\n  ]',
    }[field]
    if field == "raw_commit":
        text = text.replace('"raw_commit": "' + "a" * 40 + '"', replacement)
    elif field == "raw_dataset_id":
        text = text.replace('"raw_dataset_id": "raw-dataset-id"', replacement)
    else:
        start = text.index('"subjects": [')
        end = text.index("  ]", start) + 3
        text = text[:start] + replacement + text[end:]
    manifest.write_text(text)

    with pytest.raises(RuntimeError, match="existing study does not match"):
        manager.initialize(subjects=("s01", "s02"))


def test_pilot_metadata_contains_only_selected_subject(tmp_path):
    manager = StudyManager(mechababs(tmp_path), raw_dataset(tmp_path), runner=Runner())

    result = manager.initialize(subjects=("s01",))

    sessions = (result.study_dir / "sourcedata/sourcedata+subjects+sessions.tsv").read_text()
    assert "s01\t" in sessions
    assert "s02\t" not in sessions
