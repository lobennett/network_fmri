"""Content identities for reconstruction files, independent of ZIP packaging."""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import stat
import subprocess
import zipfile

from network_fmri.stages import StageError


def reconstruction_inventory(artifact: Path, subject: str) -> dict[str, str]:
    artifact = Path(artifact)
    if artifact.is_dir():
        root = artifact if artifact.name == f"sub-{subject}" else artifact / f"sub-{subject}"
        inventory = {}
        annex = _annex_store(root)
        for path in sorted(root.rglob("*")):
            target = path.resolve()
            if path.is_symlink() and not (target.is_relative_to(root.resolve())
                                         or annex is not None and target.is_relative_to(annex)):
                raise StageError("surface symlink escapes the subject directory")
            if path.is_file():
                with path.open("rb") as stream:
                    inventory[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
            elif path.is_symlink():
                raise StageError("surface symlink is broken or points to a directory")
        return inventory
    if not artifact.is_file() or artifact.suffix != ".zip":
        return {}
    inventory = {}
    prefixes = set()
    try:
        with zipfile.ZipFile(artifact) as archive:
            for info in archive.infolist():
                path = PurePosixPath(info.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
                    raise StageError("unsafe surface archive path")
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise StageError("surface archive contains a symlink")
                if f"sub-{subject}" not in path.parts or info.is_dir():
                    continue
                index = path.parts.index(f"sub-{subject}")
                prefixes.add(path.parts[:index + 1])
                relative = PurePosixPath(*path.parts[index + 1:]).as_posix()
                if relative in inventory or len(prefixes) > 1:
                    raise StageError("duplicate surface archive path")
                with archive.open(info) as stream:
                    inventory[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
    except (OSError, zipfile.BadZipFile) as error:
        raise StageError("cannot read surface archive") from error
    return inventory


def _annex_store(root: Path) -> Path | None:
    try:
        value = subprocess.check_output(
            ("git", "rev-parse", "--git-path", "annex/objects"), cwd=root,
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return (root / value).resolve()
