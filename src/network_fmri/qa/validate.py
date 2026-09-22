"""Run the BIDS Validator and retain fresh evidence for every outcome."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
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
    """The validator did not produce a valid, successful report."""

    def __init__(self, result: ValidationResult):
        self.result = result
        super().__init__(
            f"BIDS validation failed for {result.label!r} (exit {result.returncode}); "
            f"diagnostics: {result.report}, {result.log}"
        )


def validate_bids(
    bids_dir: Path,
    label: str,
    validator_image: Path,
    runner: Runner = subprocess.run,
) -> ValidationResult:
    """Validate a dataset and atomically publish fresh report and log artifacts.

    The validator writes to a same-directory temporary file. A previous report can
    therefore never make a failed or incomplete later invocation appear valid. If
    the program does not write a JSON object, a labelled diagnostic JSON replaces
    the old report and the stage fails even when the process exit code was zero.
    """

    if not _LABEL.fullmatch(label):
        raise ValueError(f"validation label is unsafe: {label!r}")
    bids_dir = Path(bids_dir)
    output_dir = bids_dir / "derivatives" / "bids-validator"
    output_dir.mkdir(parents=True, exist_ok=True)
    report = output_dir / f"desc-{label}_validation.json"
    log = report.with_suffix(".log")
    temporary_report = _temporary_path(output_dir, f".{report.name}.")
    temporary_log = _temporary_path(output_dir, f".{log.name}.")
    try:
        command = [
            "apptainer", "exec",
            "--bind", f"{bids_dir}:/data:ro",
            "--bind", f"{output_dir}:/out",
            str(validator_image), "deno", "-A", "/src/bids-validator.js", "/data",
            "--outfile", f"/out/{temporary_report.name}",
            "--format", "json_pp", "--prune",
        ]
        returncode, stdout, stderr = _invoke(command, runner)
        output_error = _report_error(temporary_report)
        if output_error:
            _write_json(temporary_report, {
                "label": label,
                "status": output_error,
                "detail": "BIDS Validator did not produce a fresh JSON object",
            })
        _write_text(temporary_log, _join_output(stdout, stderr))
        os.replace(temporary_report, report)
        os.replace(temporary_log, log)
    finally:
        temporary_report.unlink(missing_ok=True)
        temporary_log.unlink(missing_ok=True)
    result = ValidationResult(label, report, log, returncode or (1 if output_error else 0))
    if result.returncode:
        raise ValidationError(result)
    return result


def _temporary_path(directory: Path, prefix: str) -> Path:
    descriptor, name = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=".tmp")
    os.close(descriptor)
    path = Path(name)
    path.unlink()
    return path


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


def _report_error(path: Path) -> str | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "validator-output-missing"
    return None if isinstance(value, dict) else "validator-output-invalid"


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _join_output(stdout: str, stderr: str) -> str:
    return stdout + ("\n" if stdout and stderr else "") + stderr


def _write_json(path: Path, value: dict[str, str]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="network_fmri validate")
    parser.add_argument("--bids-dir", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--image", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    validate_bids(args.bids_dir, args.label, args.image)
    return 0
