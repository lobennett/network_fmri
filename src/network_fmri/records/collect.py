"""Collect durable study evidence into content-free dashboard records."""

from __future__ import annotations

import csv
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from network_fmri.records.entities import entity_from_path
from network_fmri.records.models import Artifact, Decision, Entity, Finding, StageAttempt


class CollectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RecordSet:
    dataset_id: str
    study_commit: str
    entities: tuple[Entity, ...]
    stage_attempts: tuple[StageAttempt, ...]
    findings: tuple[Finding, ...]
    decisions: tuple[Decision, ...]
    artifacts: tuple[Artifact, ...]


def collect_study(study: Path, runner=subprocess.run, *, raw_slot: str = "raw") -> RecordSet:
    study = Path(study).resolve()
    raw = study / "sourcedata" / raw_slot
    dataset_id = _git(runner, study, ("git", "config", "--get", "datalad.dataset.id"))
    study_commit = _git(runner, study, ("git", "rev-parse", "HEAD"))
    raw_id = _git(runner, raw, ("git", "config", "--get", "datalad.dataset.id"))
    raw_commit = _git(runner, raw, ("git", "rev-parse", "HEAD"))
    entities: dict[str, Entity] = {}
    attempts: list[StageAttempt] = []
    findings: list[Finding] = []
    decisions: list[Decision] = []
    artifacts: list[Artifact] = [
        Artifact("study", ".", kind="dataset", commit=study_commit),
        Artifact("raw", f"sourcedata/{raw_slot}", kind=f"dataset:{raw_id}", commit=raw_commit),
    ]

    for path in sorted(raw.glob("code/network_fmri/milestones/*.json")):
        value = _json(path, "milestone receipt")
        stage = _required(value, "stage", path)
        attempts.append(StageAttempt(stage, "dataset", 1, str(value.get("status", "unknown"))))
        artifacts.append(Artifact(stage, _relative(path, study), kind="receipt"))

    for path in sorted(raw.glob("code/network_fw2bids/defacing/*.json")):
        value = _json(path, "defacing receipt")
        subject = str(value.get("subject") or path.stem.removeprefix("sub-"))
        entity = Entity("raw", subject=subject)
        entities[entity.key] = entity
        attempts.append(StageAttempt("defacing", f"sub-{subject}", 1, str(value.get("status", "unknown"))))
        artifacts.append(Artifact("defacing", _relative(path, study), entity.key, "receipt"))

    for path in sorted(raw.glob("derivatives/bids-validator/*.json")):
        _json(path, "validator report")
        artifacts.append(Artifact("bids-validator", _relative(path, study), kind="report"))

    for path in sorted((study / "derivatives").glob("**/*.json")):
        if "mriqc" not in path.as_posix().lower() or not path.name.startswith("sub-"):
            continue
        value = _mriqc_metrics(_json(path, "MRIQC metric"))
        entity = entity_from_path(path.relative_to(study))
        entities[entity.key] = entity
        findings.append(Finding(
            entity.key, "mriqc", "metric", _relative(path, study),
            json.dumps(value, sort_keys=True, separators=(",", ":")),
        ))

    _collect_decisions(study / "code/network_fmri/scan_decisions.tsv", study, entities, decisions)
    _collect_exclusions(study / "code/network_fmri/analysis_exclusions.tsv", study, entities, decisions)
    _collect_surfaces(study / "code/network_fmri/surface_review.tsv", study, entities, decisions)

    for path in sorted(raw.glob("**/*_desc-truncation.json")):
        value = _json(path, "behavior truncation")
        try:
            counts = {key: value[key] for key in ("dropped_trials", "kept_trials", "total_trials")}
        except KeyError as error:
            raise CollectionError(f"malformed behavior truncation: {path}") from error
        entity = entity_from_path(path.relative_to(raw))
        entities[entity.key] = entity
        findings.append(Finding(
            entity.key, "behavior-truncation", "exclude-first-level", _relative(path, study),
            json.dumps(counts, sort_keys=True, separators=(",", ":")),
        ))
    for path in sorted(raw.glob("**/*.error.json")):
        _json(path, "event error")
        entity = entity_from_path(path.relative_to(raw))
        entities[entity.key] = entity
        findings.append(Finding(entity.key, "event-error", "error", _relative(path, study), "{}"))
    return RecordSet(
        dataset_id, study_commit, tuple(sorted(entities.values(), key=lambda item: item.key)),
        tuple(attempts), tuple(findings), tuple(decisions), tuple(artifacts),
    )


