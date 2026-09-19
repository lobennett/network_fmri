"""Apply sealed scan decisions to the current BIDS state."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from network_fmri.models import Runner, StageResult
from network_fmri.prepare.b0link import link_b0
from network_fmri.qa.validate import ValidationError, validate_bids
from network_fmri.stages import StageError

_ENTITY = re.compile(r"^[A-Za-z0-9]+$")
_RUN = re.compile(r"^(?:0|[1-9][0-9]*)$")
_REQUIRED_COLUMNS = frozenset({
    "record_type", "subject", "session", "datatype", "suffix", "task",
    "acquisition", "direction", "run", "decision",
})
_DERIVATIVE_ENTITIES = frozenset({
    "echo", "desc", "space", "res", "den", "label", "hemi", "part",
    "from", "to", "mode", "atlas",
})


@dataclass(frozen=True)
class ApprovalSnapshot:
    """The manifest/sidecar bytes accepted by the read-only approval gate."""

    manifest: Path
    metadata: Path
    manifest_bytes: bytes
    metadata_bytes: bytes
    manifest_sha256: str
    metadata_sha256: str


@dataclass(frozen=True)
class MetadataSnapshot:
    """One B0-linkable sidecar state, including a possible symlink target."""

    path: Path
    content: bytes | None
    link_target: Path | None


@dataclass(frozen=True)
class Acquisition:
    """The exact BIDS identity of one acquisition selected for removal."""

    subject: str
    session: str
    datatype: str
    suffix: str
    task: str = ""
    acquisition: str = ""
    direction: str = ""
    run: str = ""

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Acquisition":
        if row["record_type"] != "acquisition":
            raise StageError("a missing-expected record cannot be dropped")
        values = {field: row[field] for field in cls.__dataclass_fields__}
        if any(not isinstance(value, str) for value in values.values()):
            raise StageError("drop decision has malformed acquisition fields")
        result = cls(**values)
        result._validate()
        return result

    def _validate(self) -> None:
        if not _entity(self.subject, "sub-") or not _entity(self.session, "ses-"):
            raise StageError(f"invalid BIDS subject/session in drop decision: {self}")
        if self.datatype == "func" and self.suffix == "bold":
            if not self.task or not self.run:
                raise StageError(f"functional drop lacks task/run identity: {self}")
        elif self.datatype == "anat" and self.suffix in {"T1w", "T2w"}:
            if self.task or self.direction:
                raise StageError(f"anatomical drop has functional entities: {self}")
        else:
            raise StageError(f"unsupported drop acquisition: {self}")
        fields = (self.task, self.acquisition, self.direction)
        if any(not _ENTITY.fullmatch(value) for value in fields if value):
            raise StageError(f"invalid BIDS entity in drop decision: {self}")
        if self.run and not _RUN.fullmatch(self.run):
            raise StageError(f"invalid run entity in drop decision: {self}")

    @property
    def directory(self) -> Path:
        return Path(self.subject) / self.session / self.datatype


def apply_curation(
    bids_dir: Path,
    manifest: Path,
    runner: Runner = subprocess.run,
) -> StageResult:
    """Remove approved drop bundles, repair B0 metadata, and validate the result.

    Approval is checked before any files are inspected. Every candidate bundle is
    resolved before a move, preventing a partly curated tree when an identity is
    stale or ambiguous. Raw behavioral sourcedata is not a curation target.
    """

    bids_dir = Path(bids_dir).resolve(strict=False)
    manifest = Path(manifest).resolve(strict=False)
    _require_dataset(bids_dir)
    approval = _require_approval(bids_dir, manifest, runner)
    drops = _drop_acquisitions(approval)
    plan = _resolve_plan(bids_dir, drops)
    _assert_approval_unchanged(approval)
    metadata = _metadata_snapshots(bids_dir)
    transaction = _RemovalTransaction.stage(bids_dir, plan)
    try:
        b0 = _rebuild_b0(bids_dir)
        validation = validate_bids(bids_dir, "curated", runner)
    except ValidationError as error:
        _rollback(transaction, metadata)
        raise StageError(f"curated BIDS validation failed; see {error.result.report}") from error
    except Exception:
        _rollback(transaction, metadata)
        raise
    transaction.commit()
    return StageResult(
        "mriqc-curated",
        (bids_dir, validation.report, validation.log),
        {"removed_files": len(plan), "dropped_acquisitions": len(drops), "b0": b0},
    )


def _require_dataset(bids_dir: Path) -> None:
    if not bids_dir.is_dir() or bids_dir.is_symlink():
        raise StageError(f"BIDS directory is missing or unsafe: {bids_dir}")
    if not (bids_dir / "dataset_description.json").is_file():
        raise StageError(f"BIDS dataset description is missing: {bids_dir}")


def _require_approval(
    bids_dir: Path, manifest: Path, runner: Runner,
) -> ApprovalSnapshot:
    expected = bids_dir / "code" / "network_fmri" / "scan_decisions.tsv"
    if manifest != expected:
        raise StageError("curation manifest must be code/network_fmri/scan_decisions.tsv")
    metadata = manifest.with_suffix(".meta.json")
    try:
        manifest_bytes = manifest.read_bytes()
        metadata_bytes = metadata.read_bytes()
    except OSError as error:
        raise StageError("approved scan-decision manifest or metadata is unavailable") from error
    command = [
        "network-qa", "decisions", "validate", "--manifest", str(manifest),
        "--metadata", str(metadata), "--bids-dir", str(bids_dir),
    ]
    try:
        runner(command, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise StageError("scan-decision approval validation failed; curation is blocked") from error
    snapshot = ApprovalSnapshot(
        manifest, metadata, manifest_bytes, metadata_bytes,
        hashlib.sha256(manifest_bytes).hexdigest(), hashlib.sha256(metadata_bytes).hexdigest(),
    )
    _assert_approval_unchanged(snapshot)
    return snapshot


def _assert_approval_unchanged(snapshot: ApprovalSnapshot) -> None:
    try:
        current_manifest = snapshot.manifest.read_bytes()
        current_metadata = snapshot.metadata.read_bytes()
    except OSError as error:
        raise StageError("approved scan-decision manifest or metadata changed during curation") from error
    if (
        current_manifest != snapshot.manifest_bytes
        or current_metadata != snapshot.metadata_bytes
    ):
        raise StageError(
            "approved scan-decision manifest or metadata changed after validation; "
            "curation is blocked"
        )


def _drop_acquisitions(approval: ApprovalSnapshot) -> tuple[Acquisition, ...]:
    try:
        stream = io.StringIO(approval.manifest_bytes.decode("utf-8"), newline="")
        reader = csv.DictReader(stream, delimiter="\t")
        columns = frozenset(reader.fieldnames or ())
        missing = _REQUIRED_COLUMNS - columns
        if missing:
            raise StageError(f"scan-decision manifest is missing columns: {sorted(missing)}")
        drops = tuple(Acquisition.from_row(row) for row in reader if row["decision"] == "drop")
    except (UnicodeError, csv.Error) as error:
        raise StageError(f"could not read approved scan decisions: {approval.manifest}") from error
    if len(set(drops)) != len(drops):
        raise StageError("approved scan decisions contain duplicate drop acquisitions")
    return drops


def _resolve_plan(bids_dir: Path, drops: tuple[Acquisition, ...]) -> tuple[Path, ...]:
    planned: set[Path] = set()
    for acquisition in drops:
        planned.update(_resolve_bundle(bids_dir, acquisition))
    return tuple(sorted(planned))


def _resolve_bundle(bids_dir: Path, acquisition: Acquisition) -> tuple[Path, ...]:
    directory = bids_dir / acquisition.directory
    if not directory.is_dir() or directory.is_symlink():
        raise StageError(f"drop acquisition has no safe BIDS directory: {acquisition}")
    files = tuple(sorted(path for path in directory.iterdir() if path.is_file() or path.is_symlink()))
    images = tuple(
        path for path in files
        if _nifti_suffix(path) == acquisition.suffix and _matches(path, acquisition)
    )
    if not images:
        raise StageError(f"drop acquisition matched no BIDS images: {acquisition}")
    _require_unambiguous(images, acquisition)
    bundle = set(images)
    for path in files:
        suffix = _file_suffix(path)
        if _matches(path, acquisition) and suffix in {acquisition.suffix, "events"}:
            bundle.add(path)
    bundle.update(_event_qc_files(bids_dir, acquisition))
    bundle.update(_derivative_files(bids_dir, acquisition))
    return tuple(sorted(bundle))


def _event_qc_files(bids_dir: Path, acquisition: Acquisition) -> tuple[Path, ...]:
    """Return the per-acquisition event-conversion evidence, never raw behavior."""

    if acquisition.datatype != "func":
        return ()
    root = bids_dir / "sourcedata" / "events_qc" / acquisition.subject / acquisition.session
    if not root.is_dir() or root.is_symlink():
        return ()
    result = []
    for path in root.glob("*_desc-truncation.json"):
        entities = _entities(path)
        if entities is None or _file_suffix(path) != "desc-truncation":
            continue
        if _matches_entities(entities, acquisition):
            result.append(path)
    return tuple(sorted(result))


def _derivative_files(bids_dir: Path, acquisition: Acquisition) -> tuple[Path, ...]:
    """Return MRIQC and other acquisition-specific derivative products."""

    root = bids_dir / "derivatives"
    if not root.is_dir() or root.is_symlink():
        return ()
    return tuple(sorted(
        path for path in root.rglob("*")
        if (path.is_file() or path.is_symlink()) and _matches_derivative(path, acquisition)
    ))


def _require_unambiguous(images: tuple[Path, ...], acquisition: Acquisition) -> None:
    if acquisition.datatype == "anat" and len(images) != 1:
        raise StageError(f"drop acquisition matched ambiguous BIDS images: {acquisition}")
    echoes = [_entities(path).get("echo", "") for path in images]
    if len(echoes) != len(set(echoes)):
        raise StageError(f"drop acquisition matched ambiguous BIDS echoes: {acquisition}")


def _matches(path: Path, acquisition: Acquisition) -> bool:
    entities = _entities(path)
    if entities is None:
        return False
    return _matches_entities(entities, acquisition)


def _matches_entities(entities: dict[str, str], acquisition: Acquisition) -> bool:
    expected = {
        "sub": acquisition.subject.removeprefix("sub-"),
        "ses": acquisition.session.removeprefix("ses-"),
    }
    if acquisition.datatype == "func":
        expected.update({"task": acquisition.task, "run": acquisition.run})
    elif acquisition.run:
        expected["run"] = acquisition.run
    for label, value in (("acq", acquisition.acquisition), ("dir", acquisition.direction)):
        if value:
            expected[label] = value
    comparable = {
        name: _canonical_entity(name, value)
        for name, value in entities.items() if name != "echo"
    }
    return comparable == expected


def _matches_derivative(path: Path, acquisition: Acquisition) -> bool:
    entities = _entities(path)
    if entities is None:
        return False
    source_entities = {
        name: value for name, value in entities.items()
        if name not in _DERIVATIVE_ENTITIES
    }
    return _matches_entities(source_entities, acquisition)


def _entities(path: Path) -> dict[str, str] | None:
    stem = _stem(path)
    if stem is None:
        return None
    parts = stem.split("_")
    if len(parts) < 2:
        return None
    entities: dict[str, str] = {}
    for part in parts[:-1]:
        key, separator, value = part.partition("-")
        if not separator or not key or not value or key in entities:
            return None
        entities[key] = value
    return entities


def _file_suffix(path: Path) -> str | None:
    stem = _stem(path)
    return stem.rsplit("_", 1)[-1] if stem and "_" in stem else None


def _nifti_suffix(path: Path) -> str | None:
    if not (path.name.endswith(".nii") or path.name.endswith(".nii.gz")):
        return None
    return _file_suffix(path)


def _stem(path: Path) -> str | None:
    name = path.name
    if name.endswith(".nii.gz"):
        return name.removesuffix(".nii.gz")
    if name.endswith(".nii"):
        return name.removesuffix(".nii")
    if name.endswith(".json"):
        return name.removesuffix(".json")
    if name.endswith(".tsv"):
        return name.removesuffix(".tsv")
    if name.endswith(".html"):
        return name.removesuffix(".html")
    return None


def _canonical_entity(name: str, value: str) -> str:
    return str(int(value)) if name in {"run", "echo"} and value.isdigit() else value


@dataclass
class _RemovalTransaction:
    """Park curation targets until B0 repair and validation both succeed."""

    holding: Path | None
    moved: list[tuple[Path, Path]]

    @classmethod
    def stage(cls, bids_dir: Path, plan: tuple[Path, ...]) -> "_RemovalTransaction":
        if not plan:
            return cls(None, [])
        holding = Path(tempfile.mkdtemp(prefix=".network-fmri-curation-", dir=bids_dir))
        result = cls(holding, [])
        try:
            for index, path in enumerate(plan):
                parked = holding / str(index)
                os.replace(path, parked)
                result.moved.append((path, parked))
        except OSError as error:
            try:
                result.rollback(())
            except OSError as rollback_error:
                raise StageError("curation staging and rollback both failed") from rollback_error
            raise StageError("could not stage the complete approved curation bundle") from error
        return result

    def rollback(self, metadata: tuple[MetadataSnapshot, ...]) -> None:
        for original, parked in reversed(self.moved):
            if parked.exists() or parked.is_symlink():
                os.replace(parked, original)
        for snapshot in metadata:
            _restore_metadata(snapshot)
        self._discard_holding()

    def commit(self) -> None:
        self._discard_holding()

    def _discard_holding(self) -> None:
        if self.holding is not None:
            shutil.rmtree(self.holding)
            self.holding = None


def _metadata_snapshots(bids_dir: Path) -> tuple[MetadataSnapshot, ...]:
    """Capture all raw sidecars B0 linking might update before curation moves."""

    snapshots = []
    for path in sorted(bids_dir.glob("sub-*/ses-*/*/*.json")):
        if path.is_symlink():
            snapshots.append(MetadataSnapshot(path, None, path.readlink()))
        elif path.is_file():
            snapshots.append(MetadataSnapshot(path, path.read_bytes(), None))
    return tuple(snapshots)


def _rollback(
    transaction: _RemovalTransaction,
    metadata: tuple[MetadataSnapshot, ...],
) -> None:
    try:
        transaction.rollback(metadata)
    except OSError as error:
        raise StageError("curation failed and its rollback could not restore the BIDS state") from error


def _restore_metadata(snapshot: MetadataSnapshot) -> None:
    temporary = snapshot.path.with_name(f".{snapshot.path.name}.curation-restore")
    try:
        if snapshot.link_target is not None:
            temporary.symlink_to(snapshot.link_target)
        else:
            temporary.write_bytes(snapshot.content or b"")
        os.replace(temporary, snapshot.path)
    finally:
        temporary.unlink(missing_ok=True)


def _rebuild_b0(bids_dir: Path) -> dict[str, object]:
    if not any(bids_dir.glob("sub-*/ses-*/func/*_bold.nii*")):
        return {"status": "skipped", "reason": "no BOLD scans remain"}
    return link_b0(bids_dir).details


def _entity(value: str, prefix: str) -> bool:
    return value.startswith(prefix) and bool(_ENTITY.fullmatch(value.removeprefix(prefix)))
