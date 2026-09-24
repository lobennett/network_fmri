"""Strict, typed configuration for the single 46-subject BIDS workflow."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SUBJECT = re.compile(r"^s[0-9]+$")
_FLYWHEEL_PROJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_-]*$")
_SLOT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class WorkflowPaths:
    """Absolute locations used while building and processing one BIDS dataset."""

    bids_dir: Path
    parts_dir: Path
    work_dir: Path
    log_dir: Path
    templateflow_dir: Path
    freesurfer_license: Path


@dataclass(frozen=True)
class BehaviorSource:
    """The reviewed, immutable canonical behavioral source."""

    source: Path
    commit: str


@dataclass(frozen=True)
class BehaviorSources:
    """Finalized behavioral repositories included in the canonical dataset."""

    in_scanner: BehaviorSource
    out_of_scanner: BehaviorSource


@dataclass(frozen=True)
class ParticipantsSource:
    """The reviewed, deidentified BIDS-ready participant metadata source."""

    source: Path
    commit: str


@dataclass(frozen=True)
class ContainerConfig:
    """One versioned Apptainer image."""

    image: Path
    version: str


@dataclass(frozen=True)
class VerifiedContainerConfig:
    """One immutable container image identified by its expected checksum."""

    image: Path
    version: str
    sha256: str


@dataclass(frozen=True)
class SlurmConfig:
    """Shared positive resource limits for jobs in this workflow."""

    partition: str
    cpus: int
    memory_gb: int
    time_minutes: int
    array_concurrency: int
    account: str | None = None


@dataclass(frozen=True)
class MechaBABSAppConfig:
    """One named project-owned MechaBABS application file."""

    name: str
    file: Path


@dataclass(frozen=True)
class MechaBABSConfig:
    """Pinned study and campaign inputs for post-BIDS processing."""

    study_dir: Path
    campaign_dir: Path
    durable_sibling: Path
    bootstrap_script: Path
    campaign: str
    raw_slot: str
    container_dataset: Path
    mechababs_commit: str
    babs_commit: str
    mechababs_ref: str
    babs_ref: str
    cluster_file: Path
    apps: tuple[MechaBABSAppConfig, ...]


@dataclass(frozen=True)
class WorkflowConfig:
    """One validated interpretation of the single-dataset TOML configuration."""

    paths: WorkflowPaths
    subjects_file: Path
    subjects: tuple[str, ...]
    flywheel_project: str
    behavior: BehaviorSources
    participants: ParticipantsSource
    validator: ContainerConfig
    mriqc: ContainerConfig
    fmriprep: ContainerConfig
    pydeface: VerifiedContainerConfig
    slurm: SlurmConfig
    mechababs: MechaBABSConfig | None = None

    @classmethod
    def load(cls, path: Path) -> "WorkflowConfig":
        """Read and validate a workflow TOML file and its fixed subject roster."""

        try:
            raw = tomllib.loads(path.read_text())
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f"cannot read configuration {path}: {error}") from error
        return parse_config(raw, base=path.parent)


def parse_config(raw: dict[str, Any], *, base: Path) -> WorkflowConfig:
    """Parse one TOML document without allowing implicit runtime defaults."""

    if not isinstance(raw, dict):
        raise ValueError("workflow configuration must be a TOML table")
    _reject_token_keys(raw)
    _unknown_keys(
        raw,
        {"paths", "subjects_file", "flywheel_project", "behavior", "participants", "validator", "mriqc", "fmriprep", "pydeface", "slurm", "mechababs"},
        "top-level",
    )
    # Project-owned paths remain relative so the campaign records exactly which
    # checked-out files it copied. ``base`` remains available for future diagnostics.
    del base
    paths = _parse_paths(_table(raw, "paths", "top-level"))
    mechababs = _parse_mechababs(_table(raw, "mechababs", "top-level"))
    if mechababs.study_dir.resolve(strict=False).is_relative_to(
        paths.bids_dir.resolve(strict=False)
    ):
        raise ValueError("mechababs.study_dir must not be inside paths.bids_dir")
    subjects_file = _path(raw, "subjects_file", "top-level")
    subjects = _load_subjects(subjects_file)
    return WorkflowConfig(
        paths=paths,
        subjects_file=subjects_file,
        subjects=subjects,
        flywheel_project=_flywheel_project(raw),
        behavior=_parse_behavior(_table(raw, "behavior", "top-level")),
        validator=_parse_container(_table(raw, "validator", "top-level"), "validator"),
        mriqc=_parse_container(_table(raw, "mriqc", "top-level"), "mriqc"),
        fmriprep=_parse_container(_table(raw, "fmriprep", "top-level"), "fmriprep"),
        pydeface=_parse_verified_container(_table(raw, "pydeface", "top-level"), "pydeface"),
        slurm=_parse_slurm(_table(raw, "slurm", "top-level")),
        participants=_parse_participants(_table(raw, "participants", "top-level")),
        mechababs=mechababs,
    )


def _parse_paths(raw: dict[str, Any]) -> WorkflowPaths:
    _unknown_keys(
        raw,
        {
            "bids_dir",
            "parts_dir",
            "work_dir",
            "log_dir",
            "templateflow_dir",
            "freesurfer_license",
        },
        "paths",
    )
    paths = WorkflowPaths(
        bids_dir=_path(raw, "bids_dir", "paths"),
        parts_dir=_path(raw, "parts_dir", "paths"),
        work_dir=_path(raw, "work_dir", "paths"),
        log_dir=_path(raw, "log_dir", "paths"),
        templateflow_dir=_path(raw, "templateflow_dir", "paths"),
        freesurfer_license=_path(raw, "freesurfer_license", "paths"),
    )
    # Keep operators' configured paths intact for commands and receipts.  Canonicalize
    # only the comparison identities so ``..`` aliases and existing symlinks cannot
    # direct two runtime roles to the same location.
    runtime = tuple(
        path.resolve(strict=False)
        for path in (paths.bids_dir, paths.parts_dir, paths.work_dir, paths.log_dir)
    )
    if len(set(runtime)) != len(runtime):
        raise ValueError("paths.bids_dir, paths.parts_dir, paths.work_dir, and paths.log_dir must be distinct")
    return paths


def _flywheel_project(raw: dict[str, Any]) -> str:
    value = _nonempty_string(raw, "flywheel_project", "top-level")
    if not _FLYWHEEL_PROJECT.fullmatch(value):
        raise ValueError("flywheel_project must use the full group/project form")
    return value


def _parse_behavior(raw: dict[str, Any]) -> BehaviorSources:
    _unknown_keys(raw, {"in_scanner", "out_of_scanner"}, "behavior")
    return BehaviorSources(
        in_scanner=_parse_behavior_source(_table(raw, "in_scanner", "behavior"), "behavior.in_scanner"),
        out_of_scanner=_parse_behavior_source(
            _table(raw, "out_of_scanner", "behavior"), "behavior.out_of_scanner"
        ),
    )


def _parse_behavior_source(raw: dict[str, Any], name: str) -> BehaviorSource:
    _unknown_keys(raw, {"source", "commit"}, name)
    commit = _nonempty_string(raw, "commit", name)
    if not _COMMIT.fullmatch(commit):
        raise ValueError(f"{name}.commit must be a 40-character lowercase hexadecimal commit")
    return BehaviorSource(source=_path(raw, "source", name), commit=commit)


def _parse_participants(raw: dict[str, Any]) -> ParticipantsSource:
    name = "participants"
    _unknown_keys(raw, {"source", "commit"}, name)
    commit = _nonempty_string(raw, "commit", name)
    if not _COMMIT.fullmatch(commit):
        raise ValueError(f"{name}.commit must be a 40-character lowercase hexadecimal commit")
    return ParticipantsSource(source=_path(raw, "source", name), commit=commit)


def _parse_container(raw: dict[str, Any], name: str) -> ContainerConfig:
    _unknown_keys(raw, {"image", "version"}, name)
    return ContainerConfig(
        image=_path(raw, "image", name),
        version=_nonempty_string(raw, "version", name),
    )


def _parse_verified_container(raw: dict[str, Any], name: str) -> VerifiedContainerConfig:
    """Parse a present, regular container image with a canonical digest."""

    _unknown_keys(raw, {"image", "version", "sha256"}, name)
    image = _path(raw, "image", name)
    if image.is_symlink() or not image.is_file():
        raise ValueError(f"{name}.image must be a real regular file")
    sha256 = _nonempty_string(raw, "sha256", name)
    if not _SHA256.fullmatch(sha256):
        raise ValueError(f"{name}.sha256 must be a 64-character lowercase hexadecimal checksum")
    return VerifiedContainerConfig(
        image=image,
        version=_nonempty_string(raw, "version", name),
        sha256=sha256,
    )


def _parse_slurm(raw: dict[str, Any]) -> SlurmConfig:
    _unknown_keys(
        raw,
        {"partition", "account", "cpus", "memory_gb", "time_minutes", "array_concurrency"},
        "slurm",
    )
    account = raw.get("account")
    if account is not None and (not isinstance(account, str) or not account.strip()):
        raise ValueError("slurm.account must be a non-empty string when provided")
    return SlurmConfig(
        partition=_nonempty_string(raw, "partition", "slurm"),
        account=account,
        cpus=_positive_integer(raw, "cpus", "slurm"),
        memory_gb=_positive_integer(raw, "memory_gb", "slurm"),
        time_minutes=_positive_integer(raw, "time_minutes", "slurm"),
        array_concurrency=_positive_integer(raw, "array_concurrency", "slurm"),
    )


def _parse_mechababs(raw: dict[str, Any]) -> MechaBABSConfig:
    name = "mechababs"
    _unknown_keys(
        raw,
        {
            "study_dir", "campaign_dir", "durable_sibling", "bootstrap_script", "campaign", "raw_slot",
            "container_dataset", "mechababs_commit", "babs_commit", "mechababs_ref", "babs_ref",
            "cluster_file", "apps",
        },
        name,
    )
    apps_value = raw.get("apps")
    if not isinstance(apps_value, list) or not apps_value:
        raise ValueError("mechababs.apps must be a non-empty array of tables")
    apps = tuple(
        _parse_mechababs_app(value, index)
        for index, value in enumerate(apps_value)
    )
    if len({app.name for app in apps}) != len(apps):
        raise ValueError("mechababs app names must be unique")
    raw_slot = _nonempty_string(raw, "raw_slot", name)
    if not _SLOT.fullmatch(raw_slot):
        raise ValueError("mechababs.raw_slot must be one path component")
    campaign = _nonempty_string(raw, "campaign", name)
    campaign_dir = _path(raw, "campaign_dir", name)
    return MechaBABSConfig(
        study_dir=_path(raw, "study_dir", name),
        campaign_dir=campaign_dir,
        durable_sibling=_path(raw, "durable_sibling", name),
        bootstrap_script=_path(raw, "bootstrap_script", name),
        campaign=campaign,
        raw_slot=raw_slot,
        container_dataset=_path(raw, "container_dataset", name),
        mechababs_commit=_commit(raw, "mechababs_commit", name),
        babs_commit=_commit(raw, "babs_commit", name),
        mechababs_ref=_nonempty_string(raw, "mechababs_ref", name),
        babs_ref=_nonempty_string(raw, "babs_ref", name),
        cluster_file=_project_path(raw, "cluster_file", name),
        apps=apps,
    )


def _parse_mechababs_app(value: object, index: int) -> MechaBABSAppConfig:
    name = f"mechababs.apps[{index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a table")
    _unknown_keys(value, {"name", "file"}, name)
    return MechaBABSAppConfig(
        name=_nonempty_string(value, "name", name),
        file=_project_path(value, "file", name),
    )


def _commit(raw: dict[str, Any], key: str, where: str) -> str:
    value = _nonempty_string(raw, key, where)
    if not _COMMIT.fullmatch(value):
        raise ValueError(f"{where}.{key} must be a 40-character lowercase hexadecimal commit")
    return value


def _project_path(raw: dict[str, Any], key: str, where: str) -> Path:
    path = Path(_nonempty_string(raw, key, where))
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ValueError(f"{where}.{key} must be a project-relative path without '..'")
    return path


def _load_subjects(path: Path) -> tuple[str, ...]:
    try:
        subjects = tuple(line.strip() for line in path.read_text().splitlines())
    except OSError as error:
        raise ValueError(f"cannot read subjects_file {path}: {error}") from error
    if len(subjects) != 46:
        raise ValueError("subjects_file must contain exactly 46 roster labels")
    if len(set(subjects)) != 46:
        raise ValueError("subjects_file roster labels must be unique")
    invalid = sorted(subject for subject in subjects if not _SUBJECT.fullmatch(subject))
    if invalid:
        raise ValueError("subjects_file labels must match s[0-9]+: " + ", ".join(invalid))
    return subjects


def _table(raw: dict[str, Any], key: str, where: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{where}.{key} must be a TOML table")
    return value


def _nonempty_string(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}.{key} must be a non-empty string")
    return value


def _path(raw: dict[str, Any], key: str, where: str) -> Path:
    value = _nonempty_string(raw, key, where)
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{where}.{key} must be an absolute path")
    return path


def _positive_integer(raw: dict[str, Any], key: str, where: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{where}.{key} must be a positive integer")
    return value


def _unknown_keys(raw: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"unknown {where} configuration key(s): {', '.join(unknown)}")


def _reject_token_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if "token" in key.lower():
                raise ValueError("token values are not permitted in workflow configuration")
            _reject_token_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_token_keys(child)
