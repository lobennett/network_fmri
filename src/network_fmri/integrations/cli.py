"""Operator commands and the provenance wrapper for lifecycle integrations."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, distribution, entry_points
from pathlib import Path
from typing import Any

from network_fmri.cohorts import COHORTS, roster
from network_fmri.integrations.manifests import (
    ENTRY_POINT_GROUP,
    ManifestError,
    load_manifests,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _package_record(name: str) -> dict[str, Any]:
    try:
        installed = distribution(name)
    except PackageNotFoundError as error:
        raise RuntimeError(f"integration package is not installed: {name}") from error
    direct_url = installed.read_text("direct_url.json")
    return {
        "name": installed.metadata.get("Name", name),
        "version": installed.version,
        "direct_url": json.loads(direct_url) if direct_url else None,
    }


def run_integration(args: argparse.Namespace) -> int:
    """Check a contract, execute argv without a shell, and write a receipt."""

    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise SystemExit("integration run requires a command after --")
    receipt = Path(args.receipt)
    record: dict[str, Any] = {
        "schema_version": 1,
        "integration": args.name,
        "effect": args.effect,
        "command": command,
        "inputs": args.input,
        "outputs": args.output,
        "started_at": _now(),
        "status": "running",
    }
    try:
        record["package"] = _package_record(args.package)
        missing_inputs = [path for path in args.input if not Path(path).exists()]
        if missing_inputs:
            raise RuntimeError("missing inputs: " + ", ".join(missing_inputs))
        completed = subprocess.run(command, check=True)
        record["returncode"] = completed.returncode
        missing_outputs = [path for path in args.output if not Path(path).exists()]
        if missing_outputs:
            raise RuntimeError(
                "missing promised outputs: " + ", ".join(missing_outputs)
            )
        record["status"] = "completed"
    except Exception as error:
        record["status"] = "failed"
        record["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        record["ended_at"] = _now()
        _write_json(receipt, record)
    return 0


def verify_inputs(args: argparse.Namespace) -> int:
    """Verify an externally produced derivative before downstream submission."""

    root = Path(args.fmriprep_dir)
    description_path = root / "dataset_description.json"
    if not root.is_dir():
        raise SystemExit(f"fMRIPrep directory does not exist: {root}")
    if not description_path.is_file():
        raise SystemExit(f"missing fMRIPrep dataset description: {description_path}")
    try:
        description = json.loads(description_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"invalid fMRIPrep dataset description: {error}") from error
    generated = description.get("GeneratedBy", [])
    generators = [
        str(item.get("Name", "")) for item in generated if isinstance(item, dict)
    ]
    if not any("fmriprep" in name.lower() for name in generators):
        raise SystemExit(f"dataset does not identify fMRIPrep in GeneratedBy: {root}")
    subjects = sorted(path.name for path in root.glob("sub-*") if path.is_dir())
    if not subjects:
        raise SystemExit(f"no subject directories in fMRIPrep derivative: {root}")
    if args.cohort:
        expected = sorted(f"sub-{subject}" for subject in roster(args.cohort))
        if subjects != expected:
            missing = sorted(set(expected) - set(subjects))
            unexpected = sorted(set(subjects) - set(expected))
            raise SystemExit(
                f"fMRIPrep subjects do not match cohort {args.cohort}: "
                f"missing={missing}, unexpected={unexpected}"
            )

    exclusions = None
    exclusion_count = None
    exclusion_generators = None
    if args.exclusions_file:
        exclusions_path = Path(args.exclusions_file)
        if not exclusions_path.is_file():
            raise SystemExit(f"exclusion lockfile does not exist: {exclusions_path}")
        try:
            exclusions = json.loads(exclusions_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise SystemExit(f"invalid exclusion lockfile: {error}") from error
        if not isinstance(exclusions, dict) or not isinstance(
            exclusions.get("exclusions"), list
        ):
            raise SystemExit(
                "exclusion lockfile must contain an exclusions array and metadata"
            )
        metadata = exclusions.get("_meta")
        if not isinstance(metadata, dict):
            raise SystemExit("exclusion lockfile is missing _meta")
        if args.cohort and metadata.get("dataset") != args.cohort:
            raise SystemExit(
                f"exclusion lockfile dataset {metadata.get('dataset')!r} "
                f"does not match cohort {args.cohort!r}"
            )
        exclusion_count = len(exclusions["exclusions"])
        exclusion_generators = metadata.get("generators")

    _write_json(
        Path(args.receipt),
        {
            "schema_version": 1,
            "status": "verified",
            "verified_at": _now(),
            "cohort": args.cohort,
            "fmriprep_dir": str(root.resolve()),
            "fmriprep_generated_by": generated,
            "subjects": subjects,
            "exclusions_file": (
                str(Path(args.exclusions_file).resolve())
                if args.exclusions_file
                else None
            ),
            "n_exclusions": exclusion_count,
            "exclusion_generators": exclusion_generators,
        },
    )
    print(f"verified {len(subjects)} fMRIPrep subjects -> {args.receipt}")
    return 0


def list_integrations(args: argparse.Namespace) -> int:
    try:
        specs = load_manifests(args.integration_dir)
    except ManifestError as error:
        raise SystemExit(str(error)) from error
    for spec in specs:
        state = "enabled" if spec.enabled else "disabled"
        print(
            f"{spec.name:24s} {state:8s} {spec.slot.value:16s} "
            f"{spec.package} ({spec.source})"
        )
    manifest_names = {spec.name for spec in specs}
    for item in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda ep: ep.name):
        if item.name not in manifest_names:
            print(f"{item.name:24s} installed disabled  {item.value}")
    if not specs and not tuple(entry_points(group=ENTRY_POINT_GROUP)):
        print("no integrations found")
    return 0


def validate_integrations(args: argparse.Namespace) -> int:
    try:
        specs = load_manifests(args.integration_dir)
        if args.check_installed:
            for spec in specs:
                _package_record(spec.package)
    except (ManifestError, RuntimeError) as error:
        raise SystemExit(str(error)) from error
    print(f"valid: {len(specs)} integration manifest(s)")
    return 0


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="network_fmri integration")
    subparsers = parser.add_subparsers(dest="action", required=True)

    listing = subparsers.add_parser(
        "list", help="list catalog and installed integrations"
    )
    listing.add_argument("--integration-dir", action="append", type=Path, default=[])
    listing.set_defaults(handler=list_integrations)

    validation = subparsers.add_parser(
        "validate", help="validate integration manifests"
    )
    validation.add_argument("--integration-dir", action="append", type=Path, default=[])
    validation.add_argument("--check-installed", action="store_true")
    validation.set_defaults(handler=validate_integrations)

    run = subparsers.add_parser("run", help=argparse.SUPPRESS)
    run.add_argument("--name", required=True)
    run.add_argument("--package", required=True)
    run.add_argument("--effect", required=True)
    run.add_argument("--receipt", required=True)
    run.add_argument("--input", action="append", default=[])
    run.add_argument("--output", action="append", default=[])
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(handler=run_integration)

    verify = subparsers.add_parser("verify", help=argparse.SUPPRESS)
    verify.add_argument("--cohort", choices=list(COHORTS))
    verify.add_argument("--fmriprep-dir", required=True)
    verify.add_argument("--exclusions-file")
    verify.add_argument("--receipt", required=True)
    verify.set_defaults(handler=verify_inputs)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
