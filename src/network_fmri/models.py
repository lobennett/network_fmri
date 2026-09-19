"""Small shared models for the single-dataset orchestration pipeline."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Runner(Protocol):
    """The callable subset of :func:`subprocess.run` used by stage services."""

    def __call__(
        self,
        args: Sequence[str | Path],
        *,
        check: bool = False,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]: ...


@dataclass(frozen=True)
class StageResult:
    """The declared products of one successfully completed pipeline stage."""

    name: str
    outputs: tuple[Path, ...]
    details: dict[str, object] = field(default_factory=dict)
