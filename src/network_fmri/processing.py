"""Study-specific review gates around upstream MechaBABS commands."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from network_fmri.campaign import Campaign, read_table
from network_fmri.config import WorkflowConfig


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
    """Retain human gates; delegate scheduling, locks, and derivatives upstream."""

    def __init__(self, config: WorkflowConfig, *, runner=subprocess.run):
        if config.mechababs is None:
            raise ValueError("workflow configuration is missing [mechababs]")
        self.workflow = config
        self.config = config.mechababs
        self.runner = runner
        self.campaign = Campaign(self.config, runner=runner)

    def plan(self) -> tuple[ProcessingStage, ...]:
        rows = read_table(self.campaign.run("status").stdout)
        source = f"sourcedata/{self.config.raw_slot}"
        by_app = {row["app"]: row for row in rows if row["source_dataset"] == source}
        if set(by_app) != {app.file.stem for app in self.config.apps}:
            raise RuntimeError("campaign does not contain the configured source and apps")
        stages = []
        predecessors_complete = True
        for app in self.config.apps:
            row = by_app[app.file.stem]
            state = row["state"]
            if row.get("jobs") == "babs status unavailable":
                state = "intervention-required"
            elif state == "merged":
                state = "complete"
            elif state == "FAILED":
                state = "intervention-required"
            elif state == "not started":
                state = "ready" if predecessors_complete else "blocked"
            elif state.startswith("waiting"):
                state = "blocked"
            source_suffix = "" if self.config.raw_slot in {"raw", "rawbids"} else f"+{self.config.raw_slot}"
            project = f"derivatives/{app.file.stem}{source_suffix}+{self.config.campaign}"
            stages.append(ProcessingStage(app.name, app.file.stem, state, project))
            predecessors_complete = predecessors_complete and state == "complete"
        return tuple(stages)

    def status(self) -> ProcessingStatus:
        stages = self.plan()
        output = self.campaign.run("jobs").stdout
        jobs = read_table(output) if output.strip() else ()
        source = f"sourcedata/{self.config.raw_slot}"
        return ProcessingStatus(stages, tuple(job for job in jobs if job["source_dataset"] == source))

    def advance(self, stage: str) -> AdvanceResult:
        names = [app.name for app in self.config.apps]
        if stage not in names:
            raise ValueError("stage must be one of: " + ", ".join(names))
        stages = self.plan()
        selected = stages[names.index(stage)]
        for predecessor in stages[:names.index(stage)]:
            if predecessor.state != "complete":
                raise RuntimeError(f"{predecessor.stage} must be complete before {stage}")
        if selected.state == "complete":
            return AdvanceResult(stage, False, selected.state)
        if selected.state not in {"ready", "active"}:
            raise RuntimeError(f"{stage} is {selected.state} and cannot advance")
        require_stage_gate(self.workflow, stage, self.runner)
        self._sync_raw_subdataset()
        self.campaign.run("iterate", "--app", selected.application, "--batch", "1")
        return AdvanceResult(stage, True, selected.state)

    def _sync_raw_subdataset(self) -> None:
        """Advance the wrapper's raw subdataset to the canonical raw commit."""

        source = self.workflow.paths.bids_dir
        installed = self.config.study_dir / "sourcedata" / self.config.raw_slot
        for dataset in (source, installed):
            if self._output(("git", "status", "--porcelain", "--ignore-submodules=none"), dataset):
                raise RuntimeError(f"raw dataset is dirty: {dataset}")
        source_commit = self._output(("git", "rev-parse", "HEAD"), source)
        installed_commit = self._output(("git", "rev-parse", "HEAD"), installed)
        if source_commit == installed_commit:
            return
        manifest = self.config.study_dir / "code/network_fmri/study.json"
        if self._output(
            ("git", "status", "--porcelain", "--", str(manifest)), self.config.study_dir
        ):
            raise RuntimeError("study identity manifest is dirty")
        try:
            identity = json.loads(manifest.read_text())
            if not isinstance(identity, dict) or identity.get("raw_commit") != installed_commit:
                raise ValueError("raw commit does not match installed subdataset")
        except (OSError, ValueError) as error:
            raise RuntimeError("study identity does not match installed raw dataset") from error
        self.runner(
            ("datalad", "update", "--how", "merge", "-d", str(installed)),
            cwd=str(self.config.study_dir), check=True,
        )
        updated_commit = self._output(("git", "rev-parse", "HEAD"), installed)
        if updated_commit != source_commit:
            raise RuntimeError("wrapper raw subdataset did not reach the canonical raw commit")
        from network_fmri.milestones import write_json_atomic

        identity["raw_commit"] = source_commit
        write_json_atomic(manifest, identity)
        self.runner(
            (
                "datalad", "save", "-d", str(self.config.study_dir), "-m",
                "Update canonical raw BIDS subdataset", str(installed), str(manifest),
            ),
            check=True,
        )

    def _output(self, command: tuple[str, ...], cwd: Path) -> str:
        return str(self.runner(
            command, cwd=str(cwd), check=True, capture_output=True, text=True,
        ).stdout).strip()



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
        if any(app.name == "anatomical" and app.file.stem == "FreeSurfer-8.2.0"
               for app in config.mechababs.apps):
            from network_fmri.surface_evidence import prepare_surface_evidence
            evidence = prepare_surface_evidence(config, runner=runner)
            metadata = config.mechababs.study_dir / "code/network_fmri/surface_review.meta.json"
            value = json.loads(metadata.read_text())
            if Path(value.get("surface_root", "")).resolve() != evidence.resolve():
                raise RuntimeError("approval does not refer to the current standalone FreeSurfer evidence")
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
