"""The public CLI intentionally exposes only workflow operations."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from network_fmri import cli


def test_processing_run_accepts_poll_interval():
    parsed = cli.get_parser().parse_args(["processing", "run", "workflow.toml", "--poll-seconds", "5"])
    assert parsed.processing_command == "run"
    assert parsed.poll_seconds == 5
from network_fmri.models import StageResult


@pytest.mark.parametrize('state,code', [('awaiting-scan-review', 0), ('evidence-error', 2)])
def test_mriqc_controller_cli_propagates_review_state(monkeypatch, capsys, state, code):
    from network_fmri import handoff
    config = object()
    monkeypatch.setattr(cli.WorkflowConfig, 'load', lambda path: config)
    monkeypatch.setattr(cli, 'ProcessingManager', lambda value: None)
    def run(value, *, interval):
        assert value is config and interval == 10
        return {'state': state}
    monkeypatch.setattr(handoff, 'run_mriqc', run)
    assert cli.main(['processing', 'run-mriqc', 'workflow.toml', '--poll-seconds', '10']) == code
    assert state in capsys.readouterr().out


def test_help_lists_the_small_public_surface(capsys):
    assert cli.main(["--help"]) == 0
    output = capsys.readouterr().out

    assert "pipeline" in output
    assert "decisions" in output
    assert "surfaces" in output
    assert "curate" in output
    assert "study" in output
    assert "processing" in output
    assert "records" in output
    assert "glm" not in output


def test_records_build_prints_machine_readable_summary(tmp_path, monkeypatch, capsys):
    config = SimpleNamespace(mechababs=SimpleNamespace(study_dir=tmp_path / "study"))
    monkeypatch.setattr(cli.WorkflowConfig, "load", lambda _: config)
    monkeypatch.setattr(
        cli, "build_index", lambda value, output: {
            "output": str(output), "schema_version": 1, "study_commit": "a" * 40,
            "counts": {"entities": 2, "findings": 3},
        },
    )

    output = tmp_path / "cache.sqlite"
    assert cli.main(["records", "build", str(tmp_path / "workflow.toml"), "--output", str(output)]) == 0
    value = __import__("json").loads(capsys.readouterr().out)
    assert value["counts"] == {"entities": 2, "findings": 3}
    assert value["output"] == str(output)


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


def test_decisions_validate_saves_wrapper_approval(tmp_path, monkeypatch):
    manifest = tmp_path / "study/code/network_fmri/scan_decisions.tsv"
    result = StageResult("scan-decisions-approved", (manifest,))
    observed = []
    monkeypatch.setattr(
        cli, "validate_decisions",
        lambda raw, **kwargs: observed.append((raw, kwargs)) or result,
    )
    saved = []
    monkeypatch.setattr(cli.pipeline, "save_stage_result", lambda root, stage: saved.append((root, stage)))

    assert cli.main([
        "decisions", "validate", str(tmp_path / "raw"),
        "--manifest", str(manifest), "--approval-dataset", str(tmp_path / "study"),
    ]) == 0
    assert observed == [(tmp_path / "raw", {"manifest": manifest})]
    assert saved == [(tmp_path / "study", result)]


def test_decisions_generate_writes_mriqc_review_manifest(tmp_path, monkeypatch):
    observed = []
    result = StageResult("scan-decisions-generated", (tmp_path / "manifest.tsv",))
    monkeypatch.setattr(cli, "generate_decisions", lambda path: observed.append(path) or result)
    saved = []
    monkeypatch.setattr(cli.pipeline, "save_stage_result", lambda bids, stage: saved.append((bids, stage)))

    assert cli.main(["decisions", "generate", str(tmp_path / "bids")]) == 0
    assert observed == [tmp_path / "bids"]
    assert saved == [(tmp_path / "bids", result)]


def test_decisions_generate_accepts_campaign_paths(tmp_path, monkeypatch):
    observed = []
    result = StageResult("scan-decisions-generated", (tmp_path / "review.tsv",))
    monkeypatch.setattr(
        cli, "generate_decisions",
        lambda raw, **kwargs: observed.append((raw, kwargs)) or result,
    )
    monkeypatch.setattr(cli.pipeline, "save_stage_result", lambda *args: None)

    assert cli.main([
        "decisions", "generate", str(tmp_path / "raw"),
        "--mriqc-dir", str(tmp_path / "mriqc"),
        "--output", str(tmp_path / "study/review.tsv"),
    ]) == 0
    assert observed == [(tmp_path / "raw", {
        "mriqc_dir": tmp_path / "mriqc", "output": tmp_path / "study/review.tsv",
    })]


def test_surfaces_generate_uses_anatomical_derivative(tmp_path, monkeypatch):
    config = SimpleNamespace(mechababs=SimpleNamespace(study_dir=tmp_path / "study"))
    result = StageResult("surface-review-generated", (tmp_path / "surface_review.tsv",))
    monkeypatch.setattr(cli.WorkflowConfig, "load", lambda _: config)
    observed = []
    monkeypatch.setattr(
        cli, "generate_surface_review",
        lambda value, derivative: observed.append((value, derivative)) or result,
    )
    monkeypatch.setattr(cli.pipeline, "save_stage_result", lambda *args, **kwargs: None)

    assert cli.main([
        "surfaces", "generate", str(tmp_path / "workflow.toml"),
        "--anatomical-derivative", str(tmp_path / "anat"),
    ]) == 0
    assert observed == [(config, tmp_path / "anat")]


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
    result = StageResult("bids-curated-validated", (tmp_path / "report.json",))
    monkeypatch.setattr(cli, "apply_curation", lambda bids, manifest, image: observed.append((bids, manifest, image)) or result)
    saved = []
    monkeypatch.setattr(cli.pipeline, "save_stage_result", lambda bids, stage: saved.append((bids, stage)))

    image = tmp_path / "validator.sif"
    assert cli.main([
        "curate", str(tmp_path / "bids"), "--validator-image", str(image),
    ]) == 0
    assert observed == [(
        tmp_path / "bids",
        tmp_path / "bids" / "code" / "network_fmri" / "scan_decisions.tsv",
        image,
    )]
    assert saved == [(tmp_path / "bids", result)]


def test_curate_accepts_wrapper_manifest(tmp_path, monkeypatch):
    observed = []
    result = StageResult("bids-curated-validated", (tmp_path / "report.json",))
    monkeypatch.setattr(
        cli, "apply_curation",
        lambda bids, manifest, image: observed.append((bids, manifest, image)) or result,
    )
    monkeypatch.setattr(cli.pipeline, "save_stage_result", lambda *args: None)
    manifest = tmp_path / "study/code/network_fmri/scan_decisions.tsv"

    assert cli.main([
        "curate", str(tmp_path / "raw"), "--manifest", str(manifest),
        "--validator-image", str(tmp_path / "validator.sif"),
    ]) == 0
    assert observed[0][1] == manifest


def test_study_init_uses_selected_pilot_and_prints_identity(tmp_path, monkeypatch, capsys):
    config = SimpleNamespace(
        mechababs=object(), paths=SimpleNamespace(
            bids_dir=tmp_path / "raw", freesurfer_license=tmp_path / "license.txt",
        ),
        subjects=("s01", "s02"),
    )
    pilot = SimpleNamespace(
        mechababs=config.mechababs, paths=config.paths, subjects=("s02",),
    )
    monkeypatch.setattr(cli.WorkflowConfig, "load", lambda _: config)
    monkeypatch.setattr(cli.pipeline, "pilot_config", lambda value, subject: pilot)
    calls = []

    class Manager:
        def __init__(self, mechababs, raw, license_path):
            calls.append((mechababs, raw, license_path))

        def initialize(self, *, subjects):
            calls.append(subjects)
            return SimpleNamespace(
                created=True, study_id="study-id", raw_dataset_id="raw-id",
                raw_commit="a" * 40, study_dir=tmp_path / "study",
                campaign_dir=tmp_path / "campaign",
            )

    monkeypatch.setattr(cli, "StudyManager", Manager)

    assert cli.main(["study", "init", str(tmp_path / "workflow.toml"), "--pilot-subject", "s02"]) == 0
    assert calls == [(
        config.mechababs, tmp_path / "raw", tmp_path / "license.txt",
    ), ("s02",)]
    assert "study_id\tstudy-id" in capsys.readouterr().out


def test_processing_plan_prints_stable_stage_rows(tmp_path, monkeypatch, capsys):
    config = SimpleNamespace(subjects=("s01",))
    monkeypatch.setattr(cli.WorkflowConfig, "load", lambda _: config)

    class Manager:
        def __init__(self, value):
            assert value is config

        def plan(self):
            return (SimpleNamespace(stage="mriqc", application="MRIQC-24.0.2", state="ready"),)

    monkeypatch.setattr(cli, "ProcessingManager", Manager)

    assert cli.main(["processing", "plan", str(tmp_path / "workflow.toml")]) == 0
    assert capsys.readouterr().out == "mriqc\tready\tMRIQC-24.0.2\n"


def test_processing_status_prints_stages_and_json_jobs(tmp_path, monkeypatch, capsys):
    config = object()
    monkeypatch.setattr(cli.WorkflowConfig, "load", lambda _: config)

    class Manager:
        def __init__(self, value):
            assert value is config

        def status(self):
            return SimpleNamespace(
                stages=(SimpleNamespace(stage="mriqc", application="MRIQC-24.0.2", state="active"),),
                jobs=({"job_id": "123", "state": "RUNNING"},),
            )

    monkeypatch.setattr(cli, "ProcessingManager", Manager)

    assert cli.main(["processing", "status", str(tmp_path / "workflow.toml")]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "mriqc\tactive\tMRIQC-24.0.2",
        'job\t{"job_id": "123", "state": "RUNNING"}',
    ]


def test_processing_advance_passes_only_named_stage(tmp_path, monkeypatch, capsys):
    config = object()
    monkeypatch.setattr(cli.WorkflowConfig, "load", lambda _: config)
    calls = []

    class Manager:
        def __init__(self, value):
            assert value is config

        def advance(self, stage):
            calls.append(stage)
            return SimpleNamespace(stage=stage, advanced=True, previous_state="ready")

    monkeypatch.setattr(cli, "ProcessingManager", Manager)

    assert cli.main([
        "processing", "advance", str(tmp_path / "workflow.toml"), "--stage", "anatomical",
    ]) == 0
    assert calls == ["anatomical"]
    assert capsys.readouterr().out == "anatomical\tadvanced\tready\n"


def test_prepare_review_cli_preserves_pilot_selection(tmp_path, monkeypatch, capsys):
    config, pilot = object(), object()
    monkeypatch.setattr(cli.WorkflowConfig, 'load', lambda _: config)
    monkeypatch.setattr(cli.pipeline, 'pilot_config', lambda value, subject: pilot if value is config and subject == 's01' else None)
    monkeypatch.setattr(cli, 'ProcessingManager', lambda _: None)
    def prepare(value):
        assert value is pilot
        return SimpleNamespace(evidence_dir=tmp_path / 'review', source_dataset=tmp_path / 'babs',
            source_commit='a' * 40, input_commit='b' * 40, archives=1, created=True)
    monkeypatch.setattr(cli, 'prepare_mriqc_review', prepare)
    assert cli.main(['processing', 'prepare-review', 'workflow.toml', '--pilot-subject', 's01']) == 0
    assert 'evidence_dir\t' in capsys.readouterr().out


@pytest.mark.parametrize('operation,extra', [('status', []), ('advance', ['--stage', 'mriqc']), ('prepare-review', [])])
def test_processing_commands_accept_pilot_subject(operation, extra):
    parsed = cli.get_parser().parse_args(['processing', operation, 'workflow.toml', '--pilot-subject', 's01', *extra])
    assert parsed.pilot_subject == 's01'


def test_generate_decisions_accepts_explicit_approval_dataset(tmp_path):
    parsed = cli.get_parser().parse_args(['decisions', 'generate', str(tmp_path / 'raw'),
                                    '--approval-dataset', str(tmp_path / 'study')])
    assert parsed.approval_dataset == tmp_path / 'study'
