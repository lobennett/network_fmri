"""Reapply human decisions only when regenerated evidence is equivalent."""

from __future__ import annotations

import csv
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from network_fmri.models import StageResult


_BIDS_KEYS = (
    "record_type", "subject", "session", "datatype", "suffix", "task",
    "acquisition", "direction", "run",
)
_REVIEW_FIELDS = frozenset({
    "decision", "approved", "reason_code", "reason_detail", "reviewer",
    "reviewed_at", "notes",
})


@dataclass(frozen=True)
class ReviewMigrationResult:
    manifest: Path
    mismatch_report: Path
    approved: bool
    rows: int


class ReviewMigrator:
    """Regenerate evidence, compare it, then copy only human-authored fields."""

    def migrate_scan(
        self,
        source_manifest: Path,
        *,
        installed_raw: Path,
        mriqc_derivative: Path,
        regenerate: Callable[[Path, Path], StageResult],
        validate_source: Callable[[], object],
        approve: Callable[[], StageResult],
        save_approval: Callable[[StageResult], object],
    ) -> ReviewMigrationResult:
        validate_source()
        return self._migrate(
            source_manifest,
            regenerate(installed_raw, mriqc_derivative),
            approve=approve,
            save_approval=save_approval,
        )

    def migrate_surface(
        self,
        source_manifest: Path,
        *,
        installed_raw: Path,
        anatomical_derivative: Path,
        regenerate: Callable[[Path, Path], StageResult],
        validate_source: Callable[[], object],
        approve: Callable[[], StageResult],
        save_approval: Callable[[StageResult], object],
        source_fingerprints: Mapping[str, str] | None = None,
    ) -> ReviewMigrationResult:
        validate_source()
        return self._migrate(
            source_manifest,
            regenerate(installed_raw, anatomical_derivative),
            approve=approve,
            save_approval=save_approval,
            source_fingerprints=source_fingerprints,
        )

    def _migrate(
        self,
        source_manifest: Path,
        generated: StageResult,
        *,
        approve: Callable[[], StageResult],
        save_approval: Callable[[StageResult], object],
        source_fingerprints: Mapping[str, str] | None = None,
    ) -> ReviewMigrationResult:
        manifest = _manifest_output(generated)
        report = manifest.with_name(manifest.stem + ".migration.json")
        source_fields, source_rows = _read_tsv(source_manifest)
        generated_fields, generated_rows = _read_tsv(manifest)
        if (
            source_fingerprints is not None
            and "surface_fingerprint" not in source_fields
            and "surface_fingerprint" in generated_fields
        ):
            source_fields = generated_fields
            for row in source_rows:
                fingerprint = source_fingerprints.get(row["subject"])
                if fingerprint:
                    row["surface_fingerprint"] = fingerprint
        mismatches = _compare(source_fields, source_rows, generated_fields, generated_rows)
        if mismatches:
            _write_json_atomic(report, {"schema_version": 1, "mismatches": mismatches})
            return ReviewMigrationResult(manifest, report, False, len(generated_rows))

        source_by_key = {_key(row, source_fields): row for row in source_rows}
        for row in generated_rows:
            old = source_by_key[_key(row, generated_fields)]
            for field in generated_fields:
                if field in _REVIEW_FIELDS:
                    row[field] = old[field]
        _write_tsv_atomic(manifest, generated_fields, generated_rows)
        report.unlink(missing_ok=True)
        approval = approve()
        if not isinstance(approval, StageResult):
            raise RuntimeError("review approval did not return a StageResult")
        save_approval(approval)
        return ReviewMigrationResult(manifest, report, True, len(generated_rows))


def _manifest_output(result: StageResult) -> Path:
    manifests = [path for path in result.outputs if path.suffix == ".tsv"]
    if len(manifests) != 1:
        raise RuntimeError("review regeneration must produce exactly one TSV manifest")
    return manifests[0]


def _read_tsv(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    try:
        with path.open(newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t", strict=True)
            fields = tuple(reader.fieldnames or ())
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as error:
        raise RuntimeError(f"cannot read review manifest: {path}") from error
    if not fields or not rows:
        raise RuntimeError(f"review manifest is empty: {path}")
    if "subject" not in fields:
        raise RuntimeError(f"review manifest has no subject key: {path}")
    return fields, rows


def _compare(
    source_fields: tuple[str, ...],
    source_rows: list[dict[str, str]],
    generated_fields: tuple[str, ...],
    generated_rows: list[dict[str, str]],
) -> list[dict[str, object]]:
    if source_fields != generated_fields:
        return [{"reason": "schema-changed", "source": source_fields, "generated": generated_fields}]
    source = _unique_rows(source_rows, source_fields, "source")
    generated = _unique_rows(generated_rows, generated_fields, "generated")
    mismatches: list[dict[str, object]] = []
    for key in sorted(source.keys() - generated.keys()):
        mismatches.append({"reason": "missing-generated-row", "key": list(key)})
    for key in sorted(generated.keys() - source.keys()):
        mismatches.append({"reason": "new-generated-row", "key": list(key)})
    evidence_fields = tuple(
        field for field in source_fields
        if field not in _REVIEW_FIELDS
        and field not in _BIDS_KEYS
        and not field.endswith(("_path", "_dir"))
    )
    for key in sorted(source.keys() & generated.keys()):
        changed = [
            field for field in evidence_fields
            if source[key].get(field, "") != generated[key].get(field, "")
        ]
        if changed:
            mismatches.append({"reason": "evidence-changed", "key": list(key), "fields": changed})
    return mismatches


def _unique_rows(
    rows: list[dict[str, str]], fields: tuple[str, ...], label: str
) -> dict[tuple[str, ...], dict[str, str]]:
    indexed: dict[tuple[str, ...], dict[str, str]] = {}
    for row in rows:
        key = _key(row, fields)
        if key in indexed:
            raise RuntimeError(f"{label} review manifest has duplicate BIDS key: {key}")
        indexed[key] = row
    return indexed


def _key(row: dict[str, str], fields: tuple[str, ...]) -> tuple[str, ...]:
    keys = tuple(field for field in _BIDS_KEYS if field in fields)
    if keys == ("subject",):
        return (_entity(row["subject"], "sub-"),)
    values = []
    for field in keys:
        value = row.get(field, "").strip()
        if field == "subject":
            value = _entity(value, "sub-")
        elif field == "session":
            value = _entity(value, "ses-")
        elif field == "run" and value.isdigit():
            value = str(int(value))
        values.append(value)
    return tuple(values)


def _entity(value: str, prefix: str) -> str:
    return value.removeprefix(prefix)


def _write_tsv_atomic(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
