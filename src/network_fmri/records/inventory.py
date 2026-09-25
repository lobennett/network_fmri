"""Inventory tracked versions without downloading unavailable annex objects."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess

from network_fmri.records.lineage import artifact_id

_ANNEX = re.compile(r"^SHA256E?-s\d+--([a-f0-9]{64})(?:\.|$)")
_ANNEX_MD5 = re.compile(r"^MD5E?-s\d+--([a-f0-9]{32})(?:\.|$)")
_EXCLUDED = {".git", ".datalad", ".babs", ".mechababs", "containers"}


def _git(root, *args):
    return subprocess.check_output(("git", "-C", str(root), *args))


def inventory_dataset(root: Path, identity: str) -> dict:
    commit = _git(root, "rev-parse", "HEAD").decode().strip()
    artifacts = []
    for entry in _git(root, "ls-tree", "-rz", "HEAD").split(b"\0"):
        if not entry:
            continue
        header, relative_bytes = entry.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        relative = relative_bytes.decode()
        if kind != "blob" or _EXCLUDED.intersection(Path(relative).parts):
            continue
        path = root / relative
        availability = "available" if path.is_file() else "unavailable"
        observation = None
        content = "gitblob:" + blob
        if path.is_symlink():
            link = os.readlink(path)
            data = os.fsencode(link)
            actual = _blob_digest(data, len(blob))
            key = Path(link).name
            match = _ANNEX.match(key)
            if match:
                content = "sha256:" + match.group(1)
            elif path.is_file():
                with path.open("rb") as stream:
                    content = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
            elif match := _ANNEX_MD5.match(key):
                content = "md5:" + match.group(1)
            else:
                content = "gitblob:" + actual
            observation = commit if actual == blob else None
        elif path.is_file():
            sha = hashlib.sha256()
            gitsha = hashlib.sha1() if len(blob) == 40 else hashlib.sha256()
            gitsha.update(f"blob {path.stat().st_size}\0".encode())
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    sha.update(chunk)
                    gitsha.update(chunk)
            content = "sha256:" + sha.hexdigest()
            observation = commit if gitsha.hexdigest() == blob else None
        artifacts.append({"id": artifact_id(identity, relative, content), "dataset_id": identity,
                          "path": relative, "content_id": content, "commit": observation,
                          "source_ids": {}, "availability": availability})
    return {"schema_version": 1, "artifacts": artifacts, "attempts": [], "links": []}


def _blob_digest(data: bytes, length: int) -> str:
    digest = hashlib.sha1() if length == 40 else hashlib.sha256()
    digest.update(f"blob {len(data)}\0".encode() + data)
    return digest.hexdigest()


def study_datasets(study: Path):
    """Follow registered subdatasets and canonical behavioral directory links."""
    pending, seen = [study], set()
    while pending:
        root = pending.pop()
        if root.resolve() in seen or not (root / ".datalad/config").is_file():
            continue
        seen.add(root.resolve())
        identity = _git(root, "config", "--file", ".datalad/config", "--get", "datalad.dataset.id").decode().strip()
        yield identity, root
        for entry in _git(root, "ls-tree", "-rz", "HEAD").split(b"\0"):
            if not entry:
                continue
            header, relative = entry.split(b"\t", 1)
            path = Path(relative.decode())
            if _EXCLUDED.intersection(path.parts):
                continue
            # A BABS clone's inputs repeat canonical datasets, not new products.
            if "derivatives" in root.relative_to(study).parts and path.parts[0] == "sourcedata":
                continue
            if header.startswith((b"160000 ", b"120000 ")) and (root / path).is_dir():
                pending.append(root / path)
