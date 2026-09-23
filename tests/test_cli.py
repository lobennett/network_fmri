"""The public CLI intentionally exposes only workflow operations."""

from pathlib import Path

from network_fmri import cli
from network_fmri.models import StageResult


def test_help_lists_the_small_public_surface(capsys):
    assert cli.main(["--help"]) == 0
    output = capsys.readouterr().out

    assert "pipeline" in output
    assert "decisions" in output
    assert "surfaces" in output
    assert "curate" in output
    assert "glm" not in output


def test_decisions_validate_calls_the_sealing_stage(tmp_path, monkeypatch):
    observed: list[Path] = []
    result = StageResult("scan-decisions-approved", (tmp_path / "manifest.tsv",))
    monkeypatch.setattr(
        cli, "validate_decisions", lambda path: observed.append(path) or result,
    )
    saved: list[tuple[Path, object]] = []
    monkeypatch.setattr(
        cli.pipeline, "save_stage_result", lambda bids, stage: saved.append((bids, stage)),
    )

    assert cli.main(["decisions", "validate", str(tmp_path / "bids")]) == 0
    assert observed == [tmp_path / "bids"]
    assert saved == [(tmp_path / "bids", result)]
    assert saved[0][1].name == "scan-decisions-approved"


def test_surfaces_validate_seals_the_configured_dataset(tmp_path, monkeypatch):
    config = type("Config", (), {"paths": type("Paths", (), {"bids_dir": tmp_path / "bids"})()})()
    result = StageResult("surface-review-approved", (tmp_path / "surface_review.tsv",))
    monkeypatch.setattr(cli.WorkflowConfig, "load", lambda _: config)
    monkeypatch.setattr(cli, "validate_surface_review", lambda value: result if value is config else None)
    saved = []
    monkeypatch.setattr(
        cli.pipeline, "save_stage_result",
        lambda bids, stage, **kwargs: saved.append((bids, stage, kwargs["config"])),
    )

    assert cli.main(["surfaces", "validate", str(tmp_path / "workflow.toml")]) == 0
    assert saved == [(tmp_path / "bids", result, config)]


def test_curate_uses_only_the_governed_manifest_path(tmp_path, monkeypatch):
    observed: list[tuple[Path, Path, Path]] = []
    monkeypatch.setattr(
        cli, "apply_curation",
        lambda bids, manifest, image: observed.append((bids, manifest, image)),
    )

    image = tmp_path / "validator.sif"
    assert cli.main([
        "curate", str(tmp_path / "bids"), "--validator-image", str(image),
    ]) == 0
    assert observed == [(
        tmp_path / "bids",
        tmp_path / "bids" / "code" / "network_fmri" / "scan_decisions.tsv",
        image,
    )]
