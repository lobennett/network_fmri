import json
from pathlib import Path

import pytest

from network_fmri import workflow
from network_fmri.workflow import WorkflowConfigError, build_steps, load_config


def _write_config(
    tmp_path: Path,
    *,
    residuals: bool = True,
    integrations: str = "",
    model_extra: str = "",
    top_extra: str = "",
) -> Path:
    path = tmp_path / "workflow.toml"
    path.write_text(
        f"""
schema_version = 1
cohort = "discovery"
project = "test-project"
staging = "{tmp_path / 'staging'}"
campaign = "{tmp_path / 'campaign'}"
partition = "test"
export_throttle = 2
campaign_batch = 3
live = true
{top_extra}

[integrations]
directories = ["{tmp_path / 'manifests'}"]
bids = []
post_fmriprep = []
analysis = []
{integrations}

[model]
level1_dir = "{tmp_path / 'level1'}"
level2_dir = "{tmp_path / 'level2'}"
tasks = "base"
space = "fsaverage6"
smoothing_fwhm = 2.355
min_runs = 2
confounds_mode = "full"
residuals = {str(residuals).lower()}
skip_qc_plots = true
num_permutations = 99
level1_extra_args = ["--allow-dirty"]
level2_extra_args = ["--seed", "7"]
{model_extra}
""".strip()
        + "\n"
    )
    return path


def _by_name(path: Path):
    config = load_config(path)
    return config, {step.name: step for step in build_steps(config)}


def test_plan_covers_final_exclusions_before_group_model(tmp_path):
    config, steps = _by_name(_write_config(tmp_path))
    names = list(steps)
    assert names.index("campaign-preview") < names.index("campaign-advance")
    assert "--dry-run" in steps["campaign-preview"].command
    assert names.index("level1-outliers") < names.index("compile-level1-exclusions")
    assert names.index("compile-level1-exclusions") < names.index("level1-finalize")
    assert names.index("level1-finalize") < names.index("level2")

    initial = steps["level1-initial"].command
    finalize = steps["level1-finalize"].command
    outliers = steps["level1-outliers"].command
    assert str(config.motion_lock) in initial
    assert str(config.motion_lock) in outliers
    assert str(config.final_lock) in finalize
    assert "--skip-existing" not in initial
    assert "--skip-existing" in finalize
    assert (
        steps["level2"].command[steps["level2"].command.index("--space") + 1]
        == "surface"
    )


def test_review_mode_cannot_submit_flywheel_export(tmp_path):
    path = _write_config(tmp_path)
    path.write_text(path.read_text().replace("live = true", "live = false"))
    _, steps = _by_name(path)
    command = steps["prepare-bids"].command
    assert "--print" in command
    assert "--live" not in command
    assert (
        Path.home() / ".config" / "flywheel" / "user.json"
        in steps["prepare-bids"].requires
    )


def test_without_residuals_finalization_explicitly_refits(tmp_path):
    _, steps = _by_name(_write_config(tmp_path, residuals=False))
    final = steps["level1-finalize"]
    assert "--residuals" not in final.command
    assert "--skip-existing" not in final.command
    assert "refits run models" in final.note


def test_integrations_are_routed_to_explicit_profiles(tmp_path):
    path = _write_config(tmp_path)
    path.write_text(
        path.read_text().replace(
            "bids = []\npost_fmriprep = []\nanalysis = []",
            'bids = ["before-trim"]\n'
            'post_fmriprep = ["after-prep"]\n'
            'analysis = ["package-analysis"]',
        )
    )
    _, steps = _by_name(path)
    assert "--no-extensions" in steps["prepare-bids"].command
    assert "before-trim" in steps["prepare-bids"].command
    assert "after-prep" in steps["post-fmriprep-integrations"].command
    assert "package-analysis" in steps["analysis-integrations"].command
    assert str(tmp_path / "manifests") in steps["analysis-integrations"].command


@pytest.mark.parametrize(
    "old, new, message",
    [
        (
            'level1_extra_args = ["--allow-dirty"]',
            'level1_extra_args = ["--min-runs", "9"]',
            "duplicates run-spec fields",
        ),
        ('space = "fsaverage6"', 'space = "fsLR"', "residuals-only"),
    ],
)
def test_config_rejects_ambiguous_settings(tmp_path, old, new, message):
    path = _write_config(tmp_path)
    path.write_text(path.read_text().replace(old, new))
    with pytest.raises(WorkflowConfigError, match=message):
        load_config(path)


def test_config_rejects_unknown_settings(tmp_path):
    with pytest.raises(WorkflowConfigError, match="unknown top-level key"):
        load_config(_write_config(tmp_path, top_extra="typo = true"))


def test_check_reports_missing_then_ready_prerequisites(tmp_path, capsys):
    path = _write_config(tmp_path)
    assert workflow.main(["check", str(path), "level1-outliers"]) == 1
    captured = capsys.readouterr()
    assert "MISSING" in captured.out
    assert "2 missing prerequisite" in captured.err

    config = load_config(path)
    config.model.level1_dir.mkdir()
    config.motion_lock.parent.mkdir(parents=True)
    config.motion_lock.write_text("{}\n")
    assert workflow.main(["check", str(path), "level1-outliers"]) == 0
    assert "level1-outliers: ready" in capsys.readouterr().out


def test_json_plan_records_resolved_paths_code_and_subjects(tmp_path, monkeypatch):
    path = _write_config(tmp_path)
    destination = tmp_path / "plan.json"
    monkeypatch.setattr(workflow.provenance, "code_revision", lambda: "a" * 40)
    monkeypatch.setattr(workflow.provenance, "code_is_dirty", lambda: False)

    assert workflow.main(["plan", str(path), "--json", str(destination)]) == 0
    record = json.loads(destination.read_text())
    assert record["configuration"]["sha256"] == load_config(path).source_sha256
    assert record["code"] == {
        "revision": "a" * 40,
        "dirty": False,
        "python": record["code"]["python"],
    }
    assert record["subjects"] == ["s03", "s10", "s19", "s29", "s43"]
    assert record["paths"]["level2"] == str(tmp_path / "level2")
    assert record["steps"][-1]["name"] == "level2"
    assert record["steps"][-1]["command"][-4:] == [
        "--num-permutations",
        "99",
        "--seed",
        "7",
    ]


def test_repository_example_is_safe_and_has_no_smoothing_default(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRATCH", str(tmp_path))
    path = Path(__file__).parents[1] / "config" / "workflow.example.toml"
    config = load_config(path)
    steps = {step.name: step for step in build_steps(config)}
    assert config.live is False
    assert config.model.smoothing_fwhm is None
    assert "--print" in steps["prepare-bids"].command
    assert "--smoothing-fwhm" not in steps["level1-initial"].command
