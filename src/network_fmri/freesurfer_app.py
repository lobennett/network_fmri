"""Small participant adapter for standalone, fresh FreeSurfer reconstruction."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
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
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bids_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("analysis_level", choices=["participant"])
    parser.add_argument("--participant-label", nargs="+", required=True)
    parser.add_argument("--nprocs", type=int, default=4)
    parser.add_argument("--fs-license-file", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.fs_license_file.is_file():
        parser.error("FreeSurfer license is unavailable")
    os.environ["FS_LICENSE"] = str(args.fs_license_file.absolute())
    build = (Path(os.environ["FREESURFER_HOME"]) / "build-stamp.txt").read_text().strip()
    for subject in args.participant_label:
        run_subject(args.bids_dir, args.output_dir, subject.removeprefix("sub-"),
                    threads=args.nprocs, build=build)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
