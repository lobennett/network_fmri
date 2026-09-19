import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        command = [str(argument) for argument in args]
        self.calls.append(command)
        if command[:2] == ["git", "-C"]:
            return SimpleNamespace(stdout="abc123\n")
        return SimpleNamespace(stdout="")


def receipt(stage: str):
    from network_fmri.milestones import MilestoneReceipt

    return MilestoneReceipt(
        stage=stage,
        status="success",
        inputs={"bids": "raw-commit"},
        outputs={"dataset": "bids"},
        versions={"network_fmri": "a" * 40},
        jobs={"assemble": "12345"},
        validation={"bids": "valid"},
    )


def test_save_milestone_writes_complete_receipt_and_one_explicit_save(tmp_path):
    from network_fmri.milestones import save_milestone

    runner = RecordingRunner()
    commit = save_milestone(tmp_path, receipt("bids-assembled"), runner)

    assert commit == "abc123"
    assert runner.calls == [
        ["datalad", "save", "-d", str(tmp_path), "-m", "bids-assembled"],
        ["git", "-C", str(tmp_path), "rev-parse", "--verify", "HEAD"],
    ]
    assert json.loads(
        (tmp_path / "code" / "network_fmri" / "milestones" / "bids-assembled.json").read_text()
    ) == {
        "inputs": {"bids": "raw-commit"},
        "jobs": {"assemble": "12345"},
        "outputs": {"dataset": "bids"},
        "stage": "bids-assembled",
        "status": "success",
        "validation": {"bids": "valid"},
        "versions": {"network_fmri": "a" * 40},
    }


def test_save_milestone_rejects_the_configured_token_before_writing(tmp_path, monkeypatch):
    from network_fmri.milestones import MilestoneReceipt, save_milestone

    monkeypatch.setenv("FLYWHEEL_API_TOKEN", "secret-value")
    runner = RecordingRunner()
    unsafe = MilestoneReceipt(
        stage="stage",
        status="success",
        inputs={"credential": "secret-value"},
        outputs={},
        versions={},
        jobs={},
        validation={},
    )

    with pytest.raises(ValueError, match="credential"):
        save_milestone(tmp_path, unsafe, runner)

    assert runner.calls == []
    assert not list(tmp_path.rglob("*"))


def test_save_milestone_rejects_a_non_success_receipt(tmp_path):
    from network_fmri.milestones import save_milestone

    runner = RecordingRunner()

    with pytest.raises(ValueError, match="success"):
        save_milestone(tmp_path, replace(receipt("stage"), status="failed"), runner)

    assert runner.calls == []
    assert not list(tmp_path.rglob("*"))


def test_save_diagnostic_saves_only_supplied_paths_without_a_receipt(tmp_path):
    from network_fmri.milestones import save_diagnostic

    runner = RecordingRunner()
    log = tmp_path / "logs" / "failed.log"
    log.parent.mkdir()
    log.write_text("failed\n")

    commit = save_diagnostic(tmp_path, "mriqc", [log], runner)

    assert commit == "abc123"
    assert runner.calls == [
        [
            "datalad",
            "save",
            "-d",
            str(tmp_path),
            "-m",
            "mriqc-failed-diagnostics",
            str(log),
        ],
        ["git", "-C", str(tmp_path), "rev-parse", "--verify", "HEAD"],
    ]
    assert not (tmp_path / "code" / "network_fmri" / "milestones" / "mriqc.json").exists()


def test_save_diagnostic_rejects_a_path_containing_the_configured_token(tmp_path, monkeypatch):
    from network_fmri.milestones import save_diagnostic

    monkeypatch.setenv("FLYWHEEL_API_TOKEN", "secret-value")
    runner = RecordingRunner()

    with pytest.raises(ValueError, match="credential"):
        save_diagnostic(tmp_path, "mriqc", [tmp_path / "secret-value.log"], runner)

    assert runner.calls == []
