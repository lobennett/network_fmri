"""Version 1 of the public lifecycle-integration contract.

External packages may expose an :class:`IntegrationSpec` through the
``network_fmri.integrations.v1`` entry-point group. The public types deliberately do
not expose the pipeline planner's internal stage model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

API_VERSION = 1

_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
_MEMORY = re.compile(r"^[1-9][0-9]*(?:[KMGT])$")
_TIME = re.compile(r"^(?:[0-9]+-)?[0-9]{2}:[0-9]{2}:[0-9]{2}$")


class IntegrationCategory(StrEnum):
    """Broad scientific role used for configuration and documentation."""

    PREPROCESSING = "preprocessing"
    QUALITY_CONTROL = "quality-control"
    ANALYSIS = "analysis"


class LifecycleSlot(StrEnum):
    """Stable pipeline boundaries available to integrations."""

    PRE_TRIM = "pre-trim"
    PRE_FMRIPREP = "pre-fmriprep"
    POST_FMRIPREP = "post-fmriprep"
    ANALYSIS = "analysis"


class Effect(StrEnum):
    """How an integration relates to its primary input artifact."""

    READ_ONLY = "read-only"
    IN_PLACE = "in-place"
    DERIVATIVE = "derivative"


@dataclass(frozen=True)
class IntegrationResources:
    """Slurm resources for the integration's cohort job."""

    cpus: int = 1
    memory: str = "4G"
    time_limit: str = "01:00:00"

    def __post_init__(self) -> None:
        if self.cpus < 1 or not _MEMORY.fullmatch(self.memory):
            raise ValueError(f"invalid integration resources {self!r}")
        if not _TIME.fullmatch(self.time_limit):
            raise ValueError(f"invalid time limit {self.time_limit!r}")


@dataclass(frozen=True)
class IntegrationOutput:
    """A path an integration promises to create."""

    name: str
    location: str
    description: str

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name) or not self.location or not self.description:
            raise ValueError(f"invalid integration output {self!r}")


@dataclass(frozen=True)
class IntegrationSpec:
    """One external command attached to a stable lifecycle boundary.

    ``requires`` contains additional path templates that must exist before execution.
    The slot's primary artifact is always checked automatically. Commands are argv
    tokens, never shell snippets, and may use the documented pipeline placeholders.
    """

    name: str
    package: str
    description: str
    category: IntegrationCategory
    slot: LifecycleSlot
    effect: Effect
    command: tuple[str, ...]
    resources: IntegrationResources = IntegrationResources()
    requires: tuple[str, ...] = ()
    outputs: tuple[IntegrationOutput, ...] = ()
    enabled: bool = False
    source: str | None = None

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise ValueError(f"invalid integration name {self.name!r}")
        if not self.package or not self.description or not self.command:
            raise ValueError(f"incomplete integration declaration {self.name!r}")
        if any(not token for token in self.command):
            raise ValueError(f"integration {self.name!r} has an empty command token")
        if any(not path for path in self.requires):
            raise ValueError(f"integration {self.name!r} has an empty requirement")
        if self.effect == Effect.DERIVATIVE and not self.outputs:
            raise ValueError(
                f"derivative integration {self.name!r} must declare an output"
            )
