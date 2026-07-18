"""Offline tests for network_fmri.datalad_ds (DataLad-ify the staged BIDS tree).

subprocess.run is monkeypatched so no real datalad/git-annex is needed: we
capture the shelled-out commands and assert the create/save contract.
"""

import pytest

from network_fmri import datalad_ds as dd


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _recorder(returncode=0):
    """A fake subprocess.run that records (cmd, kwargs) and returns FakeProc."""
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append((cmd, kwargs))
        return FakeProc(returncode=returncode)

    fake_run.calls = calls
    return fake_run


def test_fresh_dir_creates_then_saves(tmp_path, monkeypatch):
    fake = _recorder()
    monkeypatch.setattr(dd.subprocess, "run", fake)

    dd.dataladify(str(tmp_path), message="msg")

    cmds = [c for c, _ in fake.calls]
    # create --force -c text2git <path>
    assert cmds[0] == ["datalad", "create", "--force", "-c", "text2git", str(tmp_path)]
    # then a save -m msg .
    assert cmds[1][:4] == ["datalad", "save", "-m", "msg"]
    assert cmds[1][-1] == "."
    # save runs inside the dataset
    assert fake.calls[1][1].get("cwd") == str(tmp_path)


def test_jobs_adds_jobs_flag_to_save(tmp_path, monkeypatch):
    (tmp_path / ".datalad").mkdir()  # existing dataset -> save only
    fake = _recorder()
    monkeypatch.setattr(dd.subprocess, "run", fake)

    dd.dataladify(str(tmp_path), message="msg", jobs=8)

    save = fake.calls[0][0]
    assert save[:2] == ["datalad", "save"]
    assert "--jobs" in save and save[save.index("--jobs") + 1] == "8"
    assert save[-3:] == ["-m", "msg", "."]


def test_default_omits_jobs_flag(tmp_path, monkeypatch):
    (tmp_path / ".datalad").mkdir()
    fake = _recorder()
    monkeypatch.setattr(dd.subprocess, "run", fake)

    dd.dataladify(str(tmp_path), message="msg")

    assert "--jobs" not in fake.calls[0][0]


def test_text2git_false_omits_config(tmp_path, monkeypatch):
    fake = _recorder()
    monkeypatch.setattr(dd.subprocess, "run", fake)

    dd.dataladify(str(tmp_path), text2git=False)

    assert fake.calls[0][0] == ["datalad", "create", "--force", str(tmp_path)]


def test_existing_dataset_only_saves(tmp_path, monkeypatch):
    (tmp_path / ".datalad").mkdir()
    fake = _recorder()
    monkeypatch.setattr(dd.subprocess, "run", fake)

    dd.dataladify(str(tmp_path), message="update")

    cmds = [c for c, _ in fake.calls]
    assert len(cmds) == 1  # no create
    assert cmds[0][:4] == ["datalad", "save", "-m", "update"]
    assert fake.calls[0][1].get("cwd") == str(tmp_path)


def test_nonzero_returncode_raises_systemexit(tmp_path, monkeypatch):
    monkeypatch.setattr(dd.subprocess, "run", _recorder(returncode=1))
    with pytest.raises(SystemExit):
        dd.dataladify(str(tmp_path))
