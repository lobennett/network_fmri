import json
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
        validate_bids(tmp_path, "precuration", runner)

    result = raised.value.result
    report = tmp_path / "derivatives" / "bids-validator" / "desc-precuration_validation.json"
    log = report.with_suffix(".log")
    assert result.report == report
    assert result.log == log
    assert json.loads(report.read_text())["status"] == "validator-output-missing"
    assert "invalid BIDS" in log.read_text()
    assert runner.calls == [[
        "bids-validator", str(tmp_path), "--outfile", str(report),
        "--format", "json_pp", "--prune",
    ]]


def test_validator_success_preserves_validator_output(tmp_path):
    report = tmp_path / "derivatives" / "bids-validator" / "desc-curated_validation.json"

    class WritingRunner(Runner):
        def __call__(self, args, **kwargs):
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text('{"issues": {}}\n')
            return super().__call__(args, **kwargs)

    result = validate_bids(tmp_path, "curated", WritingRunner(stdout="valid"))

    assert result.returncode == 0
    assert json.loads(report.read_text()) == {"issues": {}}
    assert result.log.read_text() == "valid"


def test_validator_rejects_unsafe_label_without_writing(tmp_path):
    with pytest.raises(ValueError, match="label"):
        validate_bids(tmp_path, "../bad", Runner())
    assert not (tmp_path / "derivatives").exists()
