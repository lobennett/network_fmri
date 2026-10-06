import subprocess
import shutil

import pytest

from network_fmri.surface_inventory import reconstruction_inventory
from network_fmri.stages import StageError


def test_real_annex_surfaces_keep_the_same_inventory(tmp_path):
    if not shutil.which("git-annex"):
        pytest.skip("git-annex is required for this integration test")
    def git(*args):
        return subprocess.run(("git", *args), cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.org")
    git("annex", "init", "surface-test")
    root = tmp_path / "subjects/sub-s03"
    path = root / "surf/lh.white"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"surface contents")
    before = reconstruction_inventory(root, "s03")
    git("annex", "add", "subjects")
    assert path.is_symlink()
    assert reconstruction_inventory(root, "s03") == before


def test_surface_link_cannot_read_other_external_files(tmp_path):
    root = tmp_path / "subjects/sub-s03"
    root.mkdir(parents=True)
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    (root / "outside").symlink_to(outside)
    with pytest.raises(StageError, match="escapes"):
        reconstruction_inventory(root, "s03")


def test_annex_lookup_timeout_is_not_reported_as_an_escaped_link(tmp_path, monkeypatch):
    root = tmp_path / "subjects/sub-s03"
    root.mkdir(parents=True)
    target = tmp_path / "annex/object"
    target.parent.mkdir()
    target.write_bytes(b"surface")
    (root / "surface").symlink_to(target)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 5)
    monkeypatch.setattr(subprocess, "check_output", timeout)
    with pytest.raises(StageError, match="cannot determine.*annex"):
        reconstruction_inventory(root, "s03")


def test_regular_extracted_files_do_not_query_parent_git(tmp_path, monkeypatch):
    root = tmp_path / "subjects/sub-s03"
    root.mkdir(parents=True)
    (root / "surface").write_bytes(b"surface")
    def fail(*args, **kwargs):
        raise AssertionError("regular extracted files do not need Git")
    monkeypatch.setattr(subprocess, "check_output", fail)
    assert "surface" in reconstruction_inventory(root, "s03")
