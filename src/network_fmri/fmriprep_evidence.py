"""Extract fMRIPrep reports and check time-series lengths after the BABS merge."""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

import nibabel as nib

from network_fmri.mriqc import _git, _gitlink, _json, _require_dataset, _sha256
from network_fmri.processing import ProcessingManager
from network_fmri.records.native_lineage import file_record, transformation
from network_fmri.registration_qc import _registered

RECEIPT = "code/network_fmri/fmriprep-evidence.json"
REPORT_SCHEMA_VERSION = 2


def _is_report(path, subject):
    return len(path.parts) == 1 and re.fullmatch(
        rf"sub-{re.escape(subject)}(?:_anat|(?:_ses-[A-Za-z0-9]+)?_func)?\.html", path.name
    ) is not None


def _missing_reports(reports, subject, expected):
    """Accept a legacy subject report or the complete anatomical/session set."""
    if f"sub-{subject}.html" in reports:
        return []
    required = {f"sub-{subject}_anat.html"}
    for key in expected:
        session = re.search(r"(?:^|_)(ses-[A-Za-z0-9]+)(?:_|$)", key)
        required.add(f"sub-{subject}" + (f"_{session[1]}" if session else "") + "_func.html")
    return [f"sub-{subject}: missing fMRIPrep HTML report {name}" for name in sorted(required - reports)]


def run_key(path):
    """Match a derivative to its input run, independent of space, echo or density."""
    entities = re.findall(r"(?:^|_)([A-Za-z0-9]+)-([^_./]+)", Path(path).name)
    return "_".join(
        f"{key}-{value}"
        for key, value in entities
        if key
        not in {"echo", "space", "res", "den", "desc", "hemi", "from", "to", "mode"}
    )


def expected_runs(raw, subject, *, runner=subprocess.run):
    expected = {}
    paths = sorted((raw / f"sub-{subject}").glob("**/func/*_bold.nii*"))
    sidecars = {
        path: path.with_name(
            path.name.removesuffix(".nii.gz").removesuffix(".nii") + ".json"
        )
        for path in paths
    }
    unavailable = [path for path in [*paths, *sidecars.values()] if not path.is_file()]
    if unavailable:
        runner(("datalad", "get", "-d", str(raw), *map(str, unavailable)), check=True)
    for path in paths:
        key = run_key(path)
        metadata = json.loads(sidecars[path].read_text())
        item = {
            "volumes": int(nib.load(path).shape[3]),
            "tr": float(metadata["RepetitionTime"]),
            "discarded_volumes": metadata.get("NumberOfVolumesDiscardedByUser"),
            "events": path.with_name(key + "_events.tsv").is_file(),
        }
        if key in expected and expected[key] != item:
            raise RuntimeError(f"echoes disagree on timing: {key}")
        expected[key] = item
    if not expected:
        raise RuntimeError(f"no input BOLD runs for sub-{subject}")
    return expected


def _image_timing(archive, member):
    with archive.open(member) as raw:
        stream = gzip.GzipFile(fileobj=raw) if member.filename.endswith(".gz") else raw
        if member.filename.endswith(".dtseries.nii"):
            image = nib.Cifti2Image.from_file_map(
                {"image": nib.FileHolder(fileobj=stream)}
            )
            axis = image.header.get_axis(0)
            if not isinstance(axis, nib.cifti2.SeriesAxis) or axis.unit != "SECOND":
                raise ValueError("CIFTI must have a time axis in seconds")
            return int(axis.size), float(axis.step)
        image = nib.Nifti1Image.from_file_map({"image": nib.FileHolder(fileobj=stream)})
        if len(image.shape) != 4:
            raise ValueError("preprocessed BOLD must be four-dimensional")
        unit = image.header.get_xyzt_units()[1]
        factor = {"sec": 1, "msec": 0.001, "usec": 0.000001}.get(unit)
        return int(image.shape[3]), float(
            image.header.get_zooms()[3]
        ) * factor if factor else None


