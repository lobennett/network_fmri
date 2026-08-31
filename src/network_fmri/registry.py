"""Authoritative CLI and Slurm-stage registries.

Scientific implementations stay in their existing modules. External packages may
contribute cohort stages through the network_fmri.pipeline_stages entry-point group,
but Slurm remains the only execution backend.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any

ENTRY_POINT_GROUP = "network_fmri.pipeline_stages"
_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
_MEMORY = re.compile(r"^[1-9][0-9]*(?:[KMGT])$")
_TIME = re.compile(r"^(?:[0-9]+-)?[0-9]{2}:[0-9]{2}:[0-9]{2}$")


class RegistryError(ValueError):
    """A registry declaration is inconsistent."""


@dataclass(frozen=True)
class CommandSpec:
    """One public CLI route and its lazily imported implementation."""

    route: tuple[str, ...]
    target: str
    summary: str

    def __post_init__(self) -> None:
        module, separator, attribute = self.target.partition(":")
        if (
            not self.route
            or any(not part for part in self.route)
            or not separator
            or not module
            or not attribute
        ):
            raise RegistryError(f"invalid command declaration {self!r}")

    @property
    def display_name(self) -> str:
        return " ".join(self.route)

    def load(self) -> Callable[[list[str]], int]:
        module_name, attribute = self.target.split(":", 1)
        handler = getattr(importlib.import_module(module_name), attribute)
        if not callable(handler):
            raise RegistryError(f"command target {self.target!r} is not callable")
        return handler


@dataclass(frozen=True)
class ArtifactSpec:
    """A logical stage input or output and its path template."""

    name: str
    location: str
    description: str
    external: bool = False

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name) or not self.location or not self.description:
            raise RegistryError(f"invalid artifact declaration {self!r}")


@dataclass(frozen=True)
class SlurmResources:
    """Resources for one job or each task in an array."""

    cpus: int
    memory: str
    time_limit: str

    def __post_init__(self) -> None:
        if self.cpus < 1 or not _MEMORY.fullmatch(self.memory):
            raise RegistryError(f"invalid Slurm resources {self!r}")
        if not _TIME.fullmatch(self.time_limit):
            raise RegistryError(f"invalid Slurm time limit {self.time_limit!r}")


class SubmissionKind(StrEnum):
    COMMAND = "command"
    EXPORT_ARRAY = "export-array"


@dataclass(frozen=True)
class StageSpec:
    """Declarative contract for one Slurm pipeline stage."""

    name: str
    description: str
    resources: SlurmResources
    command: tuple[str, ...]
    inputs: tuple[ArtifactSpec, ...]
    outputs: tuple[ArtifactSpec, ...]
    after: tuple[str, ...] = ()
    before: tuple[str, ...] = ()
    working_directory: str | None = None
    submission: SubmissionKind = SubmissionKind.COMMAND
    provider: str = "network_fmri"

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name) or not self.description or not self.command:
            raise RegistryError(f"invalid stage declaration {self!r}")
        if self.name in self.after or self.name in self.before:
            raise RegistryError(f"stage {self.name!r} cannot order itself")
        if any(not _NAME.fullmatch(name) for name in (*self.after, *self.before)):
            raise RegistryError(f"stage {self.name!r} has an invalid ordering target")


@dataclass(frozen=True)
class PipelineContext:
    """Values available in stage command and artifact templates."""

    cohort: str
    staging: str
    network_fmri_bin: str
    events_bin: str
    project: str
    partition: str
    throttle: int
    live: bool

    @property
    def bids_dir(self) -> str:
        return str(Path(self.staging) / self.cohort / "bids")

    def values(self) -> dict[str, str]:
        return {
            "bids_dir": self.bids_dir,
            "cohort": self.cohort,
            "events_bin": self.events_bin,
            "live_flag": "--live" if self.live else "",
            "network_fmri": self.network_fmri_bin,
            "partition": self.partition,
            "project": self.project,
            "staging": self.staging,
            "throttle": str(self.throttle),
        }


@dataclass(frozen=True)
class PlannedArtifact:
    name: str
    location: str
    description: str
    external: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "location": self.location,
            "description": self.description,
            "external": self.external,
        }


@dataclass(frozen=True)
class PlannedStage:
    name: str
    description: str
    resources: SlurmResources
    command: tuple[str, ...]
    inputs: tuple[PlannedArtifact, ...]
    outputs: tuple[PlannedArtifact, ...]
    dependencies: tuple[str, ...]
    working_directory: str | None
    submission: SubmissionKind
    provider: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "provider": self.provider,
            "submission": self.submission.value,
            "dependencies": list(self.dependencies),
            "resources": {
                "cpus": self.resources.cpus,
                "memory": self.resources.memory,
                "time_limit": self.resources.time_limit,
            },
            "command": list(self.command),
            "working_directory": self.working_directory,
            "inputs": [item.as_dict() for item in self.inputs],
            "outputs": [item.as_dict() for item in self.outputs],
        }


@dataclass(frozen=True)
class StageExtension:
    """Explicit return type supported for external stage entry points."""

    stages: tuple[StageSpec, ...]


def _render(template: str, context: PipelineContext, owner: str) -> str:
    try:
        return template.format_map(context.values())
    except KeyError as error:
        raise RegistryError(
            f"{owner} uses unknown template field {error.args[0]!r}"
        ) from error


def _render_artifact(
    artifact: ArtifactSpec, context: PipelineContext
) -> PlannedArtifact:
    return PlannedArtifact(
        artifact.name,
        _render(artifact.location, context, f"artifact {artifact.name!r}"),
        artifact.description,
        artifact.external,
    )


class StageRegistry:
    """Validated stage collection with stable topological planning."""

    def __init__(self, stages: Iterable[StageSpec] = ()) -> None:
        self._stages: dict[str, StageSpec] = {}
        for stage in stages:
            self.register(stage)

    def register(self, stage: StageSpec, *, provider: str | None = None) -> None:
        effective_provider = provider or stage.provider
        if stage.name in self._stages:
            old_provider = self._stages[stage.name].provider
            raise RegistryError(
                f"stage {stage.name!r} from {effective_provider!r} "
                f"conflicts with provider {old_provider!r}"
            )
        if stage.submission == SubmissionKind.EXPORT_ARRAY and stage.name != "export":
            raise RegistryError(
                "export-array is reserved for the built-in Flywheel export stage"
            )
        if (
            effective_provider != "network_fmri"
            and not stage.after
            and not stage.before
        ):
            raise RegistryError(
                f"extension stage {stage.name!r} must declare after or before"
            )
        self._stages[stage.name] = replace(stage, provider=effective_provider)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._stages)

    def _dependencies(self) -> dict[str, list[str]]:
        result = {name: list(stage.after) for name, stage in self._stages.items()}
        for stage in self._stages.values():
            for target in stage.before:
                if target not in result:
                    raise RegistryError(
                        f"stage {stage.name!r} orders unknown stage {target!r}"
                    )
                result[target].append(stage.name)
        for name, dependencies in result.items():
            unknown = [item for item in dependencies if item not in result]
            if unknown:
                raise RegistryError(
                    f"stage {name!r} has unknown dependencies: {', '.join(unknown)}"
                )
            result[name] = list(dict.fromkeys(dependencies))
        return result

    def _ordered(self, dependencies: dict[str, list[str]]) -> list[str]:
        remaining = {name: set(items) for name, items in dependencies.items()}
        ordered: list[str] = []
        while remaining:
            ready = [
                name
                for name in self._stages
                if name in remaining and not remaining[name]
            ]
            if not ready:
                raise RegistryError(
                    "pipeline dependency cycle involving: " + ", ".join(remaining)
                )
            for name in ready:
                ordered.append(name)
                remaining.pop(name)
                for required in remaining.values():
                    required.discard(name)
        return ordered

    def _validate_artifacts(
        self, ordered: Sequence[str], dependencies: dict[str, list[str]]
    ) -> None:
        definitions: dict[str, ArtifactSpec] = {}
        producers: dict[str, str] = {}
        ancestors: dict[str, set[str]] = {}
        for name in ordered:
            stage = self._stages[name]
            stage_ancestors: set[str] = set()
            for dependency in dependencies[name]:
                stage_ancestors.add(dependency)
                stage_ancestors.update(ancestors[dependency])
            ancestors[name] = stage_ancestors

            for artifact in (*stage.inputs, *stage.outputs):
                old = definitions.setdefault(artifact.name, artifact)
                if old != artifact:
                    raise RegistryError(
                        f"artifact {artifact.name!r} is declared inconsistently"
                    )
            for artifact in stage.inputs:
                if artifact.external:
                    continue
                producer = producers.get(artifact.name)
                if producer is None:
                    raise RegistryError(
                        f"stage {name!r} consumes unproduced {artifact.name!r}"
                    )
                if producer not in stage_ancestors:
                    raise RegistryError(
                        f"stage {name!r} does not depend on producer {producer!r} "
                        f"of {artifact.name!r}"
                    )
            for artifact in stage.outputs:
                if artifact.external:
                    raise RegistryError(
                        f"stage {name!r} produces external {artifact.name!r}"
                    )
                previous = producers.get(artifact.name)
                if previous is not None:
                    input_names = {item.name for item in stage.inputs}
                    if (
                        artifact.name not in input_names
                        or previous not in stage_ancestors
                    ):
                        raise RegistryError(
                            f"artifact {artifact.name!r} has multiple "
                            "unsequenced producers"
                        )
                producers[artifact.name] = name

    def plan(
        self, context: PipelineContext, *, start: str | None = None
    ) -> tuple[PlannedStage, ...]:
        dependencies = self._dependencies()
        for name, required in dependencies.items():
            if (
                self._stages[name].submission == SubmissionKind.EXPORT_ARRAY
                and required
            ):
                raise RegistryError(
                    "the built-in export array cannot have predecessor stages"
                )
        ordered = self._ordered(dependencies)
        self._validate_artifacts(ordered, dependencies)
        if start is not None:
            if start not in self._stages:
                raise RegistryError(f"--from must be one of: {' '.join(ordered)}")
            ordered = ordered[ordered.index(start) :]
        selected = set(ordered)

        planned = []
        for name in ordered:
            stage = self._stages[name]
            command = tuple(
                value
                for token in stage.command
                if (value := _render(token, context, f"stage {name!r}"))
            )
            planned.append(
                PlannedStage(
                    name,
                    stage.description,
                    stage.resources,
                    command,
                    tuple(_render_artifact(item, context) for item in stage.inputs),
                    tuple(_render_artifact(item, context) for item in stage.outputs),
                    tuple(item for item in dependencies[name] if item in selected),
                    (
                        _render(
                            stage.working_directory,
                            context,
                            f"stage {name!r}",
                        )
                        if stage.working_directory
                        else None
                    ),
                    stage.submission,
                    stage.provider,
                )
            )
        return tuple(planned)


def _coerce_extension(value: object, entry_point: EntryPoint) -> tuple[StageSpec, ...]:
    if callable(value) and not isinstance(value, type):
        value = value()
    if isinstance(value, StageSpec):
        return (value,)
    if isinstance(value, StageExtension):
        return value.stages
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        stages = tuple(value)
        if all(isinstance(stage, StageSpec) for stage in stages):
            return stages
    raise RegistryError(
        f"entry point {entry_point.name!r} must provide StageSpec, "
        "StageExtension, or an iterable of StageSpec"
    )


def load_stage_extensions(
    registry: StageRegistry,
    *,
    discovered: Iterable[EntryPoint] | None = None,
) -> None:
    """Load installed extensions in deterministic entry-point-name order."""

    found = entry_points(group=ENTRY_POINT_GROUP) if discovered is None else discovered
    for entry_point in sorted(found, key=lambda item: item.name):
        try:
            distribution = getattr(entry_point, "dist", None)
            provider = f"entry-point:{entry_point.name}"
            if distribution is not None:
                package = distribution.metadata.get("Name", distribution.name)
                provider = f"{package}=={distribution.version}:{entry_point.name}"
            stages = _coerce_extension(entry_point.load(), entry_point)
            for stage in stages:
                registry.register(stage, provider=provider)
        except Exception as error:
            raise RegistryError(
                f"invalid pipeline entry point {entry_point.name!r}: {error}"
            ) from error


COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        ("pipeline",), "network_fmri.pipeline:main", "submit the cohort pipeline"
    ),
    CommandSpec(
        ("integration",),
        "network_fmri.integrations.cli:main",
        "list, validate, or execute package integrations",
    ),
    CommandSpec(
        ("submit", "fw-heudiconv"),
        "network_fmri.fw2bids.jobs:submit",
        "submit an export array",
    ),
    CommandSpec(("curate",), "network_fmri.fw2bids.curate:main", "curate one subject"),
    CommandSpec(
        ("import-subject",),
        "network_fmri.fw2bids.jobs:import_subject",
        "export one subject with provenance",
    ),
    CommandSpec(("merge",), "network_fmri.fw2bids.jobs:merge", "merge subject exports"),
    CommandSpec(
        ("fix-sidecars",), "network_fmri.prepare.sidecars:record", "fix BIDS sidecars"
    ),
    CommandSpec(
        ("fix-sidecars-run",),
        "network_fmri.prepare.sidecars:main",
        "inner sidecar command",
    ),
    CommandSpec(
        ("validate",), "network_fmri.qa.validate:main", "run the BIDS validator"
    ),
    CommandSpec(("check",), "network_fmri.qa.check:main", "check study invariants"),
    CommandSpec(
        ("global-signal",),
        "network_fmri.qa.globalsignal:record",
        "run global-signal QA",
    ),
    CommandSpec(
        ("trim",), "network_fmri.prepare.trim:record", "trim BOLD dummy volumes"
    ),
    CommandSpec(
        ("trim-bold",), "network_fmri.prepare.trim:main", "inner BOLD trim command"
    ),
    CommandSpec(
        ("b0link",),
        "network_fmri.prepare.b0link:record",
        "link field maps to BOLD runs",
    ),
    CommandSpec(
        ("b0link-run",), "network_fmri.prepare.b0link:main", "inner B0-link command"
    ),
    CommandSpec(
        ("ingest-beh",),
        "network_fmri.behavior.ingest:record",
        "ingest behavioural data",
    ),
    CommandSpec(
        ("qa-reject",),
        "network_fmri.fw2bids.qa_reject:main",
        "mark rejected source scans",
    ),
    CommandSpec(
        ("qa-motion",), "network_fmri.qa.exclusions:motion", "compile motion exclusions"
    ),
    CommandSpec(
        ("qa-lev1",), "network_fmri.qa.exclusions:lev1", "compile level-1 exclusions"
    ),
    CommandSpec(
        ("glm-lev1",), "network_fmri.glm.submit:lev1", "submit first-level GLMs"
    ),
    CommandSpec(
        ("glm-lev2",), "network_fmri.glm.submit:lev2", "submit second-level GLMs"
    ),
    CommandSpec(
        ("glm-outliers",),
        "network_fmri.glm.submit:outliers",
        "submit cohort outlier QA",
    ),
    CommandSpec(("mriqc-iqms",), "network_fmri.qa.mriqc:record", "unpack MRIQC IQMs"),
    CommandSpec(
        ("mriqc-iqms-run",), "network_fmri.qa.mriqc:main", "inner MRIQC unpack command"
    ),
    CommandSpec(
        ("fmriprep-derivs",),
        "network_fmri.qa.fmriprep:record",
        "unpack fMRIPrep derivatives",
    ),
    CommandSpec(
        ("fmriprep-derivs-run",),
        "network_fmri.qa.fmriprep:main",
        "inner fMRIPrep unpack command",
    ),
    CommandSpec(
        ("campaign",), "network_fmri.qa.campaign:main", "drive a mechababs campaign"
    ),
    CommandSpec(
        ("shim",),
        "network_fmri.qa.shim:main",
        "build and vendor a pipeline container shim",
    ),
)


def _validate_commands(commands: Sequence[CommandSpec]) -> None:
    routes: set[tuple[str, ...]] = set()
    for command in commands:
        if command.route in routes:
            raise RegistryError(f"duplicate command {command.display_name!r}")
        routes.add(command.route)


_validate_commands(COMMANDS)


def resolve_command(
    argv: Sequence[str], commands: Sequence[CommandSpec] = COMMANDS
) -> tuple[CommandSpec, list[str]] | None:
    for command in sorted(commands, key=lambda item: len(item.route), reverse=True):
        if tuple(argv[: len(command.route)]) == command.route:
            return command, list(argv[len(command.route) :])
    return None


def command_usage(commands: Sequence[CommandSpec] = COMMANDS) -> str:
    width = max(len(item.display_name) for item in commands)
    lines = ["usage: network_fmri COMMAND [options]", "", "commands:"]
    lines.extend(f"  {item.display_name:<{width}}  {item.summary}" for item in commands)
    return "\n".join(lines) + "\n"


def _artifact(
    name: str, location: str, description: str, external: bool = False
) -> ArtifactSpec:
    return ArtifactSpec(name, location, description, external)


FLYWHEEL = _artifact(
    "flywheel-project", "flywheel://{project}", "Flywheel project", True
)
BEHAVIOR = _artifact(
    "behavior-source",
    "/oak/stanford/groups/russpold/data/network_grant/behavioral_data/canonical",
    "canonical behavior DataLad dataset at commit 445eba8",
    True,
)
PARTS = _artifact(
    "subject-bids-parts", "{staging}/{cohort}/parts", "per-subject BIDS datasets"
)
MERGED = _artifact("merged-bids", "{bids_dir}", "merged BIDS state")
SIDECARS = _artifact("fixed-sidecars", "{bids_dir}", "sidecar-fixed BIDS state")
PREVALID = _artifact("validated-pre", "{bids_dir}", "pre-trim validated BIDS state")
GS_PRE = _artifact(
    "global-signal-pre", "{bids_dir}/derivatives/global_signal/pre-trim", "pre-trim QA"
)
TRIMMED = _artifact("trimmed-bids", "{bids_dir}", "trimmed BIDS state")
B0LINKED = _artifact("b0linked-bids", "{bids_dir}", "field-map-linked BIDS state")
GS_POST = _artifact(
    "global-signal-post",
    "{bids_dir}/derivatives/global_signal/post-trim",
    "post-trim QA",
)
BEHAVIOR_IN = _artifact(
    "behavior-ingested", "{bids_dir}/sourcedata", "ingested behavior"
)
EVENTS = _artifact("events-created", "{bids_dir}", "event-complete BIDS state")
POSTVALID = _artifact(
    "validated-post", "{bids_dir}", "post-events validated BIDS state"
)
CHECKED = _artifact("checked-bids", "{bids_dir}", "study-invariant-checked BIDS state")


def builtin_stages() -> tuple[StageSpec, ...]:
    """Return the built-in straight-line cohort chain."""

    nf = "{network_fmri}"
    cohort = ("--cohort", "{cohort}", "--staging", "{staging}")
    return (
        StageSpec(
            "export",
            "curate and export one BIDS dataset per subject",
            SlurmResources(2, "8G", "08:00:00"),
            (
                nf,
                "submit",
                "fw-heudiconv",
                *cohort,
                "--project",
                "{project}",
                "--partition",
                "{partition}",
                "--throttle",
                "{throttle}",
                "{live_flag}",
            ),
            (FLYWHEEL,),
            (PARTS,),
            submission=SubmissionKind.EXPORT_ARRAY,
        ),
        StageSpec(
            "merge",
            "merge subject exports",
            SlurmResources(2, "8G", "12:00:00"),
            (nf, "merge", *cohort),
            (PARTS,),
            (MERGED,),
            after=("export",),
        ),
        StageSpec(
            "fix-sidecars",
            "coerce sidecar fields to BIDS types",
            SlurmResources(2, "8G", "02:00:00"),
            (nf, "fix-sidecars", *cohort),
            (MERGED,),
            (SIDECARS,),
            after=("merge",),
        ),
        StageSpec(
            "validate-pre",
            "validate before destructive preparation",
            SlurmResources(2, "8G", "04:00:00"),
            (nf, "validate", *cohort, "--", "--ignoreWarnings"),
            (SIDECARS,),
            (PREVALID,),
            after=("fix-sidecars",),
        ),
        StageSpec(
            "gs-pre",
            "capture pre-trim global-signal QA",
            SlurmResources(4, "16G", "12:00:00"),
            (nf, "global-signal", *cohort, "--label", "pre-trim", "--tr-marker", "7"),
            (PREVALID,),
            (GS_PRE,),
            after=("validate-pre",),
        ),
        StageSpec(
            "trim",
            "remove the first seven BOLD volumes",
            SlurmResources(16, "32G", "08:00:00"),
            (nf, "trim", *cohort, "--jobs", "16"),
            (PREVALID,),
            (TRIMMED,),
            after=("gs-pre",),
        ),
        StageSpec(
            "b0link",
            "link field maps to BOLD runs",
            SlurmResources(2, "8G", "02:00:00"),
            (nf, "b0link", *cohort),
            (TRIMMED,),
            (B0LINKED,),
            after=("trim",),
        ),
        StageSpec(
            "gs-post",
            "capture post-trim global-signal QA",
            SlurmResources(4, "16G", "12:00:00"),
            (nf, "global-signal", *cohort, "--label", "post-trim"),
            (B0LINKED,),
            (GS_POST,),
            after=("b0link",),
        ),
        StageSpec(
            "ingest-beh",
            "ingest canonical behavioural data",
            SlurmResources(2, "8G", "02:00:00"),
            (nf, "ingest-beh", *cohort),
            (B0LINKED, BEHAVIOR),
            (BEHAVIOR_IN,),
            after=("gs-post",),
        ),
        StageSpec(
            "events",
            "generate BIDS events TSVs",
            SlurmResources(4, "16G", "08:00:00"),
            ("{events_bin}", "create", "--sourcedata", "sourcedata", "--bids-dir", "."),
            (B0LINKED, BEHAVIOR_IN),
            (EVENTS,),
            after=("ingest-beh",),
            working_directory="{bids_dir}",
        ),
        StageSpec(
            "validate-post",
            "validate after preparation and events",
            SlurmResources(2, "8G", "04:00:00"),
            (nf, "validate", *cohort, "--", "--ignoreWarnings"),
            (EVENTS,),
            (POSTVALID,),
            after=("events",),
        ),
        StageSpec(
            "check",
            "assert study-specific invariants",
            SlurmResources(2, "8G", "02:00:00"),
            (nf, "check", *cohort),
            (POSTVALID,),
            (CHECKED,),
            after=("validate-post",),
        ),
    )


def pipeline_registry(*, include_extensions: bool = True) -> StageRegistry:
    registry = StageRegistry(builtin_stages())
    if include_extensions:
        load_stage_extensions(registry)
    return registry
