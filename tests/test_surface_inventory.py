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
