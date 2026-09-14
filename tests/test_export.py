"""Export must preserve any output already present at its destination."""

from unittest.mock import Mock

import pytest

from network_fmri.fw2bids import curate


@pytest.mark.parametrize("kind", ["directory", "file", "dangling-symlink"])
def test_existing_export_root_is_preserved(tmp_path, monkeypatch, kind):
    out = tmp_path / "export"
    if kind == "directory":
        out.mkdir()
        sentinel = out / "participant-data.txt"
        sentinel.write_text("preserve me")
    elif kind == "file":
        out.write_text("preserve me")
        sentinel = out
    else:
        out.symlink_to(tmp_path / "missing")
    run = Mock()
    monkeypatch.setattr(curate, "run_with_retries", run)

    with pytest.raises(SystemExit, match="already exists.*preserved"):
        curate.export("synthetic", {"s03"}, out)

    run.assert_not_called()
    if kind == "dangling-symlink":
        assert out.is_symlink()
        assert out.readlink() == tmp_path / "missing"
    else:
        assert sentinel.read_text() == "preserve me"


def test_new_export_destination_is_forwarded(tmp_path, monkeypatch):
    out = tmp_path / "new-parent" / "export"
    run = Mock()
    monkeypatch.setattr(curate, "run_with_retries", run)

    curate.export("synthetic", {"s10", "s03"}, out, retries=3)

    assert out.parent.is_dir()
    assert not out.exists()
    command, description, retries = run.call_args.args
    assert command[1:] == [
        "--project", "synthetic", "--subject", "s03", "s10",
        "--destination", str(out.parent), "--directory-name", out.name,
    ]
    assert description == "export ['s03', 's10']"
    assert retries == 3
