"""Load and validate versioned integration manifests."""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

from network_fmri.integrations.v1 import (
    API_VERSION,
    Effect,
    IntegrationCategory,
    IntegrationOutput,
    IntegrationResources,
    IntegrationSpec,
    LifecycleSlot,
)

ENTRY_POINT_GROUP = "network_fmri.integrations.v1"
CATALOG = Path(__file__).parent / "catalog"
_TOP_LEVEL = {
    "api_version",
    "name",
    "package",
    "description",
    "category",
    "slot",
    "effect",
    "enabled",
    "command",
    "requires",
    "resources",
    "outputs",
}


class ManifestError(ValueError):
    """An integration manifest or activation request is invalid."""


def _expect_keys(value: dict[str, object], allowed: set[str], owner: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ManifestError(f"{owner} has unknown keys: {', '.join(unknown)}")


def _strings(value: object, owner: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ManifestError(f"{owner} must be an array of strings")
    return tuple(value)


def load_manifest(path: Path) -> IntegrationSpec:
    """Parse one strict TOML manifest into the public v1 contract."""

    try:
        data = tomllib.loads(path.read_text())
        _expect_keys(data, _TOP_LEVEL, str(path))
        if data.get("api_version") != API_VERSION:
            raise ManifestError(f"{path} requires api_version = {API_VERSION}")
        resources_data = data.get("resources", {})
        if not isinstance(resources_data, dict):
            raise ManifestError(f"{path}: resources must be a table")
        _expect_keys(
            resources_data, {"cpus", "memory", "time_limit"}, f"{path}: resources"
        )
        resources = IntegrationResources(**resources_data)

        outputs_data = data.get("outputs", [])
        if not isinstance(outputs_data, list):
            raise ManifestError(f"{path}: outputs must be an array of tables")
        outputs = []
        for index, output in enumerate(outputs_data):
            if not isinstance(output, dict):
                raise ManifestError(f"{path}: outputs[{index}] must be a table")
            _expect_keys(
                output,
                {"name", "location", "description"},
                f"{path}: outputs[{index}]",
            )
            outputs.append(IntegrationOutput(**output))

        enabled = data.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ManifestError(f"{path}: enabled must be true or false")
        spec = IntegrationSpec(
            name=data["name"],
            package=data["package"],
            description=data["description"],
            category=IntegrationCategory(data["category"]),
            slot=LifecycleSlot(data["slot"]),
            effect=Effect(data["effect"]),
            command=_strings(data["command"], f"{path}: command"),
            resources=resources,
            requires=_strings(data.get("requires", []), f"{path}: requires"),
            outputs=tuple(outputs),
            enabled=enabled,
            source=str(path),
        )
    except (KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
        if isinstance(error, ManifestError):
            raise
        raise ManifestError(f"invalid integration manifest {path}: {error}") from error
    return spec


def manifest_paths(extra_directories: Iterable[Path] = ()) -> tuple[Path, ...]:
    """Return catalog manifests in deterministic order."""

    directories = (CATALOG, *(Path(item) for item in extra_directories))
    paths: list[Path] = []
    for directory in directories:
        if not directory.is_dir():
            raise ManifestError(f"integration directory does not exist: {directory}")
        paths.extend(sorted(directory.glob("*.toml")))
    return tuple(paths)


def load_manifests(
    extra_directories: Iterable[Path] = (),
) -> tuple[IntegrationSpec, ...]:
    """Load all catalog and site manifests and reject duplicate names."""

    specs = tuple(load_manifest(path) for path in manifest_paths(extra_directories))
    _reject_duplicates(specs)
    return specs


def _coerce_entry_point(value: object, entry_point: EntryPoint) -> IntegrationSpec:
    if callable(value) and not isinstance(value, type):
        value = value()
    if not isinstance(value, IntegrationSpec):
        raise ManifestError(
            f"entry point {entry_point.name!r} must provide IntegrationSpec"
        )
    if value.name != entry_point.name:
        raise ManifestError(
            f"entry point {entry_point.name!r} provides integration {value.name!r}"
        )
    return value


def load_entry_points(
    enabled: Iterable[str], *, discovered: Iterable[EntryPoint] | None = None
) -> tuple[IntegrationSpec, ...]:
    """Load only explicitly enabled installed integrations."""

    requested = set(enabled)
    found = entry_points(group=ENTRY_POINT_GROUP) if discovered is None else discovered
    specs = []
    for item in sorted(found, key=lambda candidate: candidate.name):
        if item.name not in requested:
            continue
        try:
            spec = _coerce_entry_point(item.load(), item)
        except Exception as error:
            if isinstance(error, ManifestError):
                raise
            raise ManifestError(
                f"invalid integration entry point {item.name!r}: {error}"
            ) from error
        specs.append(spec)
    return tuple(specs)


def _reject_duplicates(specs: Iterable[IntegrationSpec]) -> None:
    sources: dict[str, str] = {}
    for spec in specs:
        source = spec.source or "installed entry point"
        if spec.name in sources:
            raise ManifestError(
                f"duplicate integration {spec.name!r}: {sources[spec.name]} and {source}"
            )
        sources[spec.name] = source


def resolve_integrations(
    *,
    extra_directories: Iterable[Path] = (),
    enable: Iterable[str] = (),
    disable: Iterable[str] = (),
    discovered: Iterable[EntryPoint] | None = None,
) -> tuple[IntegrationSpec, ...]:
    """Resolve explicit activation across manifests and installed entry points."""

    manifests = load_manifests(extra_directories)
    enabled = set(enable)
    disabled = set(disable)
    conflict = sorted(enabled & disabled)
    if conflict:
        raise ManifestError(
            "integrations cannot be both enabled and disabled: " + ", ".join(conflict)
        )

    entry_specs = load_entry_points(enabled, discovered=discovered)
    all_specs = (*manifests, *entry_specs)
    _reject_duplicates(all_specs)
    known = {spec.name for spec in all_specs}
    unknown = sorted(enabled - known)
    if unknown:
        raise ManifestError("unknown integrations: " + ", ".join(unknown))
    active = [
        spec
        for spec in all_specs
        if spec.name not in disabled and (spec.enabled or spec.name in enabled)
    ]
    return tuple(sorted(active, key=lambda spec: (spec.slot.value, spec.name)))
