import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


def write_subjects(path: Path, labels: list[str] | None = None) -> Path:
    path.write_text("\n".join(labels or [f"s{number}" for number in range(1, 47)]) + "\n")
    return path


def write_config(
    tmp_path: Path,
    *,
    behavior_commit: str = "a" * 40,
    subjects: list[str] | None = None,
    paths: dict[str, str] | None = None,
    slurm: dict[str, int] | None = None,
    extra: str = "",
) -> Path:
    subjects_file = write_subjects(tmp_path / "subjects.txt", subjects)
    workflow_paths = {
        "bids_dir": str(tmp_path / "bids"),
        "parts_dir": str(tmp_path / "parts"),
        "work_dir": str(tmp_path / "work"),
        "log_dir": str(tmp_path / "logs"),
        "templateflow_dir": str(tmp_path / "templateflow"),
        "freesurfer_license": str(tmp_path / "license.txt"),
    }
    workflow_paths.update(paths or {})
    resources = {
        "cpus": 8,
        "memory_gb": 32,
        "time_minutes": 720,
        "array_concurrency": 4,
    }
    resources.update(slurm or {})
    path = tmp_path / "workflow.toml"
    path.write_text(
        f'''subjects_file = "{subjects_file}"
flywheel_project = "russpold/r01network"

[paths]
{chr(10).join(f'{key} = "{value}"' for key, value in workflow_paths.items())}

[behavior]
source = "{tmp_path / 'canonical-behavior'}"
commit = "{behavior_commit}"

[mriqc]
image = "{tmp_path / 'mriqc.sif'}"
version = "24.0.2"

[fmriprep]
image = "{tmp_path / 'fmriprep.sif'}"
version = "25.2.5"

[slurm]
partition = "normal"
{chr(10).join(f'{key} = {value}' for key, value in resources.items())}
{extra}'''
    )
    return path


def test_loads_single_dataset_configuration(tmp_path):
    from network_fmri.config import WorkflowConfig

    config = WorkflowConfig.load(write_config(tmp_path))

    assert config.paths.bids_dir == tmp_path / "bids"
    assert config.mriqc.version == "24.0.2"
    assert config.fmriprep.version == "25.2.5"
    assert config.subjects == tuple(f"s{number}" for number in range(1, 47))


def test_rejects_short_behavior_commit(tmp_path):
    from network_fmri.config import WorkflowConfig

    path = write_config(tmp_path, behavior_commit="445eba8")

    with pytest.raises(ValueError, match="40-character"):
        WorkflowConfig.load(path)


@pytest.mark.parametrize("value", ["r01network", "group/", "/project", "group/project/extra"])
def test_rejects_noncanonical_flywheel_project(tmp_path, value):
    from network_fmri.config import WorkflowConfig

    path = write_config(tmp_path)
    path.write_text(path.read_text().replace("russpold/r01network", value))
    with pytest.raises(ValueError, match="group/project"):
        WorkflowConfig.load(path)


@pytest.mark.parametrize("field", ["bids_dir", "parts_dir", "work_dir", "log_dir"])
def test_rejects_relative_runtime_paths(tmp_path, field):
    from network_fmri.config import WorkflowConfig

    path = write_config(tmp_path, paths={field: "relative"})

    with pytest.raises(ValueError, match="absolute path"):
        WorkflowConfig.load(path)


@pytest.mark.parametrize(
    ("subjects", "message"),
    [
        ([f"s{number}" for number in range(1, 46)], "exactly 46"),
        (["s1"] * 46, "unique"),
        (["participant1"] + [f"s{number}" for number in range(2, 47)], "s\\[0-9\\]"),
    ],
)
def test_rejects_invalid_subject_roster(tmp_path, subjects, message):
    from network_fmri.config import WorkflowConfig

    with pytest.raises(ValueError, match=message):
        WorkflowConfig.load(write_config(tmp_path, subjects=subjects))


@pytest.mark.parametrize("field", ["cpus", "memory_gb", "time_minutes", "array_concurrency"])
def test_rejects_nonpositive_slurm_resources(tmp_path, field):
    from network_fmri.config import WorkflowConfig

    with pytest.raises(ValueError, match="positive"):
        WorkflowConfig.load(write_config(tmp_path, slurm={field: 0}))


