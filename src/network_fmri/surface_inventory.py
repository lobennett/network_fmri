"""Content identities for reconstruction files, independent of ZIP packaging."""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import posixpath
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
    try:
        with zipfile.ZipFile(artifact) as archive:
            for relative, info in subject_archive_files(archive, subject).items():
                with archive.open(info) as stream:
                    inventory[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
    except (OSError, zipfile.BadZipFile) as error:
        raise StageError("cannot read surface archive") from error
    return inventory


def subject_archive_files(archive: zipfile.ZipFile, subject: str) -> dict[str, zipfile.ZipInfo]:
    """Resolve subject-local file links; never follow links onto the host filesystem."""
    files, prefixes, directories = {}, set(), set()
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
            raise StageError("unsafe surface archive path")
        if f"sub-{subject}" not in path.parts:
            continue  # The container's fsaverage link is outside this subject.
        index = path.parts.index(f"sub-{subject}")
        prefixes.add(path.parts[:index + 1])
        relative = PurePosixPath(*path.parts[index + 1:]).as_posix()
        if len(prefixes) > 1 or relative in files:
            raise StageError("duplicate surface archive path")
        if info.is_dir():
            directories.add(relative)
        else:
            files[relative] = info
    for relative in files:
        if relative == "." or relative in directories or any(
            parent.as_posix() in files for parent in PurePosixPath(relative).parents
        ):
            raise StageError("conflicting surface archive paths")
    resolved = {}
    for relative in files:
        current, seen = relative, set()
        while True:
            if current in seen or current not in files:
                raise StageError("surface archive link is cyclic, broken, or points to a directory")
            seen.add(current)
            info = files[current]
            if not stat.S_ISLNK(info.external_attr >> 16):
                resolved[relative] = info
                break
            if info.file_size > 4096:
                raise StageError("invalid surface archive link")
            try:
                target = archive.read(info).decode("utf-8")
            except UnicodeError as error:
                raise StageError("invalid surface archive link") from error
            if not target or target.startswith("/") or "\\" in target or "\x00" in target:
                raise StageError("unsafe surface archive link")
            current = posixpath.normpath(posixpath.join(posixpath.dirname(current), target))
            if current == ".." or current.startswith("../"):
                raise StageError("surface archive link escapes the subject directory")
    return resolved


def _annex_store(root: Path) -> Path | None:
    try:
        value = subprocess.check_output(
            ("git", "rev-parse", "--git-path", "annex/objects"), cwd=root,
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return (root / value).resolve()
