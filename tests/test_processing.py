from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from network_fmri.config import MechaBABSAppConfig, MechaBABSConfig
from network_fmri.processing import ProcessingManager


SHORTS = ("MRIQC-24.0.2", "fMRIPrep-25.2.5+anat", "fMRIPrep-25.2.5+full")


class Runner:
    def __init__(self, status="pipeline\tstate\tjob_id\n", status_returncode=0):
        self.commands = []
        self.status = status
        self.status_returncode = status_returncode

    def __call__(self, command, **kwargs):
        command = tuple(str(value) for value in command)
        self.commands.append((command, kwargs))
        if command[:3] == ("git", "rev-parse", "HEAD"):
            cwd = str(kwargs.get("cwd", ""))
            stdout = ("d" * 40 if cwd.endswith("mechababs") else "e" * 40) + "\n"
        else:
            stdout = (
                self.status
                if command[0] != "git" and len(command) > 1 and command[1] == "status"
                else ""
            )
        returncode = self.status_returncode if command[0] != "git" and len(command) > 1 and command[1] == "status" else 0
        if returncode and kwargs.get("check"):
            raise subprocess.CalledProcessError(returncode, command, output=stdout)
        return SimpleNamespace(
            stdout=stdout,
            returncode=returncode,
        )


def setup(tmp_path: Path, values=(("", ""), ("", ""), ("", ""))):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    (campaign / ".venv/bin").mkdir(parents=True)
    (campaign / "code/mechababs").mkdir(parents=True)
    (campaign / "code/babs").mkdir()
    study = tmp_path / "study"
    raw = study / "sourcedata/raw"
    raw.mkdir(parents=True)
    columns = ["dataset_id", "study_url", "processing_level", "n_subjects", "n_sessions"]
    row = ["raw", str(study), "session", "2", "4"]
    for short, value in zip(SHORTS, values, strict=True):
        columns.extend((f"{short}_babs", f"{short}_babs-merged"))
        row.extend(value)
    (campaign / "desc-mechababs_datasets.tsv").write_text(
        "\t".join(columns) + "\n" + "\t".join(row) + "\n"
    )
    for root in (study, raw, campaign):
        (root / ".git").mkdir(exist_ok=True)
    mechababs = MechaBABSConfig(
        study_dir=study,
        campaign_dir=campaign,
        durable_sibling=tmp_path / "oak",
        bootstrap_script=tmp_path / "bootstrap.sh",
        campaign="network-v1",
        raw_slot="raw",
        container_dataset=tmp_path / "containers",
        mechababs_commit="d" * 40,
        babs_commit="e" * 40,
        mechababs_ref="sherlock-compat",
        babs_ref="fix/plus-regex-zipname",
        cluster_file=Path("sherlock.yaml"),
        apps=tuple(
            MechaBABSAppConfig(name, Path(file))
            for name, file in zip(
                ("mriqc", "anatomical", "fmriprep"),
                ("MRIQC-24.0.2.yaml", "fMRIPrep-25.2.5+anat.yaml", "fMRIPrep-25.2.5+full.yaml"),
                strict=True,
            )
        ),
    )
    config = SimpleNamespace(
        mechababs=mechababs,
        paths=SimpleNamespace(bids_dir=raw),
        subjects=("s01", "s02"),
    )
    return config


def test_plan_reports_ordered_stage_state(tmp_path):
    manager = ProcessingManager(setup(tmp_path, (("mriqc", "done"), ("", ""), ("", ""))), runner=Runner())

    plan = manager.plan()

    assert [(item.stage, item.state) for item in plan] == [
        ("mriqc", "complete"),
        ("anatomical", "ready"),
        ("fmriprep", "blocked"),
    ]