@pytest.mark.parametrize(
    "duplicate_field", ["parts_dir", "work_dir", "log_dir"]
)
def test_rejects_shared_runtime_directories(tmp_path, duplicate_field):
    from network_fmri.config import WorkflowConfig

    path = write_config(tmp_path, paths={duplicate_field: str(tmp_path / "bids")})

    with pytest.raises(ValueError, match="distinct"):
        WorkflowConfig.load(path)


@pytest.mark.parametrize(
    "alias", ["bids/../bids", "logs/../bids"]
)
def test_rejects_lexically_equivalent_runtime_directories(tmp_path, alias):
    from network_fmri.config import WorkflowConfig

    raw_bids_dir = tmp_path / "run" / "bids" / ".." / "bids"
    path = write_config(
        tmp_path,
        paths={
            "bids_dir": str(raw_bids_dir),
            "parts_dir": str(tmp_path / "run" / alias),
        },
    )

    with pytest.raises(ValueError, match="distinct"):
        WorkflowConfig.load(path)


def test_rejects_existing_symlink_runtime_alias_but_preserves_configured_path(tmp_path):
    from network_fmri.config import WorkflowConfig

    bids_dir = tmp_path / "bids"
    bids_dir.mkdir()
    parts_link = tmp_path / "parts-link"
    parts_link.symlink_to(bids_dir, target_is_directory=True)
    path = write_config(tmp_path, paths={"parts_dir": str(parts_link)})

    with pytest.raises(ValueError, match="distinct"):
        WorkflowConfig.load(path)

    raw_work_dir = tmp_path / "work" / ".." / "work"
    config = WorkflowConfig.load(write_config(tmp_path, paths={"work_dir": str(raw_work_dir)}))
    assert config.paths.work_dir == raw_work_dir


@pytest.mark.parametrize(
    ("needle", "replacement", "message"),
    [
        ('subjects_file = "', 'subjects_file = "relative/', "absolute path"),
        ('source = "', 'source = "relative/', "absolute path"),
        ('image = "', 'image = "relative/', "absolute path"),
        ('templateflow_dir = "', 'templateflow_dir = "relative/', "absolute path"),
        ('freesurfer_license = "', 'freesurfer_license = "relative/', "absolute path"),
    ],
)
def test_rejects_relative_nonruntime_paths(tmp_path, needle, replacement, message):
    from network_fmri.config import WorkflowConfig

    path = write_config(tmp_path)
    path.write_text(path.read_text().replace(needle, replacement, 1))

    with pytest.raises(ValueError, match=message):
        WorkflowConfig.load(path)


@pytest.mark.parametrize("commit", ["A" * 40, "g" * 40, "a" * 39])
def test_rejects_noncanonical_behavior_commits(tmp_path, commit):
    from network_fmri.config import WorkflowConfig

    with pytest.raises(ValueError, match="40-character"):
        WorkflowConfig.load(write_config(tmp_path, behavior_commit=commit))


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda text: "unknown = true\n" + text, "unknown top-level"),
        (lambda text: text + "unknown = true\n", "unknown slurm"),
    ],
)
def test_rejects_unknown_top_level_and_nested_keys(tmp_path, edit, message):
    from network_fmri.config import WorkflowConfig

    path = write_config(tmp_path)
    path.write_text(edit(path.read_text()))

    with pytest.raises(ValueError, match=message):
        WorkflowConfig.load(path)


def test_rejects_token_values_in_configuration(tmp_path):
    from network_fmri.config import WorkflowConfig

    path = write_config(tmp_path, extra='flywheel_api_token = "secret-value"\n')

    with pytest.raises(ValueError, match="token"):
        WorkflowConfig.load(path)


def test_stage_result_defaults_to_empty_details():
    from network_fmri.models import StageResult

    result = StageResult("bids-assembled", (Path("/scratch/bids"),))

    assert result.details == {}


def test_config_models_are_frozen(tmp_path):
    from network_fmri.config import WorkflowConfig

    config = WorkflowConfig.load(write_config(tmp_path))

    with pytest.raises(FrozenInstanceError):
        config.paths.bids_dir = Path("/other")


def test_runner_protocol_accepts_subprocess_run():
    from network_fmri.models import Runner

    runner: Runner = subprocess.run
    result = runner(
        [sys.executable, "-c", "print('runner-compatible')"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert isinstance(runner, Runner)
    assert result.stdout == "runner-compatible\n"