def inspect_archive(path, subject, expected, target, *, require_cifti=True):
    """Read image headers only; keep reports, figures and confounds as review evidence."""
    runs = {
        key: {
            "run": key,
            "expected": value,
            "outputs": [],
            "confound_rows": None,
            "confounds": [],
            "issues": [],
        }
        for key, value in expected.items()
    }
    evidence, issues, seen, roots = [], [], set(), set()
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        for member in members:
            parts = member.filename.rstrip("/").split("/")
            subjects = set(
                re.findall(r"(?:^|[/_])(sub-[A-Za-z0-9]+)(?=[/_.]|$)", member.filename)
            )
            if (
                len(parts) < 1
                or any(p in {"", ".", "..", ".git", ".datalad"} for p in parts)
                or "\\" in member.filename
                or "\x00" in member.filename
                or member.filename in seen
                or member.flag_bits & 1
                or stat.S_IFMT(member.external_attr >> 16)
                not in {0, stat.S_IFREG, stat.S_IFDIR}
                or subjects - {f"sub-{subject}"}
            ):
                raise ValueError(f"unsafe ZIP member: {member.filename}")
            seen.add(member.filename)
            roots.add(parts[0])
        if len(roots) != 1:
            raise ValueError("expected a single fMRIPrep archive root")
        reports = set()
        for member in members:
            if member.is_dir():
                continue
            relative = Path(*member.filename.split("/")[1:])
            if _is_report(relative, subject):
                reports.add(relative.name)
            is_bold = relative.name.endswith("_desc-preproc_bold.nii.gz")
            is_cifti = relative.name.endswith(".dtseries.nii")
            is_confounds = relative.name.endswith("_desc-confounds_timeseries.tsv")
            if is_bold or is_cifti or is_confounds:
                key = run_key(relative)
                if key not in runs:
                    issues.append(
                        f"{key}: unexpected output with no matching input run"
                    )
                    continue
                row = runs[key]
                if is_confounds:
                    with archive.open(member) as stream:
                        reader = csv.DictReader(
                            io.TextIOWrapper(stream), delimiter="\t"
                        )
                        count = sum(1 for _ in reader)
                    row["confounds"].append(relative.as_posix())
                    row["confound_rows"] = count
                else:
                    volumes, tr = _image_timing(archive, member)
                    row["outputs"].append(
                        {
                            "path": relative.as_posix(),
                            "kind": "CIFTI" if is_cifti else "BOLD",
                            "volumes": volumes,
                            "tr": tr,
                        }
                    )
            # Never materialize full images or unrelated files in the review derivative.
            if _is_report(relative, subject) or (
                relative.parts
                and relative.parts[0] == f"sub-{subject}"
                and (
                    "figures" in relative.parts
                    or is_confounds
                    or relative.name.endswith("_desc-confounds_timeseries.json")
                )
            ):
                if relative.suffix not in {
                    ".html",
                    ".svg",
                    ".png",
                    ".jpg",
                    ".json",
                    ".tsv",
                }:
                    continue
                output = target / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, output.open("xb") as dest:
                    shutil.copyfileobj(source, dest)
                evidence.append(
                    {"path": relative.as_posix(), "sha256": _sha256(output)}
                )
        issues.extend(_missing_reports(reports, subject, expected))
    for key, row in runs.items():
        expected = row["expected"]
        if len(row["confounds"]) != 1 or row["confound_rows"] != expected["volumes"]:
            row["issues"].append(
                f"{key}: confound rows/files do not match the trimmed input"
            )
        if not any(
            "_space-T1w_" in f["path"] and f["kind"] == "BOLD" for f in row["outputs"]
        ):
            row["issues"].append(f"{key}: missing T1w-space preprocessed BOLD")
        if require_cifti and not any(f["kind"] == "CIFTI" for f in row["outputs"]):
            row["issues"].append(f"{key}: missing CIFTI timeseries")
        for output in row["outputs"]:
            if output["volumes"] != expected["volumes"]:
                row["issues"].append(
                    f"{key}: {output['kind']} volume count differs from trimmed input ({output['path']})"
                )
            if output["tr"] is None or not math.isclose(
                output["tr"], expected["tr"], rel_tol=1e-5
            ):
                row["issues"].append(
                    f"{key}: output TR differs from input ({output['path']})"
                )
        issues.extend(row["issues"])
    return {
        "subject": subject,
        "runs": list(runs.values()),
        "issues": issues,
        "evidence": evidence,
    }


