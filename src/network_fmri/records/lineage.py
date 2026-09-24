"""Validate portable file provenance receipts without loading file contents."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath


def artifact_id(dataset_id: str, path: str, content_id: str) -> str:
    encoded = json.dumps([dataset_id, path, content_id], separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _text(value, key):
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"missing provenance {key}")
    return result


def read_receipt(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError(f"unsupported provenance schema: {path}")
    for key in ("artifacts", "attempts", "links"):
        if not isinstance(value.get(key), list) or any(not isinstance(row, dict) for row in value[key]):
            raise ValueError(f"invalid provenance {key}: {path}")
    artifacts, attempts = set(), set()
    for item in value["artifacts"]:
        identity = _text(item, "id")
        dataset, relative, content = (_text(item, k) for k in ("dataset_id", "path", "content_id"))
        posix = PurePosixPath(relative)
        if (posix.is_absolute() or ".." in posix.parts or "\\" in relative or "\x00" in relative
                or relative == "." or posix.as_posix() != relative):
            raise ValueError("unsafe provenance path")
        if identity != artifact_id(dataset, relative, content) or identity in artifacts:
            raise ValueError("duplicate or inconsistent artifact identity")
        if item.get("availability") not in {"available", "unavailable", "remote", "temporary", "historical"}:
            raise ValueError("invalid artifact availability")
        if not isinstance(item.get("source_ids", {}), dict):
            raise ValueError("invalid artifact source IDs")
        artifacts.add(identity)
    for item in value["attempts"]:
        identity = _text(item, "id")
        for key in ("stage", "scope", "status"):
            _text(item, key)
        if identity in attempts:
            raise ValueError("duplicate attempt identity")
        attempts.add(identity)
    links = set()
    for item in value["links"]:
        key = tuple(_text(item, k) for k in ("input", "attempt", "output", "relation"))
        if key[0] not in artifacts or key[2] not in artifacts or key[1] not in attempts:
            raise ValueError("dangling provenance link")
        if key in links or key[0] == key[2]:
            raise ValueError("duplicate or self-referential provenance link")
        links.add(key)
    return value


def collect_receipts(study: Path) -> tuple[dict, ...]:
    receipts = []
    for path in sorted(study.glob("**/code/*/lineage/*.json")):
        if {".git", ".babs", ".mechababs", "containers"}.intersection(path.relative_to(study).parts):
            continue
        receipts.append(read_receipt(path))
    return tuple(receipts)
