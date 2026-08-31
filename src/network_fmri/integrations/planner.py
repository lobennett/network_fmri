"""Compile public integration contracts into the existing Slurm stage planner."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from network_fmri.integrations.manifests import ManifestError, resolve_integrations
from network_fmri.integrations.v1 import Effect, IntegrationSpec, LifecycleSlot
from network_fmri.registry import (
    CHECKED,
    PREVALID,
    ArtifactSpec,
    PipelineContext,
    PlannedStage,
    RegistryError,
    SlurmResources,
    StageRegistry,
    StageSpec,
    builtin_stages,
    load_stage_extensions,
)


class PipelineProfile(StrEnum):
    """Supported lifecycle slices; Slurm remains their execution backend."""

    BIDS = "bids"
    POST_FMRIPREP = "post-fmriprep"
    ANALYSIS = "analysis"


DEFAULT_START = {
    PipelineProfile.BIDS: "export",
    PipelineProfile.POST_FMRIPREP: "fmriprep-ready",
    PipelineProfile.ANALYSIS: "analysis-ready",
}


@dataclass(frozen=True)
class IntegrationContext(PipelineContext):
    """Additional path values exposed to integration templates."""

    profile: str = PipelineProfile.BIDS.value
    fmriprep_root: str | None = None
    exclusions_path: str | None = None
    analysis_root: str | None = None

    @property
    def fmriprep_dir(self) -> str:
        return self.fmriprep_root or str(
            Path(self.bids_dir) / "derivatives" / "fmriprep"
        )

    @property
    def exclusions_file(self) -> str:
        return self.exclusions_path or str(
            Path(self.bids_dir)
            / "derivatives"
            / "qa"
            / f"{self.cohort}_motion_lock.json"
        )

    @property
    def analysis_dir(self) -> str:
        return self.analysis_root or str(Path(self.staging) / self.cohort / "analysis")

    def values(self) -> dict[str, str]:
        return super().values() | {
            "analysis_dir": self.analysis_dir,
            "exclusions_file": self.exclusions_file,
            "fmriprep_dir": self.fmriprep_dir,
            "profile": self.profile,
        }


FMRIPREP_SOURCE = ArtifactSpec(
    "fmriprep-derivatives",
    "{fmriprep_dir}",
    "external fMRIPrep BIDS derivative",
    external=True,
)
EXCLUSIONS_SOURCE = ArtifactSpec(
    "analysis-exclusions",
    "{exclusions_file}",
    "compiled first-level exclusion lockfile",
    external=True,
)
FMRIPREP_READY = ArtifactSpec(
    "fmriprep-ready",
    "{staging}/logs/{cohort}/artifacts/fmriprep-ready.json",
    "verified fMRIPrep input receipt",
)
ANALYSIS_READY = ArtifactSpec(
    "analysis-ready",
    "{staging}/logs/{cohort}/artifacts/analysis-ready.json",
    "verified analysis input receipt",
)


@dataclass(frozen=True)
class _Slot:
    profile: PipelineProfile
    primary: ArtifactSpec
    after: str
    before: str | None = None


_SLOTS = {
    LifecycleSlot.PRE_TRIM: _Slot(PipelineProfile.BIDS, PREVALID, "gs-pre", "trim"),
    LifecycleSlot.PRE_FMRIPREP: _Slot(PipelineProfile.BIDS, CHECKED, "check"),
    LifecycleSlot.POST_FMRIPREP: _Slot(
        PipelineProfile.POST_FMRIPREP, FMRIPREP_READY, "fmriprep-ready"
    ),
    LifecycleSlot.ANALYSIS: _Slot(
        PipelineProfile.ANALYSIS, ANALYSIS_READY, "analysis-ready"
    ),
}


def _profile_stages(profile: PipelineProfile) -> tuple[StageSpec, ...]:
    nf = "{network_fmri}"
    if profile == PipelineProfile.BIDS:
        return builtin_stages()
    if profile == PipelineProfile.POST_FMRIPREP:
        return (
            StageSpec(
                "fmriprep-ready",
                "verify the external fMRIPrep derivative",
                SlurmResources(1, "2G", "00:15:00"),
                (
                    nf,
                    "integration",
                    "verify",
                    "--cohort",
                    "{cohort}",
                    "--fmriprep-dir",
                    "{fmriprep_dir}",
                    "--receipt",
                    FMRIPREP_READY.location,
                ),
                (FMRIPREP_SOURCE,),
                (FMRIPREP_READY,),
            ),
        )
    return (
        StageSpec(
            "analysis-ready",
            "verify fMRIPrep derivatives and the exclusion lockfile",
            SlurmResources(1, "2G", "00:15:00"),
            (
                nf,
                "integration",
                "verify",
                "--cohort",
                "{cohort}",
                "--fmriprep-dir",
                "{fmriprep_dir}",
                "--exclusions-file",
                "{exclusions_file}",
                "--receipt",
                ANALYSIS_READY.location,
            ),
            (FMRIPREP_SOURCE, EXCLUSIONS_SOURCE),
            (ANALYSIS_READY,),
        ),
    )


def _integration_stage(
    spec: IntegrationSpec, slot: _Slot, predecessor: str
) -> StageSpec:
    receipt = ArtifactSpec(
        f"{spec.name}-receipt",
        f"{{staging}}/logs/{{cohort}}/integrations/{spec.name}.json",
        f"execution receipt for {spec.name}",
    )
    requirements = tuple(
        ArtifactSpec(
            f"{spec.name}-require-{index}",
            location,
            f"additional input {index} for {spec.name}",
            external=True,
        )
        for index, location in enumerate(spec.requires, start=1)
    )
    declared_outputs = tuple(
        ArtifactSpec(output.name, output.location, output.description)
        for output in spec.outputs
    )
    outputs = (receipt, *declared_outputs)
    if spec.effect == Effect.IN_PLACE:
        outputs = (*outputs, slot.primary)

    required_paths = (slot.primary.location, *spec.requires)
    checked_outputs = tuple(output.location for output in spec.outputs)
    if spec.effect == Effect.IN_PLACE:
        checked_outputs = (*checked_outputs, slot.primary.location)
    wrapper = [
        "{network_fmri}",
        "integration",
        "run",
        "--name",
        spec.name,
        "--package",
        spec.package,
        "--effect",
        spec.effect.value,
        "--receipt",
        receipt.location,
    ]
    for path in required_paths:
        wrapper.extend(("--input", path))
    for path in checked_outputs:
        wrapper.extend(("--output", path))
    wrapper.extend(("--", *spec.command))
    return StageSpec(
        name=spec.name,
        description=spec.description,
        resources=SlurmResources(
            spec.resources.cpus,
            spec.resources.memory,
            spec.resources.time_limit,
        ),
        command=tuple(wrapper),
        inputs=(slot.primary, *requirements),
        outputs=outputs,
        after=(predecessor,),
        before=(slot.before,) if slot.before else (),
        provider=f"integration:{spec.package}",
    )


def build_registry(
    profile: PipelineProfile | str,
    *,
    integration_directories: Iterable[Path] = (),
    enable: Iterable[str] = (),
    disable: Iterable[str] = (),
    include_legacy_extensions: bool = True,
) -> tuple[StageRegistry, tuple[IntegrationSpec, ...]]:
    """Build one profile and return its explicitly activated integrations."""

    profile = PipelineProfile(profile)
    registry = StageRegistry(_profile_stages(profile))
    if profile == PipelineProfile.BIDS and include_legacy_extensions:
        load_stage_extensions(registry)
    try:
        integrations = resolve_integrations(
            extra_directories=integration_directories,
            enable=enable,
            disable=disable,
        )
    except ManifestError as error:
        raise RegistryError(str(error)) from error

    incompatible = [
        spec for spec in integrations if _SLOTS[spec.slot].profile != profile
    ]
    if incompatible:
        details = ", ".join(f"{spec.name} ({spec.slot.value})" for spec in incompatible)
        raise RegistryError(
            f"profile {profile.value!r} cannot run integrations: {details}"
        )

    predecessors = {slot: declaration.after for slot, declaration in _SLOTS.items()}
    for spec in integrations:
        slot = _SLOTS[spec.slot]
        registry.register(
            _integration_stage(spec, slot, predecessors[spec.slot]),
            provider=f"integration:{spec.package}",
        )
        predecessors[spec.slot] = spec.name
    return registry, integrations


def plan_with_resume_guard(
    registry: StageRegistry,
    context: IntegrationContext,
    *,
    start: str,
    assume_complete: bool = False,
) -> tuple[PlannedStage, ...]:
    """Plan a resume and refuse to silently bypass enabled integrations."""

    full = registry.plan(context)
    names = [stage.name for stage in full]
    if start not in names:
        raise RegistryError(f"--from must be one of: {' '.join(names)}")
    selected_index = names.index(start)
    if not assume_complete:
        missing = []
        for stage in full[:selected_index]:
            guarded = stage.provider.startswith("integration:") or stage.name in {
                "fmriprep-ready",
                "analysis-ready",
            }
            if not guarded:
                continue
            receipt = next(
                output
                for output in stage.outputs
                if output.name.endswith("-receipt") or output.name == stage.name
            )
            if not Path(receipt.location).is_file():
                missing.append(f"{stage.name}: {receipt.location}")
        if missing:
            raise RegistryError(
                "resume would bypass integrations without receipts: "
                + "; ".join(missing)
                + ". Resume at the first missing integration or pass --assume-complete."
            )
    return registry.plan(context, start=start)
