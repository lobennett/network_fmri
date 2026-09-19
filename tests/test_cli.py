"""The public CLI intentionally exposes only workflow operations."""

from pathlib import Path

from network_fmri import cli


def test_help_lists_the_small_public_surface(capsys):
    assert cli.main(["--help"]) == 0
    output = capsys.readouterr().out

    assert "pipeline" in output
    assert "decisions" in output
    assert "curate" in output
    assert "glm" not in output


def test_decisions_validate_calls_the_sealing_stage(tmp_path, monkeypatch):
    observed: list[Path] = []
    monkeypatch.setattr(cli, "validate_decisions", lambda path: observed.append(path))

    assert cli.main(["decisions", "validate", str(tmp_path / "bids")]) == 0
    assert observed == [tmp_path / "bids"]


def test_curate_uses_only_the_governed_manifest_path(tmp_path, monkeypatch):
    observed: list[tuple[Path, Path]] = []
    monkeypatch.setattr(cli, "apply_curation", lambda bids, manifest: observed.append((bids, manifest)))

    assert cli.main(["curate", str(tmp_path / "bids")]) == 0
    assert observed == [(
        tmp_path / "bids",
        tmp_path / "bids" / "code" / "network_fmri" / "scan_decisions.tsv",
    )]
