"""The explicit human review gate backed by :mod:`network_qa`."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from network_fmri.models import Runner, StageResult
from network_fmri.stages import StageError


def manifest_path(bids_dir: Path) -> Path:
    """Return the one governed decision manifest location for this dataset."""

    return Path(bids_dir) / "code" / "network_fmri" / "scan_decisions.tsv"


def generate_decisions(
    bids_dir: Path,
    runner: Runner = subprocess.run,
    *,
    mriqc_dir: Path | None = None,
    output: Path | None = None,
) -> StageResult:
    """Compile MRIQC and behavioral evidence into an unapproved manifest."""

    bids_dir = Path(bids_dir)
    manifest = Path(output) if output is not None else manifest_path(bids_dir)
    mriqc = Path(mriqc_dir) if mriqc_dir is not None else bids_dir / "derivatives" / "mriqc"
    command = [
        "network-qa", "decisions", "generate", "--bids-dir", str(bids_dir),
        "--mriqc-dir", str(mriqc), "--output", str(manifest),
    ]
    _run_checked(command, runner)
    return StageResult(
        "scan-decisions-generated", (manifest, manifest.with_suffix(".meta.json"))
    )


def validate_decisions(bids_dir: Path, runner: Runner = subprocess.run) -> StageResult:
    """Validate reviewed decisions and atomically seal their approval sidecar."""

    bids_dir = Path(bids_dir)
    manifest = manifest_path(bids_dir)
    command = [
        "network-qa", "decisions", "approve", "--manifest", str(manifest),
        "--metadata", str(manifest.with_suffix(".meta.json")), "--bids-dir", str(bids_dir),
    ]
    _run_checked(command, runner)
    return StageResult(
        "scan-decisions-approved", (manifest, manifest.with_suffix(".meta.json"))
    )


def _run_checked(command: list[str], runner: Runner) -> None:
    try:
        runner(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError("scan-decision command failed; manifest remains unapproved") from error


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="network_fmri decisions")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("generate", "validate"):
        subcommand = subcommands.add_parser(command)
        subcommand.add_argument("--bids-dir", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    if args.command == "generate":
        generate_decisions(args.bids_dir)
    else:
        validate_decisions(args.bids_dir)
    return 0
