"""Small participant adapter for standalone, fresh FreeSurfer reconstruction."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess


def _subject(subject: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9]+", subject):
        raise ValueError("subject must be an unprefixed BIDS label")
    return f"sub-{subject}"


def select_anatomy(bids: Path, subject: str) -> tuple[Path, Path | None]:
    root = Path(bids) / _subject(subject)
    selected = []
    for suffix in ("T1w", "T2w"):
        paths = sorted(p for p in root.rglob(f"*_{suffix}.nii*")
                       if p.parent.name == "anat" and p.name.endswith((".nii", ".nii.gz")))
        if len(paths) > 1 or (suffix == "T1w" and not paths):
            raise ValueError(f"expected {'one' if suffix == 'T1w' else 'at most one'} {suffix}; found {len(paths)}")
        if any(not p.is_file() for p in paths):
            raise ValueError(f"{suffix} content is unavailable")
        selected.append(paths[0] if paths else None)
    return selected[0], selected[1]


def recon_command(t1: Path, t2: Path | None, subject: str,
                  subjects_dir: Path, threads: int) -> tuple[str, ...]:
    label = _subject(subject)
    if threads < 1:
        raise ValueError("threads must be positive")
    target = Path(subjects_dir) / label
    if target.exists() or target.is_symlink():
        raise ValueError(f"reconstruction already exists: {target}")
    command = ("recon-all", "-s", label, "-sd", str(subjects_dir), "-i", str(t1),
               "-all", "-openmp", str(threads))
    return command + (("-T2", str(t2), "-T2pial") if t2 else ())


def _sha256(path: Path) -> str:
    # FreeSurfer's official 8.2 image bundles Python 3.8.
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_subject(bids: Path, output: Path, subject: str, *, threads: int = 4,
                build: str, runner=subprocess.run) -> Path:
    if not re.search(r"(?<![\d.])8\.2\.0(?![\d.])", build):
        raise ValueError("FreeSurfer 8.2.0 is required")
    bids, output = Path(bids).absolute(), Path(output).absolute()
    t1, t2 = select_anatomy(bids, subject)
    subjects = output / "subjects"
    command = recon_command(t1, t2, subject, subjects, threads)
    receipt = output / "code" / f"{_subject(subject)}_reconstruction.json"
    if receipt.exists() or receipt.is_symlink():
        raise ValueError(f"reconstruction receipt already exists: {receipt}")
    subjects.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1, "subject": _subject(subject), "build": build,
        "command": list(command), "started_at": datetime.now(timezone.utc).isoformat(),
        "inputs": [{"path": p.relative_to(bids).as_posix(), "sha256": _sha256(p)}
                   for p in (t1, t2) if p is not None],
        "status": "running", "exit_code": None,
    }
    # Reserve the receipt exclusively: a second worker cannot start this subject.
    with receipt.open("x") as stream:
        json.dump(record, stream, indent=2)
    try:
        runner(command, check=True)
    except BaseException as error:
        record.update(status="failed", exit_code=getattr(error, "returncode", None))
        raise
    else:
        record.update(status="success", exit_code=0)
    finally:
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, indent=2) + "\n")
        temporary.replace(receipt)
    return receipt


def copy_reconstruction(source: Path, target: Path) -> None:
    """Detach annex links and make only the new reconstruction owner-writable."""
    shutil.copytree(source, target, symlinks=False)
    for path in (target, *target.rglob("*")):
        path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IWUSR)


def run_correction(bids: Path, output: Path, subject: str, *, corrections: Path,
                   build: str, threads: int = 4, runner=subprocess.run) -> Path:
    """Restart a sealed edit copy; leave its source and other subjects untouched."""
    label = _subject(subject)
    record = json.loads((corrections / "code" / f"{label}.json").read_text())
    if record["build"] != build or not re.search(r"(?<![\d.])8\.2\.0(?![\d.])", build):
        raise ValueError("correction build must match the original FreeSurfer 8.2.0 build")
    kind = record["kind"]
    if record["subject"] != label or kind not in {"none", "wm", "pial", "wm-pial"} or threads < 1:
        raise ValueError("invalid correction record")
    t1, t2 = select_anatomy(bids, subject)
    inputs = [{"path": p.relative_to(bids).as_posix(), "sha256": _sha256(p)}
              for p in (t1, t2) if p is not None]
    if inputs != record["inputs"]:
        raise ValueError("correction anatomy differs from the original inputs")
    source = corrections / "subjects" / label
    files = sorted(source.rglob("*"))
    if source.is_symlink() or any(p.is_symlink() for p in files):
        raise ValueError("correction inputs must contain ordinary files")
    inventory = {p.relative_to(source).as_posix(): _sha256(p) for p in files if p.is_file()}
    if not inventory or inventory != record["inventory"]:
        raise ValueError("correction inventory changed")
    subjects = output.absolute() / "subjects"
    target = subjects / label
    receipt = output / "code" / f"{label}_reconstruction.json"
    if target.exists() or target.is_symlink() or receipt.exists() or receipt.is_symlink():
        raise ValueError("correction output already exists")
    command = ()
    if kind != "none":
        restart = ("-autorecon-pial",) if kind == "pial" else ("-autorecon2-wm", "-autorecon3")
        command = ("recon-all", "-s", label, "-sd", str(subjects), *restart,
                   "-openmp", str(threads)) + (("-T2pial",) if t2 else ())
    receipt.parent.mkdir(parents=True, exist_ok=True)
    result = {"schema_version": 1, "subject": label, "build": build, "inputs": inputs,
              "command": list(command), "correction": record, "status": "running",
              "started_at": datetime.now(timezone.utc).isoformat(), "exit_code": None}
    with receipt.open("x") as stream:
        json.dump(result, stream, indent=2)
    try:
        copy_reconstruction(source, target)
        if command:
            (target / "scripts/recon-all.done").unlink(missing_ok=True)
            if list((target / "scripts").glob("IsRunning*")):
                raise ValueError("source reconstruction contains a running-job marker")
            runner(command, check=True)
        if not (target / "scripts/recon-all.done").is_file():
            raise ValueError("reconstruction did not finish")
    except BaseException as error:
        result.update(status="failed", exit_code=getattr(error, "returncode", None))
        raise
    else:
        result.update(status="success", exit_code=0)
    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, indent=2) + "\n")
        temporary.replace(receipt)
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bids_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("analysis_level", choices=["participant"])
    parser.add_argument("--participant-label", nargs="+", required=True)
    parser.add_argument("--nprocs", type=int, default=4)
    parser.add_argument("--fs-license-file", type=Path, required=True)
    parser.add_argument("--corrections-dir", type=Path)
    args = parser.parse_args(argv)
    if not args.fs_license_file.is_file():
        parser.error("FreeSurfer license is unavailable")
    os.environ["FS_LICENSE"] = str(args.fs_license_file.absolute())
    build = (Path(os.environ["FREESURFER_HOME"]) / "build-stamp.txt").read_text().strip()
    for subject in args.participant_label:
        if args.corrections_dir:
            run_correction(args.bids_dir, args.output_dir, subject[4:] if subject.startswith("sub-") else subject,
                           corrections=args.corrections_dir, threads=args.nprocs, build=build)
        else:
            run_subject(args.bids_dir, args.output_dir, subject[4:] if subject.startswith("sub-") else subject,
                        threads=args.nprocs, build=build)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
