import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from network_fmri.qa.validate import ValidationError, validate_bids


def test_installed_bids_validator_deno_entrypoint_smoke():
    """The frozen Linux environment must expose the validator command we invoke."""

    executable = shutil.which("bids-validator-deno")
    if executable is None:
        pytest.skip("bids-validator-deno is installed only in the reviewed Linux environment")
    completed = subprocess.run([executable, "--version"], check=False, capture_output=True, text=True)
    assert completed.returncode == 0


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
    command, = runner.calls
    assert command[:3] == ["bids-validator-deno", str(tmp_path), "--outfile"]
    assert Path(command[3]).parent == report.parent
    assert Path(command[3]) != report
    assert command[4:] == ["--format", "json_pp", "--prune"]


def test_validator_success_preserves_validator_output(tmp_path):
    report = tmp_path / "derivatives" / "bids-validator" / "desc-curated_validation.json"

    class WritingRunner(Runner):
        def __call__(self, args, **kwargs):
            temporary_report = Path(args[args.index("--outfile") + 1])
            temporary_report.write_text('{"issues": {}}\n')
            return super().__call__(args, **kwargs)

    result = validate_bids(tmp_path, "curated", WritingRunner(stdout="valid"))

    assert result.returncode == 0
    assert json.loads(report.read_text()) == {"issues": {}}
    assert result.log.read_text() == "valid"


def test_validator_never_reuses_a_stale_report_when_new_output_is_missing(tmp_path):
    report = tmp_path / "derivatives" / "bids-validator" / "desc-curated_validation.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"old": true}\n')

    with pytest.raises(ValidationError):
        validate_bids(tmp_path, "curated", Runner(returncode=0))

    assert json.loads(report.read_text())["status"] == "validator-output-missing"


def test_validator_rejects_unsafe_label_without_writing(tmp_path):
    with pytest.raises(ValueError, match="label"):
        validate_bids(tmp_path, "../bad", Runner())
    assert not (tmp_path / "derivatives").exists()