def test_status_refreshes_jobs_and_marks_failed_cell_for_intervention(tmp_path):
    runner = Runner("pipeline\tstate\tjob_id\nfMRIPrep-25.2.5+anat\tFAILED\t123\n")
    manager = ProcessingManager(
        setup(tmp_path, (("mriqc", "done"), ("anat", ""), ("", ""))), runner=runner
    )

    status = manager.status()

    assert status.stages[1].state == "intervention-required"
    assert status.jobs[0]["job_id"] == "123"
    assert any("status" in command and "--output" in command for command, _ in runner.commands)


def test_status_accepts_an_unscaffolded_campaign_with_no_job_table(tmp_path):
    manager = ProcessingManager(setup(tmp_path), runner=Runner(status="", status_returncode=1))

    status = manager.status()

    assert status.jobs == ()
    assert status.stages[0].state == "ready"


def test_advance_runs_one_reconciler_transition_for_requested_stage(tmp_path, monkeypatch):
    runner = Runner()
    config = setup(tmp_path)
    checked = []
    monkeypatch.setattr("network_fmri.processing.require_stage_gate", lambda *_args, **_kwargs: checked.append("mriqc"))
    manager = ProcessingManager(config, runner=runner)

    result = manager.advance("mriqc")

    assert result.stage == "mriqc"
    assert result.advanced is True
    assert checked == ["mriqc"]
    command = runner.commands[-1][0]
    assert command[-2:] == ("--batch", "1")


def test_advance_rejects_stage_until_previous_pipeline_is_merged(tmp_path, monkeypatch):
    monkeypatch.setattr("network_fmri.processing.require_stage_gate", lambda *_args, **_kwargs: None)
    manager = ProcessingManager(setup(tmp_path), runner=Runner())

    with pytest.raises(RuntimeError, match="mriqc must be complete"):
        manager.advance("anatomical")


def test_complete_stage_installs_derivative_in_canonical_study(tmp_path, monkeypatch):
    config = setup(tmp_path, (("studies/study-raw/derivatives/MRIQC-24.0.2", "done"), ("", ""), ("", "")))
    source = config.mechababs.campaign_dir / "studies/study-raw/derivatives/MRIQC-24.0.2"
    source.mkdir(parents=True)
    runner = Runner()
    monkeypatch.setattr("network_fmri.processing.require_stage_gate", lambda *_: None)

    result = ProcessingManager(config, runner=runner).advance("mriqc")

    assert result.advanced is False
    clone = next(command for command, _ in runner.commands if command[:2] == ("datalad", "clone"))
    assert clone == (
        "datalad", "clone", "-d", str(config.mechababs.study_dir), str(source),
        "derivatives/MRIQC-24.0.2",
    )


@pytest.mark.parametrize("stage", ["mriqc", "anatomical", "fmriprep"])
def test_advance_checks_the_named_gate(tmp_path, monkeypatch, stage):
    values = {
        "mriqc": (("", ""), ("", ""), ("", "")),
        "anatomical": (("mriqc", "done"), ("", ""), ("", "")),
        "fmriprep": (("mriqc", "done"), ("anat", "done"), ("", "")),
    }[stage]
    calls = []
    monkeypatch.setattr(
        "network_fmri.processing.require_stage_gate",
        lambda _config, requested, _runner: calls.append(requested),
    )

    ProcessingManager(setup(tmp_path, values), runner=Runner()).advance(stage)

    assert calls == [stage]


def test_advance_rejects_dirty_study_before_gate_or_submission(tmp_path, monkeypatch):
    class DirtyRunner(Runner):
        def __call__(self, command, **kwargs):
            result = super().__call__(command, **kwargs)
            if tuple(command[:3]) == ("git", "status", "--porcelain"):
                return SimpleNamespace(stdout=" M changed\n")
            return result

    called = []
    monkeypatch.setattr("network_fmri.processing.require_stage_gate", lambda *_: called.append(True))

    with pytest.raises(RuntimeError, match="is dirty"):
        ProcessingManager(setup(tmp_path), runner=DirtyRunner()).advance("mriqc")
    assert called == []
