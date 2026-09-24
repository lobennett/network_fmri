"""Create the BIDS study wrapper consumed by MechaBABS."""

from __future__ import annotations

import csv
import json
import re
import subprocess
import tempfile
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from network_fmri.config import MechaBABSConfig


_ECHO = re.compile(r"_echo-[^_]+")
_MECHABABS_URL = "https://github.com/con/mechababs.git"
_BABS_URL = "https://github.com/PennLINC/babs.git"


@dataclass(frozen=True)
class StudyResult:
    """Identity and locations of one initialized study."""

    study_dir: Path
    campaign_dir: Path
    study_id: str
    raw_dataset_id: str
    raw_commit: str
    created: bool


class StudyManager:
    """Build an additive study wrapper with an upstream-managed campaign."""

    def __init__(
        self,
        config: MechaBABSConfig,
        raw_bids_dir: Path,
        freesurfer_license: Path,
        *,
        runner=subprocess.run,
    ) -> None:
        self.config = config
        self.raw_bids_dir = raw_bids_dir.resolve(strict=False)
        self.freesurfer_license = Path(freesurfer_license).resolve(strict=False)
        self.runner = runner

    @property
    def manifest_path(self) -> Path:
        return self.config.study_dir / "code" / "network_fmri" / "study.json"

    def initialize(self, *, subjects: tuple[str, ...]) -> StudyResult:
        """Create the wrapper and campaign, or verify an identical prior run."""

        self._require_raw_dataset()
        raw_commit = self._output(("git", "rev-parse", "HEAD"), cwd=self.raw_bids_dir)
        raw_id = self._output(
            ("git", "config", "--get", "datalad.dataset.id"), cwd=self.raw_bids_dir
        )
        expected = self._identity(raw_commit, raw_id, subjects)
        if self.config.study_dir.exists():
            return self._verify_existing(expected)

        self._require_inputs(subjects)
        self._run(("datalad", "create", "-c", "text2git", str(self.config.study_dir)))
        self.config.study_dir.mkdir(parents=True, exist_ok=True)
        self._write_study_files(subjects)
        raw_destination = Path("sourcedata") / self.config.raw_slot
        self._run(
            (
                "datalad", "clone", "-d", str(self.config.study_dir),
                str(self.raw_bids_dir), str(raw_destination),
            ),
            cwd=self.config.study_dir,
        )
        (self.config.study_dir / raw_destination).mkdir(parents=True, exist_ok=True)
        self._run(
            (
                "datalad", "create-sibling", "-d", str(self.config.study_dir),
                "-s", "oak", str(self.config.durable_sibling),
            )
        )
        study_id = self._output(
            ("git", "config", "--get", "datalad.dataset.id"), cwd=self.config.study_dir
        )
        expected["study_id"] = study_id
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(expected, indent=2, sort_keys=True) + "\n")
        self._run(
            ("datalad", "save", "-d", str(self.config.study_dir), "-m", "Create canonical BIDS study")
        )
        self._initialize_campaign()
        return StudyResult(
            self.config.study_dir,
            self.config.campaign_dir,
            study_id,
            raw_id,
            raw_commit,
            True,
        )

    def _require_raw_dataset(self) -> None:
        if not (self.raw_bids_dir / ".datalad").is_dir():
            raise RuntimeError(f"raw BIDS path is not a DataLad dataset: {self.raw_bids_dir}")
        status = self._output(("git", "status", "--porcelain"), cwd=self.raw_bids_dir)
        if status:
            raise RuntimeError("raw BIDS dataset is dirty")

    def _require_inputs(self, subjects: tuple[str, ...]) -> None:
        if not subjects or len(set(subjects)) != len(subjects):
            raise ValueError("study subjects must be non-empty and unique")
        missing = [subject for subject in subjects if not (self.raw_bids_dir / f"sub-{subject}").is_dir()]
        if missing:
            raise RuntimeError("raw BIDS dataset is missing subjects: " + ", ".join(missing))

    def _identity(
        self, raw_commit: str, raw_dataset_id: str, subjects: tuple[str, ...]
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "campaign": self.config.campaign,
            "campaign_dir": str(self.config.campaign_dir),
            "durable_sibling": str(self.config.durable_sibling),
            "container_dataset": str(self.config.container_dataset),
            "raw_source": str(self.raw_bids_dir),
            "raw_slot": self.config.raw_slot,
            "raw_commit": raw_commit,
            "raw_dataset_id": raw_dataset_id,
            "subjects": list(subjects),
            "mechababs_commit": self.config.mechababs_commit,
            "babs_commit": self.config.babs_commit,
            "cluster_file": self.config.cluster_file.as_posix(),
            "apps": [asdict(app) | {"file": app.file.as_posix()} for app in self.config.apps],
        }

    def _verify_existing(self, expected: dict[str, object]) -> StudyResult:
        try:
            actual = json.loads(self.manifest_path.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError("existing study does not match: missing or invalid identity manifest") from error
        study_id = actual.pop("study_id", None) if isinstance(actual, dict) else None
        installed_raw = self.config.study_dir / "sourcedata" / self.config.raw_slot
        try:
            installed_commit = self._output(("git", "rev-parse", "HEAD"), cwd=installed_raw)
            installed_id = self._output(
                ("git", "config", "--get", "datalad.dataset.id"), cwd=installed_raw
            )
            actual_study_id = self._output(
                ("git", "config", "--get", "datalad.dataset.id"), cwd=self.config.study_dir
            )
            raw_url = self._output(
                (
                    "git", "config", "--file", str(self.config.study_dir / ".gitmodules"),
                    "--get", f"submodule.sourcedata/{self.config.raw_slot}.url",
                ),
                cwd=self.config.study_dir,
            )
            oak_url = self._output(
                ("git", "remote", "get-url", "oak"), cwd=self.config.study_dir
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError("existing study does not match: raw subdataset is unavailable") from error
        if (
            not study_id
            or actual != expected
            or installed_commit != expected["raw_commit"]
            or installed_id != expected["raw_dataset_id"]
            or actual_study_id != study_id
            or Path(raw_url).resolve(strict=False) != self.raw_bids_dir
            or Path(oak_url).resolve(strict=False) != self.config.durable_sibling.resolve(strict=False)
            or not self.config.campaign_dir.is_dir()
        ):
            raise RuntimeError("existing study does not match the requested DataLad identities and commits")
        self._verify_campaign()
        return StudyResult(
            self.config.study_dir,
            self.config.campaign_dir,
            str(study_id),
            str(expected["raw_dataset_id"]),
            str(expected["raw_commit"]),
            False,
        )

    def _verify_campaign(self) -> None:
        # Upstream verifies the selected environment against its committed uv.lock.
        from network_fmri.campaign import Campaign

        path = self.config.campaign_dir / "pyproject.toml"
        try:
            sources = tomllib.loads(path.read_text())["tool"]["uv"]["sources"]
            expected = {
                "mechababs": {"git": _MECHABABS_URL, "rev": self.config.mechababs_commit},
                "babs": {"git": _BABS_URL, "rev": self.config.babs_commit},
            }
            if any(sources.get(name) != value for name, value in expected.items()):
                raise ValueError("changed tool pins")
        except (OSError, KeyError, ValueError) as error:
            raise RuntimeError("campaign tool pins do not match workflow configuration") from error
        Campaign(self.config, runner=self.runner).run("status")

    def _write_study_files(self, subjects: tuple[str, ...]) -> None:
        description = {
            "Name": "Network Grant canonical study",
            "BIDSVersion": "1.10.0",
            "DatasetType": "study",
        }
        (self.config.study_dir / "dataset_description.json").write_text(
            json.dumps(description, indent=2) + "\n"
        )
        source = self.config.study_dir / "sourcedata"
        source.mkdir(parents=True, exist_ok=True)
        session_rows = list(self._session_rows(subjects))
        # Upstream infers subject-level jobs when only the subject table exists.
        subject_rows = []
        for subject in subjects:
            rows = [row for row in session_rows if row["subject_id"] == subject]
            datatypes = sorted({value for row in rows for value in row["datatypes"].split(",") if value})
            subject_rows.append({
                "subject_id": subject,
                "datatypes": ",".join(datatypes),
                "t1w_num": str(sum(int(row["t1w_num"]) for row in rows)),
                "bold_num": str(sum(int(row["bold_num"]) for row in rows)),
            })
        self._write_tsv(
            source / "sourcedata+subjects.tsv",
            ("subject_id", "datatypes", "t1w_num", "bold_num"),
            subject_rows,
        )

    def _session_rows(self, subjects: Iterable[str]) -> Iterable[dict[str, str]]:
        for subject in subjects:
            subject_dir = self.raw_bids_dir / f"sub-{subject}"
            sessions = sorted(path for path in subject_dir.glob("ses-*") if path.is_dir())
            if not sessions:
                sessions = [subject_dir]
            for session in sessions:
                t1w = tuple(session.glob("anat/*_T1w.nii.gz"))
                bold = {
                    _ECHO.sub("", path.name)
                    for path in session.glob("func/*_bold.nii.gz")
                }
                datatypes = [name for name, present in (("anat", t1w), ("func", bold)) if present]
                yield {
                    "subject_id": subject,
                    "session_id": session.name.removeprefix("ses-") if session != subject_dir else "",
                    "datatypes": ",".join(datatypes),
                    "t1w_num": str(len(t1w)),
                    "bold_num": str(len(bold)),
                }

    @staticmethod
    def _write_tsv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def _initialize_campaign(self) -> None:
        """Let upstream own campaign copies, environment locks, and registration."""

        from network_fmri.campaign import Campaign

        with tempfile.TemporaryDirectory(prefix="network-mechababs-") as temporary:
            directory = Path(temporary)
            for kind, relative, content in self._rendered_configs():
                target = directory / kind / relative.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            self._run((
                "uvx", "--from", f"git+{_MECHABABS_URL}@{self.config.mechababs_commit}",
                "mechababs", "campaign", "init", self.config.campaign,
                "-d", str(self.config.study_dir),
                "--apps", ",".join(str(directory / "apps" / app.file.name) for app in self.config.apps),
                "--cluster", str(directory / "clusters" / self.config.cluster_file.name),
                "--mechababs", f"{_MECHABABS_URL}@{self.config.mechababs_commit}",
                "--babs", f"{_BABS_URL}@{self.config.babs_commit}",
            ), cwd=self.config.study_dir)
        Campaign(self.config, runner=self.runner).run(
            "add-dataset", "--sourcedata", f"sourcedata/{self.config.raw_slot}"
        )

    def _rendered_configs(self) -> list[tuple[str, Path, str]]:
        source_root = Path(__file__).with_name("mechababs")
        replacements = {
            "{{CONTAINER_DATASET}}": str(self.config.container_dataset),
            "{{FREESURFER_LICENSE}}": str(self.freesurfer_license),
        }
        requested = [("clusters", self.config.cluster_file)] + [
            ("apps", app.file) for app in self.config.apps
        ]
        rendered = []
        for kind, relative in requested:
            source = source_root / kind / relative.name
            content = source.read_text()
            for marker, value in replacements.items():
                content = content.replace(marker, value)
            # MECHABABS_VENV is an upstream composition placeholder.
            remaining = re.findall(r"\{\{([A-Z_]+)\}\}", content)
            if set(remaining) - {"MECHABABS_VENV"}:
                raise RuntimeError(f"unresolved placeholder in MechaBABS config: {source}")
            rendered.append((kind, relative, content))
        return rendered

    def _output(self, command: tuple[str, ...], *, cwd: Path) -> str:
        result = self.runner(command, cwd=str(cwd), check=True, capture_output=True, text=True)
        return str(result.stdout).strip()

    def _run(self, command: tuple[str, ...], *, cwd: Path | None = None) -> None:
        self.runner(command, cwd=str(cwd) if cwd else None, check=True)
