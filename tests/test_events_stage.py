from types import SimpleNamespace

import pytest

from network_fmri.stages import StageError
from network_fmri.stages.events import generate_events


class Runner:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, args, **kwargs):
        self.calls.append([str(value) for value in args])
        if self.fail:
            raise __import__("subprocess").CalledProcessError(2, args)
        return SimpleNamespace()


def test_events_uses_audited_create_against_canonical_sourcedata(tmp_path):
    behavioral = tmp_path / "sourcedata" / "behavioral" / "in_scanner"
    behavioral.mkdir(parents=True)
    runner = Runner()

    result = generate_events(tmp_path, runner)

    assert result.name == "bids-events-generated"
    assert result.outputs == (tmp_path,)
    assert result.details["conversion_errors"] == str(
        tmp_path / "sourcedata" / "events_qc" / "conversion_errors.tsv"
    )
    assert runner.calls == [[
        "network-events", "create", "--bids-dir", str(tmp_path),
        "--behavioral-dir", str(behavioral),
    ]]


def test_events_requires_canonical_behavioral_sourcedata(tmp_path):
    with pytest.raises(StageError, match="canonical behavioral"):
        generate_events(tmp_path, Runner())


def test_events_reports_audit_or_incomplete_conversion_failure(tmp_path):
    (tmp_path / "sourcedata" / "behavioral" / "in_scanner").mkdir(parents=True)
    with pytest.raises(StageError, match="audit or conversion"):
        generate_events(tmp_path, Runner(fail=True))
