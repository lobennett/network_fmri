"""Run the BIDS Validator and retain its evidence for every outcome."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from network_fmri.models import Runner

_LABEL = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True)
class ValidationResult:
    """The durable evidence produced by one BIDS Validator invocation."""

    label: str
    report: Path
    log: Path
    returncode: int


class ValidationError(RuntimeError):
    """The validator reported errors after its diagnostics were persisted."""

    def __init__(self, result: ValidationResult):
        self.result = result
        super().__init__(
            f"BIDS validation failed for {result.label!r} (exit {result.returncode}); "
            f"diagnostics: {result.report}, {result.log}"
        )


def validate_bids(
    bids_dir: Path,
    label: str,
    runner: Runner = subprocess.run,
) -> ValidationResult:
    """Validate a dataset, retaining JSON and text diagnostics before failing.

    The validator itself owns the detailed JSON report. A marked fallback is
    written only when the executable failed before creating its requested file.
    """

    if not _LABEL.fullmatch(label):
        raise ValueError(f"validation label is unsafe: {label!r}")
    bids_dir = Path(bids_dir)
    output_dir = bids_dir / "derivatives" / "bids-validator"
    output_dir.mkdir(parents=True, exist_ok=True)
    report = output_dir / f"desc-{label}_validation.json"
    log = report.with_suffix(".log")
    command = [
        "bids-validator", str(bids_dir), "--outfile", str(report), "--format",
        "json_pp", "--prune",
    ]
    returncode, stdout, stderr = _invoke(command, runner)
    _ensure_report(report, label)
    _write_log(log, stdout, stderr)
    result = ValidationResult(label, report, log, returncode)
    if returncode:
        raise ValidationError(result)
    return result


def _invoke(command: list[str], runner: Runner) -> tuple[int, str, str]:
    try:
        completed = runner(command, check=False, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        return error.returncode or 1, _as_text(error.output), _as_text(error.stderr)
    except OSError as error:
        return 127, "", str(error)
    return int(getattr(completed, "returncode", 0) or 0), _as_text(
        getattr(completed, "stdout", "")
    ), _as_text(getattr(completed, "stderr", ""))


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _ensure_report(path: Path, label: str) -> None:
    if path.is_file():
        return
    fallback = {
        "label": label,
        "status": "validator-output-missing",
        "detail": "bids-validator did not create the requested JSON output",
    }
    path.write_text(json.dumps(fallback, indent=2) + "\n", encoding="utf-8")


def _write_log(path: Path, stdout: str, stderr: str) -> None:
    path.write_text(stdout + ("\n" if stdout and stderr else "") + stderr, encoding="utf-8")


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="network_fmri validate")
    parser.add_argument("--bids-dir", required=True, type=Path)
    parser.add_argument("--label", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    validate_bids(args.bids_dir, args.label)
    return 0
