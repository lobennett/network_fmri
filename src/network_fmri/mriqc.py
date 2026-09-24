"""Materialize archived BABS MRIQC evidence for the explicit human-review gate."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

from network_fmri.config import WorkflowConfig
from network_fmri.processing import ProcessingManager


RECEIPT = Path('code/network_fmri/mriqc-evidence.json')
_COMMIT = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_SUBJECT = re.compile(r'sub-[A-Za-z0-9]+\Z')


@dataclass(frozen=True)
class MRIQCEvidenceResult:
    evidence_dir: Path
    source_dataset: Path
    source_commit: str
    input_commit: str
    archives: int
    created: bool


def _json(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + '\n'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _output(root: Path, *command: str, runner) -> str:
    return str(runner(command, cwd=str(root), check=True, capture_output=True, text=True).stdout).strip()


def _git(root: Path, *arguments: str, runner) -> str:
    return _output(root, 'git', '--no-optional-locks', *arguments, runner=runner)


def _gitlink(root: Path, commit: str, relative: str, *, runner) -> str:
    row = _git(root, 'ls-tree', commit, '--', relative, runner=runner)
    header, separator, path = row.partition('\t')
    fields = header.split()
    if not separator or path != relative or len(fields) != 3 or fields[:2] != ['160000', 'commit']:
        raise RuntimeError(f'missing pinned gitlink: {relative}')
    return fields[2]


def _require_dataset(root: Path, *, runner) -> str:
    if root.is_symlink() or Path(_git(root, 'rev-parse', '--show-toplevel', runner=runner)).resolve() != root.resolve():
        raise RuntimeError(f'not an independent dataset: {root}')
    identity = _git(root, 'config', '--file', '.datalad/config', '--get', 'datalad.dataset.id', runner=runner)
    if not identity:
        raise RuntimeError(f'missing DataLad dataset identity: {root}')
    if _git(root, 'status', '--porcelain', '--untracked-files=all', '--ignore-submodules=all', runner=runner):
        raise RuntimeError(f'dataset is dirty: {root}')
    return identity


def _extract(archives: list[tuple[str, Path]], prefix: str, target: Path) -> list[dict]:
    """Validate every member before copying only loose QA evidence, never images."""
    from network_qa._evidence_paths import is_vcs_administration_path

    records, destinations = [], set()
    for subject, archive in archives:
        seen, iqms, reports = set(), 0, 0
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                path = PurePosixPath(member.filename)
                mode = member.external_attr >> 16
                parts = member.filename.rstrip('/').split('/')
                if (path.is_absolute() or '\\' in member.filename or '\x00' in member.filename
                        or any(part in {'', '.', '..'} for part in parts)
                        or not parts or parts[0] != prefix or is_vcs_administration_path(Path(*parts))
                        or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR})
                        or member.flag_bits & 1):
                    raise ValueError(f'unsafe ZIP member: {member.filename}')
                if member.filename in seen:
                    raise ValueError(f'duplicate ZIP member: {member.filename}')
                seen.add(member.filename)
                if member.is_dir():
                    continue
                relative = Path(*parts[1:])
                if len(parts) < 2:
                    raise ValueError(f'invalid ZIP member: {member.filename}')
                subjects = {match.group() for part in parts[1:]
                            for match in re.finditer(r'sub-[A-Za-z0-9]+', part)}
                if subjects and subjects != {subject}:
                    raise ValueError(f'ZIP member has wrong subject: {member.filename}')
                if relative == Path('dataset_description.json'):
                    # Each subject ZIP carries shared metadata. The canonical
                    # evidence derivative receives its own explicit description.
                    continue
                if relative.suffix not in {'.json', '.html', '.tsv'}:
                    continue
                if relative == RECEIPT or parts[1] == 'code':
                    raise ValueError(f'reserved evidence member: {member.filename}')
                if not subjects:
                    continue
                if relative in destinations:
                    raise ValueError(f'duplicate evidence member: {relative}')
                destinations.add(relative)
                output = target / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as stream, output.open('xb') as destination:
                    shutil.copyfileobj(stream, destination, length=1024 * 1024)
                iqms += relative.name.endswith(('_bold.json', '_T1w.json', '_T2w.json'))
                reports += relative.suffix == '.html'
                records.append({'path': relative.as_posix(), 'sha256': _sha256(output)})
        if not iqms or not reports:
            raise RuntimeError(f'archive lacks IQMs or reports: {archive.name}')
    return sorted(records, key=lambda row: row['path'])


def prepare_mriqc_review(config: WorkflowConfig, *, runner=subprocess.run) -> MRIQCEvidenceResult:
    """Get merged subject ZIPs and register an immutable, separate evidence dataset."""
    stages = ProcessingManager(config, runner=runner).plan()
    stage = next((stage for stage in stages if stage.stage == 'mriqc'), None)
    if stage is None or stage.state != 'complete':
        raise RuntimeError('MRIQC must be merged/complete before preparing review evidence')
    study = config.mechababs.study_dir.resolve()
    source = study / stage.project
    destination = source.with_name(source.name + '+review')
    _require_dataset(study, runner=runner)
    source_id = _require_dataset(source, runner=runner)
    commit = _git(source, 'rev-parse', 'HEAD', runner=runner)
    if not _COMMIT.fullmatch(commit) or _gitlink(study, 'HEAD', stage.project, runner=runner) != commit:
        raise RuntimeError('merged MRIQC commit is not registered in the study')
    input_commit = _gitlink(source, commit, 'sourcedata/raw', runner=runner)
    inclusion = csv.DictReader(io.StringIO(_git(source, 'show', f'{commit}:code/processing_inclusion.csv', runner=runner)))
    included = [row.get('sub_id', '') for row in inclusion]
    if (not included or len(included) != len(set(included))
            or any(not _SUBJECT.fullmatch(subject) for subject in included)):
        raise RuntimeError('invalid subject-level BABS processing inclusion')
    tracked = _git(source, 'ls-tree', '-r', '--name-only', commit, runner=runner).splitlines()
    archives = []
    for subject in sorted(included):
        matches = [path for path in tracked if '/' not in path and path.startswith(subject + '_') and path.endswith('.zip')]
        if len(matches) != 1:
            raise RuntimeError(f'expected one merged MRIQC subject archive for {subject}')
        archives.append((subject, source / matches[0]))
    if len([path for path in tracked if '/' not in path and path.startswith('sub-') and path.endswith('.zip')]) != len(archives):
        raise RuntimeError('unexpected or duplicate merged subject archives')
    unavailable = [path for _, path in archives if not path.is_file()]
    if unavailable:
        runner(('datalad', 'get', '-d', str(source), *map(str, unavailable)), check=True)
    for _, path in archives:
        if not path.is_file():
            raise RuntimeError(f'unavailable archive content: {path}')
        if path.is_symlink():
            runner(('git', 'annex', 'fsck', '--', path.name), cwd=str(source), check=True)
    archive_records = [{'path': path.name, 'sha256': _sha256(path)} for _, path in archives]
    with tempfile.TemporaryDirectory(prefix='.mriqc-review-', dir=source.parent) as temporary:
        staging = Path(temporary)
        evidence = _extract(archives, stage.application, staging)
        description = {'Name': f'{stage.application} review evidence', 'BIDSVersion': '1.10.0',
                       'DatasetType': 'derivative', 'GeneratedBy': [{'Name': 'network_fmri',
                           'Description': 'Extracted existing MRIQC JSON/HTML/TSV; no MRIQC execution'}]}
        description_path = staging / 'dataset_description.json'
        description_path.write_text(_json(description))
        evidence.append({'path': 'dataset_description.json', 'sha256': _sha256(description_path)})
        receipt = {'schema_version': 1, 'kind': 'babs-mriqc-evidence',
                   'source_dataset_name': source.name, 'source_dataset_id': source_id,
                   'source_dataset_commit': commit, 'raw_gitlink': 'sourcedata/raw',
                   'input_datalad_commit': input_commit, 'archives': archive_records,
                   'evidence': sorted(evidence, key=lambda row: row['path'])}
        (staging / RECEIPT).parent.mkdir(parents=True)
        (staging / RECEIPT).write_text(_json(receipt))
        if (_git(source, 'rev-parse', 'HEAD', runner=runner) != commit
                or any(_sha256(path) != record['sha256'] for (_, path), record in zip(archives, archive_records))):
            raise RuntimeError('MRIQC source changed while preparing evidence')
        from network_qa.compiler import _archive_evidence_commit
        if _archive_evidence_commit(staging) != input_commit:
            raise RuntimeError('archive evidence is not bound to the committed BABS snapshot')
        expected = {path.relative_to(staging): _sha256(path) for path in staging.rglob('*') if path.is_file()}
        if destination.exists() or destination.is_symlink():
            _require_dataset(destination, runner=runner)
            from network_qa._evidence_paths import iter_evidence_files
            actual = {path.relative_to(destination): _sha256(path) for path in iter_evidence_files(destination)
                      if not path.relative_to(destination).parts[0].startswith('.')}
            if actual != expected:
                raise RuntimeError('existing review evidence does not match the merged archive snapshot')
            if _gitlink(study, 'HEAD', destination.relative_to(study).as_posix(), runner=runner) != _git(destination, 'rev-parse', 'HEAD', runner=runner):
                raise RuntimeError('existing review evidence is not registered in the study')
            created = False
        else:
            runner(('datalad', 'create', '-c', 'text2git', '-d', str(study), str(destination)), check=True)
            for relative in expected:
                output = destination / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(staging / relative, output)
            runner(('datalad', 'save', '-d', str(destination), '-m', 'Prepare MRIQC archive review evidence'), check=True)
            runner(('datalad', 'save', '-d', str(study), '-m', 'Register MRIQC review evidence', str(destination)), check=True)
            created = True
    return MRIQCEvidenceResult(destination, source, commit, input_commit, len(archives), created)