def _collect_decisions(path, study, entities, decisions):
    for row in _tsv(path, "scan decisions", required=("subject", "decision")):
        entity = _row_entity(row)
        entities[entity.key] = entity
        decisions.append(Decision(
            entity.key, "preprocessing", row["decision"], row.get("reviewer") or None,
            row.get("reason_detail") or row.get("reason_code") or None,
            row.get("reviewed_at") or None,
        ))


def _collect_exclusions(path, study, entities, decisions):
    for row in _tsv(path, "analysis exclusions", required=("subject", "decision")):
        entity = _row_entity(row)
        entities[entity.key] = entity
        decisions.append(Decision(
            entity.key, "first-level", row["decision"], row.get("reviewer") or None,
            row.get("reason") or None, row.get("reviewed_at") or None,
        ))


def _collect_surfaces(path, study, entities, decisions):
    for row in _tsv(path, "surface review", required=("subject", "approved")):
        entity = Entity("anatomical", subject=row["subject"].removeprefix("sub-"), suffix="surface")
        entities[entity.key] = entity
        decisions.append(Decision(
            entity.key, "surface", row["approved"], row.get("reviewer") or None,
            row.get("notes") or None, row.get("reviewed_at") or None,
        ))


def _row_entity(row: dict[str, str]) -> Entity:
    def value(name, prefix=""):
        item = (row.get(name) or "").strip()
        return item.removeprefix(prefix) or None
    run = value("run")
    return Entity(
        "raw", value("subject", "sub-"), value("session", "ses-"), row.get("datatype") or "func",
        value("task"), str(int(run)) if run and run.isdigit() else run,
        value("acquisition"), value("echo"), value("suffix") or "bold",
    )


def _tsv(path: Path, label: str, *, required: tuple[str, ...]):
    if not path.exists():
        return []
    try:
        with path.open(newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t", strict=True)
            fields = tuple(reader.fieldnames or ())
            if any(field not in fields for field in required):
                raise ValueError("missing required columns")
            return list(reader)
    except (OSError, UnicodeError, csv.Error, ValueError) as error:
        raise CollectionError(f"malformed {label}: {path}") from error


def _json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise TypeError("expected object")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as error:
        raise CollectionError(f"malformed {label}: {path}") from error


def _required(value: dict, key: str, path: Path) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise CollectionError(f"malformed milestone receipt: {path}")
    return item


def _git(runner, root: Path, command: tuple[str, ...]) -> str:
    try:
        return str(runner(command, cwd=str(root), check=True, capture_output=True, text=True).stdout).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise CollectionError(f"cannot read DataLad identity: {root}") from error


def _relative(path: Path, study: Path) -> str:
    return path.relative_to(study).as_posix()


def _mriqc_metrics(value: dict) -> dict[str, int | float]:
    prefixes = (
        "aor", "aqi", "cjv", "cnr", "dvars_", "efc", "fber", "fd_", "fwhm_",
        "gcor", "gsr_", "icvs_", "inu_", "qi_", "rpve_", "size_", "snr",
        "spacing_", "summary_", "tpm_overlap_", "tsnr", "wm2max",
    )
    return {
        key: metric for key, metric in value.items()
        if isinstance(metric, (int, float)) and not isinstance(metric, bool)
        and key.startswith(prefixes)
    }
