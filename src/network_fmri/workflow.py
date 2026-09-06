"""Render one auditable runbook from Flywheel export through group models.

This module is deliberately a thin facade. It does not implement another scheduler,
scientific method, or retry system: existing network_fmri commands remain the
executors. The workflow file supplies the small amount of study-level wiring that was
previously reconstructed by hand for every cohort.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
import tomllib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from network_fmri import provenance
from network_fmri.cohorts import COHORTS, roster

SCHEMA_VERSION = 1
NF = str(Path(sys.executable).parent / "network_fmri")
_UNEXPANDED_VARIABLE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[^}]+\})")
_SPACES = {"MNI", "T1w", "surface", "fsaverage6"}
_TASK_SETS = {"base", "dual", "all"}
_CONFOUND_MODES = {"full", "no-motion", "no-cosine", "task-only"}


class WorkflowConfigError(ValueError):
    """The study-run file is missing information or is internally inconsistent."""


@dataclass(frozen=True)
class IntegrationConfig:
    """Explicitly activated package integrations, grouped by lifecycle profile."""

    directories: tuple[Path, ...] = ()
    bids: tuple[str, ...] = ()
    post_fmriprep: tuple[str, ...] = ()
    analysis: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelConfig:
    """Scientific and output choices shared by both model levels."""

    level1_dir: Path
    level2_dir: Path
    tasks: str = "base"
    space: str = "MNI"
    smoothing_fwhm: float | None = None
    min_runs: int = 2
    confounds_mode: str = "full"
    residuals: bool = True
    skip_qc_plots: bool = True
    num_permutations: int = 5000
    level1_extra_args: tuple[str, ...] = ()
    level2_extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowConfig:
    """One immutable interpretation of a versioned study-run TOML file."""

    source: Path
    source_sha256: str
    cohort: str
    project: str
    staging: Path
    campaign: Path
    partition: str
    export_throttle: int
    campaign_batch: int
    live: bool
    integrations: IntegrationConfig
    model: ModelConfig

    @property
    def bids_dir(self) -> Path:
        return self.staging / self.cohort / "bids"

    @property
    def mriqc_dir(self) -> Path:
        return self.bids_dir / "derivatives" / "mriqc"

    @property
    def fmriprep_dir(self) -> Path:
        return self.bids_dir / "derivatives" / "fmriprep"

    @property
    def qa_dir(self) -> Path:
        return self.bids_dir / "derivatives" / "qa"

    @property
    def motion_lock(self) -> Path:
        return self.qa_dir / f"{self.cohort}_motion_lock.json"

    @property
    def final_lock(self) -> Path:
        return self.qa_dir / f"{self.cohort}_lev1_lock.json"

    @property
    def outlier_csv(self) -> Path:
        return self.model.level1_dir / "cohort_qa" / "lev1_outliers.csv"


@dataclass(frozen=True)
class WorkflowStep:
    """One operator-visible handoff in the end-to-end runbook."""

    name: str
    phase: str
    summary: str
    command: tuple[str, ...]
    requires: tuple[Path, ...] = ()
    produces: tuple[Path, ...] = ()
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "phase": self.phase,
            "summary": self.summary,
            "command": list(self.command),
            "requires": [str(path) for path in self.requires],
            "produces": [str(path) for path in self.produces],
            "note": self.note,
        }


def _unknown_keys(value: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise WorkflowConfigError(f"unknown {where} key(s): {', '.join(unknown)}")


def _required_string(value: dict[str, Any], key: str, where: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise WorkflowConfigError(f"{where}.{key} must be a non-empty string")
    return result


def _string_list(value: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    result = value.get(key, [])
    if not isinstance(result, list) or any(
        not isinstance(item, str) or not item for item in result
    ):
        raise WorkflowConfigError(f"{where}.{key} must be an array of strings")
    return tuple(result)


def _path(raw: str, where: str) -> Path:
    expanded = os.path.expanduser(os.path.expandvars(raw))
    if _UNEXPANDED_VARIABLE.search(expanded):
        raise WorkflowConfigError(f"{where} contains an unset environment variable")
    result = Path(expanded)
    if not result.is_absolute():
        raise WorkflowConfigError(f"{where} must be an absolute path")
    return result


def _positive_integer(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkflowConfigError(f"{where} must be a positive integer")
    return value


def _boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise WorkflowConfigError(f"{where} must be true or false")
    return value


def _optional_number(value: Any, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise WorkflowConfigError(f"{where} must be a non-negative number")
    return float(value)


def _reject_governed_args(
    arguments: tuple[str, ...], governed: set[str], where: str
) -> None:
    duplicates = sorted(
        {
            token.split("=", 1)[0]
            for token in arguments
            if token.split("=", 1)[0] in governed
        }
    )
    if duplicates:
        raise WorkflowConfigError(
            f"{where} duplicates run-spec fields: {', '.join(duplicates)}"
        )


def load_config(path: Path) -> WorkflowConfig:
    """Read and strictly validate a version-1 study-run file."""

    try:
        raw_bytes = path.read_bytes()
        raw = tomllib.loads(raw_bytes.decode())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise WorkflowConfigError(f"cannot read {path}: {error}") from error
    if not isinstance(raw, dict):
        raise WorkflowConfigError("workflow document must be a TOML table")
    _unknown_keys(
        raw,
        {
            "schema_version",
            "cohort",
            "project",
            "staging",
            "campaign",
            "partition",
            "export_throttle",
            "campaign_batch",
            "live",
            "integrations",
            "model",
        },
        "top-level",
    )

    schema = raw.get("schema_version")
    if schema != SCHEMA_VERSION:
        raise WorkflowConfigError(
            f"schema_version must be {SCHEMA_VERSION}, got {schema!r}"
        )
    cohort = _required_string(raw, "cohort", "top-level")
    if cohort not in COHORTS:
        raise WorkflowConfigError(
            f"unknown cohort {cohort!r}; expected one of {', '.join(COHORTS)}"
        )

    integrations_raw = raw.get("integrations", {})
    if not isinstance(integrations_raw, dict):
        raise WorkflowConfigError("integrations must be a TOML table")
    _unknown_keys(
        integrations_raw,
        {"directories", "bids", "post_fmriprep", "analysis"},
        "integrations",
    )
    integrations = IntegrationConfig(
        directories=tuple(
            _path(item, "integrations.directories")
            for item in _string_list(integrations_raw, "directories", "integrations")
        ),
        bids=_string_list(integrations_raw, "bids", "integrations"),
        post_fmriprep=_string_list(integrations_raw, "post_fmriprep", "integrations"),
        analysis=_string_list(integrations_raw, "analysis", "integrations"),
    )

    model_raw = raw.get("model")
    if not isinstance(model_raw, dict):
        raise WorkflowConfigError("model must be a TOML table")
    _unknown_keys(
        model_raw,
        {
            "level1_dir",
            "level2_dir",
            "tasks",
            "space",
            "smoothing_fwhm",
            "min_runs",
            "confounds_mode",
            "residuals",
            "skip_qc_plots",
            "num_permutations",
            "level1_extra_args",
            "level2_extra_args",
        },
        "model",
    )
    tasks = model_raw.get("tasks", "base")
    if not isinstance(tasks, str) or tasks not in _TASK_SETS:
        raise WorkflowConfigError(
            f"model.tasks must be one of {', '.join(sorted(_TASK_SETS))}"
        )
    space = model_raw.get("space", "MNI")
    if not isinstance(space, str) or space not in _SPACES:
        raise WorkflowConfigError(
            "model.space must be MNI, T1w, surface, or fsaverage6; "
            "fsLR is residuals-only and cannot produce this group task-contrast workflow"
        )
    confounds_mode = model_raw.get("confounds_mode", "full")
    if not isinstance(confounds_mode, str) or confounds_mode not in _CONFOUND_MODES:
        raise WorkflowConfigError(
            "model.confounds_mode must be full, no-motion, no-cosine, or task-only"
        )
    level1_extra = _string_list(model_raw, "level1_extra_args", "model")
    level2_extra = _string_list(model_raw, "level2_extra_args", "model")
    _reject_governed_args(
        level1_extra,
        {
            "--bids-dir",
            "--fmriprep-dir",
            "--exclusions-file",
            "--min-runs",
            "--confounds-mode",
            "--smoothing-fwhm",
            "--residuals",
            "--skip-existing",
            "--skip-qc-plots",
            "--space",
        },
        "model.level1_extra_args",
    )
    _reject_governed_args(
        level2_extra,
        {"--num-permutations", "--space", "--level1-dirs", "--output-dir"},
        "model.level2_extra_args",
    )
    model = ModelConfig(
        level1_dir=_path(
            _required_string(model_raw, "level1_dir", "model"),
            "model.level1_dir",
        ),
        level2_dir=_path(
            _required_string(model_raw, "level2_dir", "model"),
            "model.level2_dir",
        ),
        tasks=tasks,
        space=space,
        smoothing_fwhm=_optional_number(
            model_raw.get("smoothing_fwhm"), "model.smoothing_fwhm"
        ),
        min_runs=_positive_integer(model_raw.get("min_runs", 2), "model.min_runs"),
        confounds_mode=confounds_mode,
        residuals=_boolean(model_raw.get("residuals", True), "model.residuals"),
        skip_qc_plots=_boolean(
            model_raw.get("skip_qc_plots", True), "model.skip_qc_plots"
        ),
        num_permutations=_positive_integer(
            model_raw.get("num_permutations", 5000),
            "model.num_permutations",
        ),
        level1_extra_args=level1_extra,
        level2_extra_args=level2_extra,
    )
    if model.level1_dir == model.level2_dir:
        raise WorkflowConfigError(
            "model.level1_dir and model.level2_dir must be different"
        )

    project = raw.get("project", "r01network")
    partition = raw.get("partition", "russpold,normal")
    if not isinstance(project, str) or not project:
        raise WorkflowConfigError("top-level.project must be a non-empty string")
    if not isinstance(partition, str) or not partition:
        raise WorkflowConfigError("top-level.partition must be a non-empty string")
    return WorkflowConfig(
        source=path.absolute(),
        source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        cohort=cohort,
        project=project,
        staging=_path(
            _required_string(raw, "staging", "top-level"), "top-level.staging"
        ),
        campaign=_path(
            _required_string(raw, "campaign", "top-level"), "top-level.campaign"
        ),
        partition=partition,
        export_throttle=_positive_integer(
            raw.get("export_throttle", 3), "top-level.export_throttle"
        ),
        campaign_batch=_positive_integer(
            raw.get("campaign_batch", 1), "top-level.campaign_batch"
        ),
        live=_boolean(raw.get("live", False), "top-level.live"),
        integrations=integrations,
        model=model,
    )


def _task_flag(tasks: str) -> tuple[str, ...]:
    return {
        "base": ("--base-tasks",),
        "dual": ("--dual-tasks",),
        "all": ("--all",),
    }[tasks]


def _integration_args(
    config: WorkflowConfig, enabled: tuple[str, ...]
) -> tuple[str, ...]:
    result: list[str] = []
    for directory in config.integrations.directories:
        result.extend(("--integration-dir", str(directory)))
    for name in enabled:
        result.extend(("--enable-integration", name))
    return tuple(result)


def _pipeline_command(
    config: WorkflowConfig, profile: str, enabled: tuple[str, ...]
) -> tuple[str, ...]:
    command = [
        NF,
        "pipeline",
        "--cohort",
        config.cohort,
        "--profile",
        profile,
        "--staging",
        str(config.staging),
        "--project",
        config.project,
        "--partition",
        config.partition,
        "--no-extensions",
        *_integration_args(config, enabled),
    ]
    if profile == "bids":
        command.extend(("--throttle", str(config.export_throttle)))
        if config.live:
            command.append("--live")
        else:
            command.append("--print")
    elif profile == "post-fmriprep":
        command.extend(("--fmriprep-dir", str(config.fmriprep_dir)))
    elif profile == "analysis":
        command.extend(
            (
                "--fmriprep-dir",
                str(config.fmriprep_dir),
                "--exclusions-file",
                str(config.motion_lock),
                "--analysis-dir",
                str(config.model.level1_dir.parent),
            )
        )
    return tuple(command)


def _campaign_command(config: WorkflowConfig, *arguments: str) -> tuple[str, ...]:
    return (
        NF,
        "campaign",
        "--campaign",
        str(config.campaign),
        "--staging",
        str(config.staging),
        "--partition",
        config.partition,
        "--",
        *arguments,
    )


def _level1_command(
    config: WorkflowConfig, exclusions: Path, *, finalize: bool
) -> tuple[str, ...]:
    model = config.model
    command = [
        NF,
        "glm-lev1",
        "--cohort",
        config.cohort,
        *_task_flag(model.tasks),
        "--results-dir",
        str(model.level1_dir),
        "--space",
        model.space,
        "--partition",
        config.partition,
        "--",
        "--bids-dir",
        str(config.bids_dir),
        "--fmriprep-dir",
        str(config.fmriprep_dir),
        "--exclusions-file",
        str(exclusions),
        "--min-runs",
        str(model.min_runs),
        "--confounds-mode",
        model.confounds_mode,
    ]
    if model.smoothing_fwhm is not None:
        command.extend(("--smoothing-fwhm", str(model.smoothing_fwhm)))
    if model.residuals:
        command.append("--residuals")
    if model.skip_qc_plots:
        command.append("--skip-qc-plots")
    if finalize and model.residuals:
        command.append("--skip-existing")
    command.extend(model.level1_extra_args)
    return tuple(command)


def build_steps(config: WorkflowConfig) -> tuple[WorkflowStep, ...]:
    """Build the ordered operator runbook without touching data or Slurm."""

    campaign_study = (
        config.campaign / "studies" / f"study-{config.cohort}" / "derivatives"
    )
    dataset_description = config.bids_dir / "dataset_description.json"
    fmriprep_description = config.fmriprep_dir / "dataset_description.json"
    steps: list[WorkflowStep] = [
        WorkflowStep(
            "prepare-bids",
            "BIDS",
            "export Flywheel data and run the validated BIDS preparation DAG",
            _pipeline_command(config, "bids", config.integrations.bids),
            produces=(dataset_description,),
            requires=(
                Path.home() / ".config" / "flywheel" / "user.json",
                config.staging.parent,
            ),
            note=(
                "Live Flywheel tagging/export is disabled until top-level live=true."
                if not config.live
                else None
            ),
        ),
        WorkflowStep(
            "campaign-status",
            "preprocessing",
            "submit a lightweight campaign status check",
            _campaign_command(config, "status"),
            requires=(
                dataset_description,
                config.campaign / ".venv" / "bin" / "activate",
            ),
        ),
        WorkflowStep(
            "campaign-preview",
            "preprocessing",
            "dry-run the next campaign transition before advancing it",
            _campaign_command(config, "iterate", "--dry-run"),
            requires=(
                dataset_description,
                config.campaign / ".venv" / "bin" / "activate",
            ),
            note=(
                "Review this output before every campaign-advance; the campaign "
                "may select cells across cohorts."
            ),
        ),
        WorkflowStep(
            "campaign-advance",
            "preprocessing",
            "advance ready MRIQC and fMRIPrep campaign cells",
            _campaign_command(config, "iterate", "--batch", str(config.campaign_batch)),
            requires=(
                dataset_description,
                config.campaign / ".venv" / "bin" / "activate",
            ),
            produces=(campaign_study,),
            note="Repeat status/advance until the required campaign cells are merged.",
        ),
        WorkflowStep(
            "assemble-mriqc",
            "quality-control",
            "assemble MRIQC IQMs into the cohort BIDS derivative",
            (
                NF,
                "mriqc-iqms",
                "--cohort",
                config.cohort,
                "--staging",
                str(config.staging),
                "--campaign",
                str(config.campaign),
            ),
            requires=(campaign_study / "MRIQC-24.0.2",),
            produces=(config.mriqc_dir / "dataset_description.json",),
            note="Runs data extraction in the foreground; use a Slurm allocation.",
        ),
        WorkflowStep(
            "compile-motion-exclusions",
            "quality-control",
            "compile the motion and behavioral exclusion lockfile",
            (
                NF,
                "qa-motion",
                "--cohort",
                config.cohort,
                "--staging",
                str(config.staging),
                "--out",
                str(config.motion_lock),
                "--mriqc-dir",
                str(config.mriqc_dir),
                "--partition",
                config.partition,
            ),
            requires=(config.mriqc_dir / "dataset_description.json",),
            produces=(config.motion_lock,),
        ),
        WorkflowStep(
            "assemble-fmriprep",
            "preprocessing",
            "assemble fMRIPrep archives into the cohort BIDS derivative",
            (
                NF,
                "fmriprep-derivs",
                "--cohort",
                config.cohort,
                "--staging",
                str(config.staging),
                "--campaign",
                str(config.campaign),
            ),
            requires=(campaign_study / "fMRIPrep-25.2.5",),
            produces=(fmriprep_description,),
            note="Runs bulk extraction in the foreground; use a large Slurm allocation.",
        ),
    ]
    if config.integrations.post_fmriprep:
        steps.append(
            WorkflowStep(
                "post-fmriprep-integrations",
                "integration",
                "verify fMRIPrep and run explicitly enabled post-fMRIPrep packages",
                _pipeline_command(
                    config, "post-fmriprep", config.integrations.post_fmriprep
                ),
                requires=(fmriprep_description,),
            )
        )
    if config.integrations.analysis:
        steps.append(
            WorkflowStep(
                "analysis-integrations",
                "integration",
                "verify model inputs and run explicitly enabled analysis packages",
                _pipeline_command(config, "analysis", config.integrations.analysis),
                requires=(fmriprep_description, config.motion_lock),
            )
        )
    steps.extend(
        (
            WorkflowStep(
                "level1-initial",
                "modeling",
                "fit runs and provisional subject fixed effects using the motion lock",
                _level1_command(config, config.motion_lock, finalize=False),
                requires=(
                    dataset_description,
                    fmriprep_description,
                    config.motion_lock,
                ),
                produces=(config.model.level1_dir,),
            ),
            WorkflowStep(
                "level1-outliers",
                "quality-control",
                "detect cohort outliers without reintroducing excluded runs",
                (
                    NF,
                    "glm-outliers",
                    "--lev1-dirs",
                    str(config.model.level1_dir),
                    "--results-dir",
                    str(config.outlier_csv.parent),
                    "--partition",
                    config.partition,
                    "--",
                    "--exclusions-file",
                    str(config.motion_lock),
                ),
                requires=(config.model.level1_dir, config.motion_lock),
                produces=(config.outlier_csv,),
            ),
            WorkflowStep(
                "compile-level1-exclusions",
                "quality-control",
                "add first-level outliers to the final exclusion lockfile",
                (
                    NF,
                    "qa-lev1",
                    "--cohort",
                    config.cohort,
                    "--staging",
                    str(config.staging),
                    "--out",
                    str(config.final_lock),
                    "--mriqc-dir",
                    str(config.mriqc_dir),
                    "--lev1-outliers-csv",
                    str(config.outlier_csv),
                    "--partition",
                    config.partition,
                ),
                requires=(
                    config.outlier_csv,
                    config.mriqc_dir / "dataset_description.json",
                ),
                produces=(config.final_lock,),
            ),
            WorkflowStep(
                "level1-finalize",
                "modeling",
                "refresh subject fixed effects using the final exclusion lockfile",
                _level1_command(config, config.final_lock, finalize=True),
                requires=(config.model.level1_dir, config.final_lock),
                produces=(config.model.level1_dir,),
                note=(
                    "Existing residuals make this a fixed-effects refresh."
                    if config.model.residuals
                    else "Residuals are disabled, so this safety pass refits run models."
                ),
            ),
            WorkflowStep(
                "level2",
                "modeling",
                "submit group models from finalized subject fixed effects",
                (
                    NF,
                    "glm-lev2",
                    "--lev1-dirs",
                    str(config.model.level1_dir),
                    *_task_flag(config.model.tasks),
                    "--results-dir",
                    str(config.model.level2_dir),
                    "--space",
                    (
                        "surface"
                        if config.model.space in {"surface", "fsaverage6"}
                        else "volume"
                    ),
                    "--partition",
                    config.partition,
                    "--",
                    "--num-permutations",
                    str(config.model.num_permutations),
                    *config.model.level2_extra_args,
                ),
                requires=(config.model.level1_dir, config.final_lock),
                produces=(config.model.level2_dir,),
                note="Run only after every level1-finalize array task succeeds.",
            ),
        )
    )
    return tuple(steps)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return value


def build_record(
    config: WorkflowConfig, steps: tuple[WorkflowStep, ...]
) -> dict[str, Any]:
    """Create a provenance-ready, machine-readable copy of the runbook."""

    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "configuration": {
            "path": str(config.source),
            "sha256": config.source_sha256,
            "resolved": _jsonable(asdict(config)),
        },
        "code": {
            "revision": provenance.code_revision(),
            "dirty": provenance.code_is_dirty(),
            "python": sys.version.split()[0],
        },
        "subjects": roster(config.cohort),
        "paths": {
            "bids": str(config.bids_dir),
            "mriqc": str(config.mriqc_dir),
            "fmriprep": str(config.fmriprep_dir),
            "motion_exclusions": str(config.motion_lock),
            "final_exclusions": str(config.final_lock),
            "level1": str(config.model.level1_dir),
            "level2": str(config.model.level2_dir),
        },
        "steps": [step.as_dict() for step in steps],
    }


def _find_step(steps: tuple[WorkflowStep, ...], name: str) -> WorkflowStep:
    try:
        return next(step for step in steps if step.name == name)
    except StopIteration:
        available = ", ".join(step.name for step in steps)
        raise WorkflowConfigError(
            f"unknown workflow step {name!r}; expected one of: {available}"
        ) from None


def _print_plan(config: WorkflowConfig, steps: tuple[WorkflowStep, ...]) -> None:
    print(
        f"{config.cohort}: {len(roster(config.cohort))} subjects, "
        f"{len(steps)} operator steps"
    )
    for index, step in enumerate(steps, start=1):
        print(f"\n{index:02d}. {step.name} [{step.phase}]")
        print(f"    {step.summary}")
        print(f"    {shlex.join(step.command)}")
        if step.note:
            print(f"    note: {step.note}")


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="network_fmri workflow")
    commands = parser.add_subparsers(dest="action", required=True)
    plan = commands.add_parser("plan", help="print the complete ordered runbook")
    plan.add_argument("config", type=Path)
    plan.add_argument("--json", type=Path, help="write an auditable plan record")
    command = commands.add_parser("command", help="print one exact command")
    command.add_argument("config", type=Path)
    command.add_argument("step")
    check = commands.add_parser("check", help="check filesystem prerequisites")
    check.add_argument("config", type=Path)
    check.add_argument("step")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = get_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        steps = build_steps(config)
        if args.action == "plan":
            _print_plan(config, steps)
            if args.json:
                args.json.parent.mkdir(parents=True, exist_ok=True)
                args.json.write_text(
                    json.dumps(build_record(config, steps), indent=2, sort_keys=True)
                    + "\n"
                )
                print(f"\nplan record: {args.json}")
            return 0
        step = _find_step(steps, args.step)
        if args.action == "command":
            print(shlex.join(step.command))
            return 0
        missing = [path for path in step.requires if not path.exists()]
        for path in step.requires:
            state = "MISSING" if path in missing else "OK"
            print(f"{state:7s} {path}")
        if missing:
            print(
                f"{step.name}: {len(missing)} missing prerequisite(s)",
                file=sys.stderr,
            )
            return 1
        print(f"{step.name}: ready")
        return 0
    except WorkflowConfigError as error:
        parser.error(str(error))
        return 2
