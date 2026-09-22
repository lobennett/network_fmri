import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from network_fmri.qa.validate import ValidationError, validate_bids


class Runner:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.calls = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, args, **kwargs):
        self.calls.append([str(value) for value in args])
        return SimpleNamespace(
            returncode=self.returncode, stdout=self.stdout, stderr=self.stderr
        )


def test_validator_failure_keeps_json_and_log_diagnostics(tmp_path):
    runner = Runner(returncode=1, stderr="invalid BIDS")

    with pytest.raises(ValidationError) as raised:
        validate_bids(tmp_path, "precuration", tmp_path / "validator.sif", runner)

    result = raised.value.result
    report = tmp_path / "derivatives" / "bids-validator" / "desc-precuration_validation.json"
    log = report.with_suffix(".log")
    assert result.report == report
    assert result.log == log
    assert json.loads(report.read_text())["status"] == "validator-output-missing"
    assert "invalid BIDS" in log.read_text()
    command, = runner.calls
    assert command[:3] == ["apptainer", "exec", "--bind"]
    assert command[3] == f"{tmp_path}:/data:ro"
    assert command[4:6] == ["--bind", f"{report.parent}:/out"]
    assert command[6:11] == [
        str(tmp_path / "validator.sif"), "deno", "-A", "/src/bids-validator.js", "/data",
    ]
    assert command[11] == "--outfile"
    assert command[12].startswith("/out/.desc-precuration_validation.json.")
    assert command[13:] == ["--format", "json_pp", "--prune"]


def test_validator_success_preserves_validator_output(tmp_path):
    report = tmp_path / "derivatives" / "bids-validator" / "desc-curated_validation.json"

    class WritingRunner(Runner):
        def __call__(self, args, **kwargs):
            name = Path(args[args.index("--outfile") + 1]).name
            temporary_report = report.parent / name
            temporary_report.write_text('{"issues": {}}\n')
            return super().__call__(args, **kwargs)

    result = validate_bids(
        tmp_path, "curated", tmp_path / "validator.sif", WritingRunner(stdout="valid"),
    )

    assert result.returncode == 0
    assert json.loads(report.read_text()) == {"issues": {}}
    assert result.log.read_text() == "valid"


def test_validator_never_reuses_a_stale_report_when_new_output_is_missing(tmp_path):
    report = tmp_path / "derivatives" / "bids-validator" / "desc-curated_validation.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"old": true}\n')

    with pytest.raises(ValidationError):
        validate_bids(tmp_path, "curated", tmp_path / "validator.sif", Runner(returncode=0))

    assert json.loads(report.read_text())["status"] == "validator-output-missing"


def test_validator_rejects_unsafe_label_without_writing(tmp_path):
    with pytest.raises(ValueError, match="label"):
        validate_bids(tmp_path, "../bad", tmp_path / "validator.sif", Runner())
    assert not (tmp_path / "derivatives").exists()
