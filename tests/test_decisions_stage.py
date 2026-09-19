from types import SimpleNamespace

import pytest

from network_fmri.stages import StageError
from network_fmri.stages.decisions import generate_decisions, validate_decisions


class Runner:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, args, **kwargs):
        command = [str(value) for value in args]
        self.calls.append(command)
        if self.fail:
            raise __import__("subprocess").CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)


def test_generate_decisions_delegates_evidence_compilation_to_network_qa(tmp_path):
    runner = Runner()

    result = generate_decisions(tmp_path, runner)

    manifest = tmp_path / "code" / "network_fmri" / "scan_decisions.tsv"
    assert result.name == "scan-decisions-generated"
    assert result.outputs == (manifest, manifest.with_suffix(".meta.json"))
    assert runner.calls == [[
        "network-qa", "decisions", "generate", "--bids-dir", str(tmp_path),
        "--mriqc-dir", str(tmp_path / "derivatives" / "mriqc"),
        "--output", str(manifest),
    ]]


def test_validate_decisions_seals_the_reviewed_manifest(tmp_path):
    runner = Runner()
    manifest = tmp_path / "code" / "network_fmri" / "scan_decisions.tsv"

    result = validate_decisions(tmp_path, runner)

    assert result.name == "scan-decisions-approved"
    assert runner.calls == [[
        "network-qa", "decisions", "approve", "--manifest", str(manifest),
        "--metadata", str(manifest.with_suffix(".meta.json")), "--bids-dir", str(tmp_path),
    ]]


def test_decision_command_failures_are_stage_errors(tmp_path):
    with pytest.raises(StageError, match="scan-decision"):
        generate_decisions(tmp_path, Runner(fail=True))
