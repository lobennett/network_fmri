import json
import subprocess
from types import SimpleNamespace

import pytest

from network_fmri import pipeline
from network_fmri.registry import (
    PipelineContext,
    StageRegistry,
    builtin_stages,
)


def test_print_plan_writes_optional_dry_run_record_only(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.provenance, "code_revision", lambda: "b" * 40)
    monkeypatch.setattr(pipeline.provenance, "code_is_dirty", lambda: False)
    staging = tmp_path / "staging"
    record_path = tmp_path / "plan.json"
    result = pipeline.main(
        [
            "--cohort",
            "discovery",
            "--staging",
            str(staging),
            "--print",
            "--no-extensions",
            "--plan-json",
            str(record_path),
        ]
    )
    assert result == 0
    assert not (staging / "logs").exists()

    record = json.loads(record_path.read_text())
    assert record["schema_version"] == 1
    assert record["status"] == "dry-run"
    assert record["subjects"] == ["s03", "s10", "s19", "s29", "s43"]
    assert len(record["code"]["revision"]) == 40
    assert [stage["name"] for stage in record["stages"]][-2:] == [
        "validate-post",
        "check",
    ]
    trim = next(stage for stage in record["stages"] if stage["name"] == "trim")
    assert trim["inputs"][0]["name"] == "validated-pre"
    assert trim["outputs"][0]["name"] == "trimmed-bids"
    assert trim["job_id"] is None


def test_resumed_submission_records_exact_jobs_and_dependencies(tmp_path, monkeypatch):
    submitted = []
    next_job = iter(range(100, 107))

    def fake_run(command, **kwargs):
        submitted.append(command)
        return SimpleNamespace(stdout=f"Submitted batch job {next(next_job)}\n")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(pipeline.provenance, "code_revision", lambda: "a" * 40)
    monkeypatch.setattr(pipeline.provenance, "code_is_dirty", lambda: False)
    record_path = tmp_path / "submitted.json"
    result = pipeline.main(
        [
            "--cohort",
            "discovery",
            "--staging",
            str(tmp_path / "staging"),
            "--from",
            "trim",
            "--no-extensions",
            "--plan-json",
            str(record_path),
        ]
    )
    assert result == 0
    assert len(submitted) == 7
    assert not any(item.startswith("--dependency=") for item in submitted[0])
    assert "--dependency=afterok:100" in submitted[1]

    record = json.loads(record_path.read_text())
    assert record["status"] == "submitted"
    assert record["stages"][0]["name"] == "trim"
    assert record["stages"][0]["job_id"] == "100"
    assert record["stages"][1]["dependency_job_ids"] == ["100"]
    assert record["stages"][-1]["job_id"] == "106"
    assert record["stages"][-1]["submission_command"][0] == "sbatch"


def test_partial_submission_failure_is_recorded(tmp_path, monkeypatch):
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise subprocess.CalledProcessError(1, command, stderr="scheduler error")
        return SimpleNamespace(stdout="Submitted batch job 100\n")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    monkeypatch.setattr(pipeline.provenance, "code_revision", lambda: "a" * 40)
    monkeypatch.setattr(pipeline.provenance, "code_is_dirty", lambda: False)
    record_path = tmp_path / "failed.json"

    with pytest.raises(subprocess.CalledProcessError):
        pipeline.main(
            [
                "--cohort",
                "discovery",
                "--staging",
                str(tmp_path / "staging"),
                "--from",
                "trim",
                "--no-extensions",
                "--plan-json",
                str(record_path),
            ]
        )

    record = json.loads(record_path.read_text())
    assert record["status"] == "failed"
    assert record["error"].startswith("CalledProcessError:")
    assert record["stages"][0]["job_id"] == "100"
    assert record["stages"][1]["job_id"] is None
    assert record["stages"][1]["submission_command"][0] == "sbatch"


def test_sbatch_supports_multiple_afterok_dependencies(tmp_path):
    context = PipelineContext(
        cohort="discovery",
        staging=str(tmp_path),
        network_fmri_bin="/venv/network_fmri",
        events_bin="/venv/network-events",
        project="r01network",
        partition="normal",
        throttle=3,
        live=False,
    )
    stage = StageRegistry(builtin_stages()).plan(context, start="b0link")[0]
    args = SimpleNamespace(cohort="discovery", partition="normal")
    command = pipeline._command_sbatch(stage, args, tmp_path / "logs", ["41", "42"])
    assert "--dependency=afterok:41:42" in command


def test_export_array_receives_its_declared_resources(tmp_path, monkeypatch):
    from network_fmri.fw2bids import jobs

    context = PipelineContext(
        cohort="discovery",
        staging=str(tmp_path),
        network_fmri_bin="/venv/network_fmri",
        events_bin="/venv/network-events",
        project="r01network",
        partition="normal",
        throttle=3,
        live=True,
    )
    export = StageRegistry(builtin_stages()).plan(context)[0]
    command = pipeline._export_submission_command(export)
    assert command[-6:] == [
        "--cpus",
        "2",
        "--mem-gb",
        "8",
        "--time",
        "08:00:00",
    ]
    assert "--live" in command

    captured = {}

    def fake_sbatch_array(args):
        captured["args"] = args
        return "12345"

    monkeypatch.setattr(jobs, "sbatch_array", fake_sbatch_array)
    assert pipeline._submit_export(export) == "12345"
    assert captured["args"].cpus == 2
    assert captured["args"].mem_gb == 8
    assert captured["args"].time == "08:00:00"
