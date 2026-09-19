"""Generate BIDS events from the already-audited canonical behavioral tree."""

from __future__ import annotations

import subprocess
from pathlib import Path

from network_fmri.models import Runner, StageResult
from network_fmri.stages import StageError


def generate_events(bids_dir: Path, runner: Runner = subprocess.run) -> StageResult:
    """Create events after trimming, preserving conversion failures as QC evidence.

    ``network-events create`` performs its own identity audit before conversion.
    It returns success for completed conversions with explicit per-run errors in
    ``sourcedata/events_qc/conversion_errors.tsv``; those rows are later review
    evidence and must not be collapsed into a stage failure here.
    """
    bids_dir = Path(bids_dir)
    behavioral_dir = bids_dir / "sourcedata" / "behavioral"
    if not bids_dir.is_dir():
        raise StageError(f"BIDS directory is missing: {bids_dir}")
    if not behavioral_dir.is_dir():
        raise StageError(f"canonical behavioral sourcedata is missing: {behavioral_dir}")
    command = [
        "network-events",
        "create",
        "--bids-dir",
        str(bids_dir),
        "--behavioral-dir",
        str(behavioral_dir),
    ]
    try:
        runner(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError("event identity audit or conversion command failed") from error
    evidence = bids_dir / "sourcedata" / "events_qc" / "conversion_errors.tsv"
    return StageResult("bids-events-generated", (bids_dir,), {"conversion_errors": str(evidence)})