def prepare_fmriprep_review(config, *, runner=subprocess.run):
    """Publish immutable reports/checks once; restarts verify the same inputs and outputs."""
    stage = next(
        s
        for s in ProcessingManager(config, runner=runner).plan()
        if s.stage == "fmriprep"
    )
    if stage.state != "complete":
        raise RuntimeError("fMRIPrep must be merged before extracting reports")
    study = config.mechababs.study_dir.resolve()
    _require_dataset(study, runner=runner)
    source = study / stage.project
    source_id, commit = _registered(source, study, runner)
    raw = study / "sourcedata" / config.mechababs.raw_slot
    raw_id = _require_dataset(raw, runner=runner)
    raw_commit = _git(raw, "rev-parse", "HEAD", runner=runner)
    if _gitlink(source, commit, "sourcedata/raw", runner=runner) != raw_commit:
        raise RuntimeError("fMRIPrep inputs differ from the current raw dataset")
    tracked = _git(
        source, "ls-tree", "-r", "--name-only", commit, runner=runner
    ).splitlines()
    archives = []
    for subject in config.subjects:
        matches = [
            p
            for p in tracked
            if "/" not in p and p.startswith(f"sub-{subject}_") and p.endswith(".zip")
        ]
        if len(matches) != 1:
            raise RuntimeError(f"expected one merged archive for sub-{subject}")
        path = source / matches[0]
        if not path.is_file():
            runner(("datalad", "get", "-d", str(source), str(path)), check=True)
        archives.append(
            {"subject": subject, "path": path.name, "sha256": _sha256(path)}
        )
    inputs = {
        "source_dataset_id": source_id,
        "source_commit": commit,
        "raw_dataset_id": raw_id,
        "raw_commit": raw_commit,
        "archives": archives,
        "require_cifti": True,
        "schema_version": 1,
    }
    destination = source.with_name(source.name + "+review")
    upgrading = destination.exists() or destination.is_symlink()
    if upgrading:
        identity, previous_commit = _registered(destination, study, runner)
        receipt = json.loads((destination / RECEIPT).read_text())
        if receipt["inputs"] != inputs:
            raise RuntimeError(
                "fMRIPrep review inputs changed; previous evidence preserved"
            )
        for row in receipt["evidence"]:
            if _sha256(destination / row["path"]) != row["sha256"]:
                raise RuntimeError("fMRIPrep review evidence changed")
        version = receipt.get('report_schema_version', 1)
        if version == REPORT_SCHEMA_VERSION:
            return destination
        if version != 1:
            raise RuntimeError('unsupported fMRIPrep report schema version')
    with tempfile.TemporaryDirectory(
        prefix=".fmriprep-review-", dir=source.parent
    ) as temp:
        staging = Path(temp) / "output"
        staging.mkdir()
        if not upgrading:
            runner(("datalad", "create", "--force", str(staging)), check=True)
            identity = _git(staging, 'config', '--file', '.datalad/config',
                            '--get', 'datalad.dataset.id', runner=runner)
        subjects = []
        evidence = []
        for item in archives:
            result = inspect_archive(
                source / item["path"],
                item["subject"],
                expected_runs(raw, item["subject"], runner=runner),
                staging,
            )
            subjects.append(result)
            evidence.extend(result["evidence"])
            lineage = transformation(
                "fmriprep-report-extraction",
                f"sub-{item['subject']}",
                [file_record(source_id, item)],
                [
                    file_record(identity, r, availability="available")
                    for r in result["evidence"]
                ],
                software={"network_fmri": "0.1.0"},
                parameters={"raw_commit": raw_commit, "header_checks_only": True},
            )
            path = (
                staging
                / f"code/network_fmri/lineage/sub-{item['subject']}_fmriprep-evidence.json"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_json(lineage))
            evidence.append(
                {"path": path.relative_to(staging).as_posix(), "sha256": _sha256(path)}
            )
        receipt = {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "inputs": inputs,
            "subjects": subjects,
            "evidence": evidence,
            "status": "issues" if any(s["issues"] for s in subjects) else "success",
        }
        (staging / RECEIPT).write_text(_json(receipt))
        (staging / "dataset_description.json").write_text(
            _json(
                {
                    "Name": "fMRIPrep reports and output checks",
                    "BIDSVersion": "1.10.0",
                    "DatasetType": "derivative",
                    "GeneratedBy": [{"Name": "network_fmri", "Version": "0.1.0"}],
                }
            )
        )
        if (
            _registered(source, study, runner)[1] != commit
            or _git(raw, "rev-parse", "HEAD", runner=runner) != raw_commit
        ):
            raise RuntimeError("fMRIPrep review inputs changed during extraction")
        if upgrading:
            if _registered(destination, study, runner)[1] != previous_commit:
                raise RuntimeError('fMRIPrep review changed during report upgrade')
            # Keep the dataset identity and Git history. Replace changed files
            # atomically rather than writing through read-only annex symlinks.
            for path in staging.rglob('*'):
                if path.is_file():
                    target = destination / path.relative_to(staging)
                    if target.is_file() and _sha256(target) == _sha256(path):
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(path, target)
            staging = destination
        runner(
            (
                "datalad",
                "save",
                "-d",
                str(staging),
                "-m",
                "Extract fMRIPrep reports and check output alignment",
            ),
            check=True,
        )
        if not upgrading:
            staging.rename(destination)
    runner(
        (
            "datalad",
            "save",
            "-d",
            str(study),
            "-m",
            "Register fMRIPrep review evidence",
            str(destination),
        ),
        check=True,
    )
    return destination
