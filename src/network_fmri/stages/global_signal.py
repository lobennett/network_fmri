"""Global-signal derivative production for the current BIDS dataset."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal

from network_fmri.models import Runner, StageResult
from network_fmri.stages import StageError

_LABELS = frozenset({"pretrim", "posttrim"})


def run_global_signal(
    bids_dir: Path,
    label: Literal["pretrim", "posttrim"],
    runner: Runner = subprocess.run,
) -> StageResult:
    """Run the pinned global-signal command into a BIDS derivative dataset."""
    if label not in _LABELS:
        raise StageError(f"global-signal label must be one of {sorted(_LABELS)}, got {label!r}")
    bids_dir = Path(bids_dir)
    output = bids_dir / "derivatives" / f"gs-{label}"
    write_derivative_description(output, f"Global signal {label}")
    command = [
        "nf-global-signal",
        "--bids-dir",
        str(bids_dir),
        "--out-tsv",
        str(output / "gs_metrics.tsv"),
        "--out-pdf",
        str(output / "gs.pdf"),
    ]
    _run_checked(command, runner)
    return StageResult(f"gs-{label}", (output,))


def write_derivative_description(root: Path, name: str) -> Path:
    """Create the minimal, stable BIDS derivative metadata document."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    description = {
        "Name": name,
        "BIDSVersion": "1.10.0",
        "DatasetType": "derivative",
        "GeneratedBy": [{"Name": "global_signal_plots"}],
    }
    path = root / "dataset_description.json"
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(description, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise StageError(f"could not write derivative description: {path}") from error
    return path


def _run_checked(command: list[str], runner: Runner) -> None:
    try:
        runner(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError(f"global-signal command failed: {command[0]}") from error
