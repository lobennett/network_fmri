"""Render fmriprepviz after BABS merges fMRIPrep, before final output review."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile
import zipfile

from network_fmri.mriqc import _git, _gitlink, _json, _require_dataset, _sha256
from network_fmri.pipeline import require_committed_surface_approval
from network_fmri.processing import ProcessingManager
from network_fmri.records.native_lineage import file_record, transformation
from network_fmri.surface_evidence import prepare_surface_evidence

VERSION = '0.1.0'
REVISION = 'd684fcba661c382d0d6c4cc0906bc36fbaab38dd'
RECEIPT = 'code/network_fmri/registration-qc.json'
SOFTWARE = {'fmriprepviz': {'version': VERSION, 'commit': REVISION}}


def extract_boldrefs(archive: Path, subject: str, target: Path) -> list[dict]:
    """Copy only native-space references; validate paths before writing anything."""
    selected, seen, roots = [], set(), set()
    with zipfile.ZipFile(archive) as stream:
        for member in stream.infolist():
            name = member.filename
            parts = name.rstrip('/').split('/')
            if (PurePosixPath(name).is_absolute() or '\\' in name or '\x00' in name
                    or any(p in {'', '.', '..'} for p in parts) or name in seen):
                raise ValueError(f'unsafe or duplicate ZIP member: {name}')
            seen.add(name)
            if member.is_dir() or not name.endswith('_space-T1w_boldref.nii.gz'):
                continue
            if (len(parts) < 4 or parts[1] != f'sub-{subject}' or parts[-2] != 'func'
                    or not parts[-1].startswith(f'sub-{subject}_') or member.flag_bits & 1
                    or stat.S_IFMT(member.external_attr >> 16) not in {0, stat.S_IFREG}):
                raise ValueError(f'invalid native BOLD reference: {name}')
            roots.add(parts[0])
            selected.append((member, Path(*parts[1:])))
        if not selected or len(roots) != 1:
            raise RuntimeError(f'expected one fMRIPrep root with T1w BOLD references for sub-{subject}')
        records = []
        for member, relative in selected:
            output = target / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            with stream.open(member) as src, output.open('xb') as dst:
                shutil.copyfileobj(src, dst)
            records.append({'path': relative.as_posix(), 'sha256': _sha256(output),
                            'archive_member': member.filename})
    return sorted(records, key=lambda row: row['path'])


def _registered(root, study, runner):
    identity = _require_dataset(root, runner=runner)
    commit = _git(root, 'rev-parse', 'HEAD', runner=runner)
    if _gitlink(study, 'HEAD', root.relative_to(study).as_posix(), runner=runner) != commit:
        raise RuntimeError(f'derivative is not registered at its current commit: {root}')
    return identity, commit


def prepare_registration_qc(config, *, runner=subprocess.run) -> Path:
    """Generate one movie/viewer per subject and save a separate DataLad derivative."""
    stage = next(s for s in ProcessingManager(config, runner=runner).plan() if s.stage == 'fmriprep')
    if stage.state != 'complete':
        raise RuntimeError('fMRIPrep must be merged before registration QC')
    require_committed_surface_approval(config, runner)
    subjects_dir = prepare_surface_evidence(config, runner=runner)
    study = config.mechababs.study_dir.resolve()
    _require_dataset(study, runner=runner)
    source = study / stage.project
    source_id, commit = _registered(source, study, runner)
    surface_root = subjects_dir.parent
    surface_id, surface_commit = _registered(surface_root, study, runner)
    surface_receipt = json.loads((surface_root / 'code/network_fmri/surface-evidence.json').read_text())
    if _gitlink(source, commit, 'sourcedata/FreeSurfer-8.2.0', runner=runner) != surface_receipt['source_dataset_commit']:
        raise RuntimeError('reviewed ribbon does not match the reconstruction used by fMRIPrep')
    tracked = _git(source, 'ls-tree', '-r', '--name-only', commit, runner=runner).splitlines()
    subjects = []
    for subject in config.subjects:
        matches = [p for p in tracked if '/' not in p and p.startswith(f'sub-{subject}_') and p.endswith('.zip')]
        if len(matches) != 1:
            raise RuntimeError(f'expected one merged fMRIPrep archive for sub-{subject}')
        archive = source / matches[0]
        if not archive.is_file():
            runner(('datalad', 'get', '-d', str(source), str(archive)), check=True)
        ribbon = subjects_dir / f'sub-{subject}/mri/ribbon.mgz'
        subjects.append({'subject': subject, 'archive': {'path': archive.name, 'sha256': _sha256(archive)},
                         'ribbon': {'path': ribbon.relative_to(surface_root).as_posix(), 'sha256': _sha256(ribbon)}})
    inputs = {'source_dataset_id': source_id, 'source_commit': commit, 'source_project': stage.project,
              'surface_dataset_id': surface_id, 'surface_commit': surface_commit,
              'software': SOFTWARE, 'subjects': subjects, 'parameters': {'n_cuts': 7, 'fps': 2}}
    destination = source.with_name(source.name.replace('fMRIPrep-', f'fmriprepviz-{VERSION}-', 1))
    if destination == source:
        raise RuntimeError('unexpected fMRIPrep derivative name')
    if destination.exists() or destination.is_symlink():
        _registered(destination, study, runner)
        receipt = json.loads((destination / RECEIPT).read_text())
        if receipt['inputs'] != inputs:
            raise RuntimeError('registration QC inputs changed; previous outputs were preserved')
        for item in receipt['outputs']:
            if _sha256(destination / item['path']) != item['sha256']:
                raise RuntimeError('registration QC output changed')
        return destination
    with tempfile.TemporaryDirectory(prefix='.registration-qc-', dir=source.parent) as temporary:
        staging = Path(temporary) / 'output'
        staging.mkdir()
        runner(('datalad', 'create', '--force', str(staging)), check=True)
        identity = _git(staging, 'config', '--file', '.datalad/config', '--get', 'datalad.dataset.id', runner=runner)
        evidence = []
        for row in subjects:
            subject = row['subject']
            with tempfile.TemporaryDirectory(prefix='inputs-', dir=temporary) as workspace:
                refs = extract_boldrefs(source / row['archive']['path'], subject, Path(workspace))
                output = staging / f'sub-{subject}/sub-{subject}_desc-registration.gif'
                output.parent.mkdir(parents=True)
                command = ('fmriprepviz', workspace, '--subject', subject, '--fs-dir',
                           str(subjects_dir / f'sub-{subject}'), '--n-cuts', '7', '--fps', '2', '-o', str(output))
                log = output.with_suffix('.log')
                try:
                    with log.open('w') as stream:
                        runner(command, check=True, stdout=stream, stderr=subprocess.STDOUT,
                               env={**os.environ, 'MPLBACKEND': 'Agg'})
                except subprocess.CalledProcessError as error:
                    raise RuntimeError(f'fmriprepviz failed for sub-{subject}:\n{log.read_text()[-4000:]}') from error
                products = [{'path': p.relative_to(staging).as_posix(), 'sha256': _sha256(p)}
                            for p in (output, output.with_suffix('.html'))]
                provenance = transformation('fmriprepviz', f'sub-{subject}',
                    [file_record(source_id, row['archive']), file_record(surface_id, row['ribbon'])],
                    [file_record(identity, item, availability='available') for item in products],
                    software=SOFTWARE, parameters={'boldrefs': refs, **inputs['parameters']})
                lineage = staging / f'code/network_fmri/lineage/sub-{subject}_registration.json'
                lineage.parent.mkdir(parents=True, exist_ok=True)
                lineage.write_text(_json(provenance))
                evidence.extend(products + [{'path': p.relative_to(staging).as_posix(), 'sha256': _sha256(p)}
                                            for p in (log, lineage)])
        description = staging / 'dataset_description.json'
        description.write_text(_json({'Name': 'BOLD-to-anatomical registration review', 'BIDSVersion': '1.10.0',
            'DatasetType': 'derivative', 'GeneratedBy': [{'Name': 'fmriprepviz', 'Version': VERSION,
                'CodeURL': f'https://github.com/poldrack/fmriprepviz/tree/{REVISION}'}]}))
        evidence.append({'path': description.name, 'sha256': _sha256(description)})
        (staging / RECEIPT).write_text(_json({'schema_version': 1, 'stage': 'fmriprepviz',
            'status': 'success', 'inputs': inputs, 'outputs': evidence}))
        if (_registered(source, study, runner)[1] != commit
                or _registered(surface_root, study, runner)[1] != surface_commit
                or any(_sha256(source / r['archive']['path']) != r['archive']['sha256']
                       or _sha256(surface_root / r['ribbon']['path']) != r['ribbon']['sha256'] for r in subjects)):
            raise RuntimeError('registration QC inputs changed during rendering')
        runner(('datalad', 'save', '-d', str(staging), '-m', 'Generate fmriprepviz registration review'), check=True)
        staging.rename(destination)
    runner(('datalad', 'save', '-d', str(study), '-m', 'Register fmriprepviz outputs', str(destination)), check=True)
    return destination
