import json
from types import SimpleNamespace

import pytest

from network_fmri.stages import StageError
from network_fmri.stages.global_signal import run_global_signal


class Runner:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, args, **kwargs):
        self.calls.append([str(value) for value in args])
        if self.fail:
            raise __import__("subprocess").CalledProcessError(1, args)
        return SimpleNamespace()


def test_global_signal_creates_bids_derivative_and_runs_pinned_cli(tmp_path):
    runner = Runner()

    result = run_global_signal(tmp_path, "pretrim", runner)

    root = tmp_path / "derivatives" / "gs-pretrim"
    description = json.loads((root / "dataset_description.json").read_text())
    assert result.name == "gs-pretrim"
    assert result.outputs == (root,)
    assert description["DatasetType"] == "derivative"
    assert description["Name"] == "Global signal pretrim"
    assert runner.calls == [[
        "nf-global-signal", "--bids-dir", str(tmp_path), "--out-tsv",
        str(root / "gs_metrics.tsv"), "--out-pdf", str(root / "gs.pdf"),
    ]]


def test_global_signal_rejects_unknown_label_without_writing(tmp_path):
    with pytest.raises(StageError, match="label"):
        run_global_signal(tmp_path, "during", Runner())
    assert not (tmp_path / "derivatives").exists()


def test_global_signal_reports_subprocess_failure(tmp_path):
    with pytest.raises(StageError, match="command failed"):
        run_global_signal(tmp_path, "posttrim", Runner(fail=True))
