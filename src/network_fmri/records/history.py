"""Save observed scheduler transitions; rebuilding SQLite never removes history."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

from network_fmri.records.mechababs import collect_attempts
from network_fmri.records.models import StageAttempt


def record_status(config, status, *, runner=subprocess.run):
    root = config.mechababs.study_dir
    path = root / "code/network_fmri/processing-history" / (config.mechababs.campaign + ".json")
    if path.is_symlink():
        raise RuntimeError("processing history must not be a symlink")
    value = _read(path) if path.exists() else {"schema_version": 1, "attempts": {}}
    applications = {stage.stage: stage.application for stage in status.stages}
    projects = {stage.stage: stage.project for stage in status.stages}
    changed = False
    for attempt in collect_attempts(SimpleNamespace(status=lambda: status)):
        application = applications.get(attempt.stage, attempt.stage)
        project = projects.get(attempt.stage, "")
        campaign = project.rsplit("+", 1)[-1] if project else config.mechababs.campaign
        key = hashlib.sha256(json.dumps([campaign, application,
            attempt.scope, attempt.job_id], separators=(",", ":")).encode()).hexdigest()
        payload = asdict(attempt)
        payload.pop("attempt")
        previous = value["attempts"].get(key)
        if previous and previous["latest"] == payload:
            continue
        item = previous or {"id": key, "stage": attempt.stage, "scope": attempt.scope,
            "campaign": campaign, "application": application, "observations": []}
        item["latest"] = payload
        item["status"] = attempt.state
        item["observations"].append({**payload, "observed_at": datetime.now(timezone.utc).isoformat()})
        value["attempts"][key] = item
        changed = True
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".pending")
        temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
        os.replace(temporary, path)
    if path.exists():
        # Also recover a previous interrupted save. DataLad makes no new commit
        # when this path is already saved; unrelated edits are not included.
        runner(("datalad", "save", "-m", "Record processing status", "--", str(path.relative_to(root))),
               cwd=root, check=True)


def _read(path):
    value = json.loads(path.read_text())
    if value.get("schema_version") != 1 or not isinstance(value.get("attempts"), dict):
        raise ValueError(f"unsupported processing history: {path}")
    return value


def read_history(study: Path):
    attempts, evidence = [], []
    for path in sorted((study / "code/network_fmri/processing-history").glob("*.json")):
        for item in _read(path)["attempts"].values():
            attempts.append(StageAttempt(attempt=1, **item["latest"]))
            evidence.append(item)
    attempts.sort(key=lambda row: (row.stage, row.scope, row.job_id or ""))
    evidence.sort(key=lambda row: (row["stage"], row["scope"], row["latest"].get("job_id") or ""))
    return tuple(attempts), {"schema_version": 1, "artifacts": [], "attempts": evidence, "links": []}
