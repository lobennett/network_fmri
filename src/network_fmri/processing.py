"""Small gated adapter around one MechaBABS campaign."""

from __future__ import annotations

import csv
import io
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from network_fmri.config import WorkflowConfig


_STAGES = (
    ("mriqc", "MRIQC-24.0.2"),
    ("anatomical", "fMRIPrep-25.2.5+anat"),
    ("fmriprep", "fMRIPrep-25.2.5+full"),
)
_FAILURE_STATES = frozenset({
    "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL",
    "BOOT_FAIL", "DEADLINE", "DEPENDENCY_NEVER_SATISFIED", "INVALID_DEPEND",
    "LAUNCH_FAILED",
})


@dataclass(frozen=True)
class ProcessingStage:
    stage: str
    application: str
    state: str
    project: str = ""


@dataclass(frozen=True)
class ProcessingStatus:
    stages: tuple[ProcessingStage, ...]
    jobs: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class AdvanceResult:
    stage: str
    advanced: bool
    previous_state: str


class ProcessingManager:
    """Plan, inspect, and advance one ordered MechaBABS cell at a time."""

    def __init__(self, config: WorkflowConfig, *, runner=subprocess.run) -> None:
        if config.mechababs is None:
            raise ValueError("workflow configuration is missing [mechababs]")
        self.workflow = config
        self.config = config.mechababs
        self.runner = runner

    @property
    def ledger_path(self) -> Path:
        return self.config.campaign_dir / "desc-mechababs_datasets.tsv"

    @property
    def executable(self) -> Path:
        return self.config.campaign_dir / ".venv" / "bin" / "mechababs"

    def plan(self) -> tuple[ProcessingStage, ...]:
        """Return stage readiness from the current campaign ledger."""

        return self._stages(self._ledger_row(), ())

    def status(self) -> ProcessingStatus:
        """Refresh BABS jobs and combine them with the campaign ledger."""

        result = self.runner(
            (
                str(self.executable), "status", "--campaign-path",
                str(self.config.campaign_dir), "--output", "tsv",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        returncode = getattr(result, "returncode", 0)
        if returncode not in {0, 1} or (returncode == 1 and str(result.stdout).strip()):
            raise subprocess.CalledProcessError(
                returncode, "mechababs status", output=result.stdout
            )
        jobs = self._parse_jobs(str(result.stdout))
        return ProcessingStatus(self._stages(self._ledger_row(), jobs), jobs)

    def advance(self, stage: str) -> AdvanceResult:
        """Validate one named gate and run exactly one reconciler transition."""

        names = tuple(name for name, _ in _STAGES)
        if stage not in names:
            raise ValueError("stage must be one of: " + ", ".join(names))
        self._require_clean_inputs()
        self._require_pins()
        status = self.status()
        selected = status.stages[names.index(stage)]
        for predecessor in status.stages[: names.index(stage)]:
            if predecessor.state != "complete":
                raise RuntimeError(f"{predecessor.stage} must be complete before {stage}")
        if selected.state == "complete":
            return AdvanceResult(stage, False, selected.state)
        if selected.state == "intervention-required":
            raise RuntimeError(f"{stage} requires intervention before it can advance")
        if selected.state not in {"ready", "active"}:
            raise RuntimeError(f"{stage} is {selected.state} and cannot advance")
        require_stage_gate(self.workflow, stage, self.runner)
        self.runner(
            (
                str(self.executable), "iterate", "--campaign-path",
                str(self.config.campaign_dir), "--batch", "1",
            ),
            check=True,
        )
        return AdvanceResult(stage, True, selected.state)

    def _ledger_row(self) -> dict[str, str]:
        try:
            with self.ledger_path.open(newline="") as stream:
                rows = list(csv.DictReader(stream, delimiter="\t", strict=True))
        except (OSError, UnicodeError, csv.Error) as error:
            raise RuntimeError(f"cannot read MechaBABS ledger: {self.ledger_path}") from error
        if len(rows) != 1:
            raise RuntimeError("MechaBABS campaign must contain exactly one canonical dataset")
        return rows[0]

    def _stages(
        self, row: Mapping[str, str], jobs: tuple[dict[str, str], ...]
    ) -> tuple[ProcessingStage, ...]:
        stages = []
        predecessors_complete = True
        for name, application in _STAGES:
            project = row.get(f"{application}_babs", "")
            merged = row.get(f"{application}_babs-merged", "")
            failed = any(
                job.get("pipeline") == application
                and (
                    (job.get("state") or job.get("status", "")).upper() in _FAILURE_STATES
                    or job.get("is_failed", "").lower() == "true"
                )
                for job in jobs
            )
            if merged:
                state = "complete"
            elif failed:
                state = "intervention-required"
            elif project:
                state = "active"
            elif predecessors_complete:
                state = "ready"
            else:
                state = "blocked"
            stages.append(ProcessingStage(name, application, state, project))
            predecessors_complete = predecessors_complete and state == "complete"
        return tuple(stages)

    @staticmethod
    def _parse_jobs(value: str) -> tuple[dict[str, str], ...]:
        if not value.strip():
            return ()
        reader = csv.DictReader(io.StringIO(value), delimiter="\t", strict=True)
        if not reader.fieldnames:
            raise RuntimeError("MechaBABS status returned no header")
        return tuple(dict(row) for row in reader)

    def _require_clean_inputs(self) -> None:
        roots = [
            self.config.study_dir,
            self.workflow.paths.bids_dir,
            self.config.campaign_dir,
        ]
        behavioral = self.workflow.paths.bids_dir / "sourcedata" / "behavioral"
        if behavioral.is_dir():
            roots.extend(path for path in behavioral.iterdir() if path.is_dir())
        for root in roots:
            result = self.runner(
                ("git", "status", "--porcelain"), cwd=str(root),
                check=True, capture_output=True, text=True,
            )
            if str(result.stdout).strip():
                raise RuntimeError(f"processing input is dirty: {root}")

    def _require_pins(self) -> None:
        for name, expected in (
            ("mechababs", self.config.mechababs_commit),
            ("babs", self.config.babs_commit),
        ):
            root = self.config.campaign_dir / "code" / name
            actual = self.runner(
                ("git", "rev-parse", "HEAD"), cwd=str(root),
                check=True, capture_output=True, text=True,
            )
            if str(actual.stdout).strip() != expected:
                raise RuntimeError(f"campaign {name} pin does not match workflow configuration")


def require_stage_gate(config: WorkflowConfig, stage: str, runner=subprocess.run) -> None:
    """Require the committed evidence appropriate to one processing stage."""

    if stage == "mriqc":
        _require_committed_milestone(config.paths.bids_dir, "bids-precuration-validated", runner)
        return
    from network_fmri import pipeline

    if stage == "anatomical":
        pipeline.require_committed_approval(config, runner)
        _require_committed_milestone(config.paths.bids_dir, "bids-curated-validated", runner)
        return
    if stage == "fmriprep":
        pipeline.require_committed_surface_approval(config, runner)
        return
    raise ValueError(f"unknown processing stage: {stage}")


def _require_committed_milestone(bids_dir: Path, stage: str, runner=subprocess.run) -> None:
    from network_fmri.milestones import receipt_path

    path = receipt_path(bids_dir, stage)
    try:
        working = path.read_bytes()
        relative = path.relative_to(bids_dir).as_posix()
        committed = runner(
            ("git", "show", f"HEAD:{relative}"), cwd=str(bids_dir),
            check=True, capture_output=True,
        ).stdout
        if isinstance(committed, str):
            committed = committed.encode()
        value = json.loads(working)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"{stage} milestone is not committed") from error
    if working != committed or not isinstance(value, dict) or value.get("status") != "success":
        raise RuntimeError(f"{stage} milestone is not committed")
