from pathlib import Path

from network_fmri.provenance import activate_git_annex, ensure_git_annex


def test_existing_git_annex_bundle_exposes_its_compatible_git(tmp_path: Path):
    root = tmp_path / "git-annex"
    bindir = root / "usr" / "bin"
    bundle = root / "usr" / "lib" / "git-annex.linux"
    bindir.mkdir(parents=True)
    bundle.mkdir(parents=True)
    (bindir / "git-annex").touch()
    (bundle / "git-annex").touch()
    (bundle / "git").touch()

    assert ensure_git_annex(root) == bundle


def test_activate_git_annex_prepends_bundle_to_path(tmp_path: Path, monkeypatch):
    root = tmp_path / "git-annex"
    bindir = root / "usr" / "bin"
    bundle = root / "usr" / "lib" / "git-annex.linux"
    bindir.mkdir(parents=True)
    bundle.mkdir(parents=True)
    (bindir / "git-annex").touch()
    (bundle / "git-annex").touch()
    (bundle / "git").touch()
    monkeypatch.setenv("PATH", "/system/bin")

    assert activate_git_annex(root) == bundle
    assert __import__("os").environ["PATH"] == f"{bundle}:/system/bin"
