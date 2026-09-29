"""Pinned FSQC evidence between reconstruction and manual surface approval."""
from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

from network_fmri.mriqc import _git, _json, _require_dataset, _sha256
from network_fmri.registration_qc import _registered
from network_fmri.records.native_lineage import file_record, transformation

VERSION = '2.1.4'
RECEIPT = 'code/network_fmri/surface-qc.json'
# Inputs used by the enabled FSQC modules; the receipt also pins the full dataset.
INPUT_FILES = {'mri/orig.mgz', 'mri/norm.mgz', 'mri/aseg.mgz', 'mri/aparc+aseg.mgz',
               'mri/transforms/talairach.lta', 'scripts/recon-all.log'}
INPUT_FILES.update(f'{folder}/{hemi}.{suffix}' for hemi in ('lh', 'rh')
                   for folder, suffix in [('surf', 'white'), ('surf', 'pial'), ('surf', 'inflated'),
                                          ('surf', 'w-g.pct.mgh'), ('label', 'cortex.label'),
                                          ('label', 'aparc.annot')])
VIEWS = ('x=-40', 'x=-20', 'x=0', 'x=20', 'x=40', 'y=-60', 'y=-30', 'y=0', 'y=30',
         'z=-20', 'z=0', 'z=20', 'z=40', 'z=60')


def validate_outputs(root: Path, subjects) -> dict:
    """FSQC may report module failures even when its process exits successfully."""
    with (root / 'fsqc-results.csv').open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != len(subjects) or {r['subject'] for r in rows} != {'sub-' + s for s in subjects}:
        raise RuntimeError('FSQC results do not match the requested subjects')
    result = {}
    for row in rows:
        subject = row.pop('subject')
        status = dict(line.split(':', 1) for line in
                      (root / 'status' / subject / 'status.txt').read_text().splitlines() if line)
        for module in ('metrics', 'screenshots', 'surfaces'):
            if status.get(module, '').strip() != '0':
                raise RuntimeError(f'FSQC {module} failed or was not run for {subject}')
            if module != 'metrics' and not any((root / module / subject).glob('*.png')):
                raise RuntimeError(f'FSQC {module} images missing for {subject}')
        metrics = {}
        for name, value in row.items():
            number = float(value) if value else float('nan')
            metrics[name] = number if math.isfinite(number) else None
        result[subject.removeprefix('sub-')] = metrics
    return result


def prepare_surface_qc(config, subjects_dir: Path, *, runner=subprocess.run) -> Path:
    """Run on the controller's compute allocation; never modify reconstructions."""
    image = config.fsqc
    if image is None or image.version != VERSION:
        raise RuntimeError(f'configure a checksum-pinned [fsqc] container at version {VERSION}')
    if _sha256(image.image) != image.sha256:
        raise RuntimeError('FSQC container checksum mismatch')
    study = config.mechababs.study_dir.resolve()
    surface = subjects_dir.parent.resolve()
    _require_dataset(study, runner=runner)
    source_id, commit = _registered(surface, study, runner)
    surface_receipt = json.loads((surface / 'code/network_fmri/surface-evidence.json').read_text())
    inputs = {'surface_dataset_id': source_id, 'surface_commit': commit,
              'surface_project': surface.relative_to(study).as_posix(),
              'source_project': surface_receipt['source_project'], 'subjects': list(config.subjects),
              'software': {'FSQC': {'version': VERSION, 'container_sha256': image.sha256}},
              'parameters': {'screenshots_views': list(VIEWS), 'screenshots_layout': [3, 5],
                             'surfaces': True, 'outlier': len(config.subjects) >= 10}}
    destination = surface.with_name(surface.name.replace('FreeSurfer-8.2.0', f'fsqc-{VERSION}', 1).removesuffix('+review'))
    if destination == surface:
        raise RuntimeError('unexpected FreeSurfer evidence path')
    if destination.exists():
        _registered(destination, study, runner)
        stored = json.loads((destination / RECEIPT).read_text())
        if stored.get('status') != 'success' or stored['inputs'] != inputs:
            raise RuntimeError('FSQC inputs changed; previous evidence preserved')
        for row in stored['outputs']:
            if _sha256(destination / row['path']) != row['sha256']:
                raise RuntimeError('FSQC output changed')
        validate_outputs(destination, config.subjects)
        return destination
    with tempfile.TemporaryDirectory(prefix='.fsqc-', dir=surface.parent) as temporary:
        staging = Path(temporary) / 'output'
        staging.mkdir()
        scratch = Path(temporary) / 'tmp'
        xkb = Path(temporary) / 'xkb'
        scratch.mkdir()
        xkb.mkdir()
        command = ('singularity', 'exec', '--cleanenv', '--bind',
                   f'{surface}:/input:ro,{staging}:/output,{scratch}:/tmp,{xkb}:/var/lib/xkb', str(image.image),
                   'xvfb-run', '-a', '-e', '/output/xvfb.log', '-s',
                   '-screen 0 1280x1024x24 -nolisten tcp -noreset', 'python3', '/app/fsqc/run_fsqc',
                   '--subjects_dir', '/input/subjects', '--output_dir', '/output',
                   '--subjects', *('sub-' + s for s in config.subjects),
                   '--screenshots', '--screenshots_views', *VIEWS, '--screenshots_layout', '3', '5',
                   '--surfaces', '--exit-on-error')
        if len(config.subjects) >= 10:
            command += ('--outlier',)
        log = staging / 'fsqc.log'
        try:
            with log.open('w') as stream:
                runner(command, check=True, stdout=stream, stderr=subprocess.STDOUT,
                       env={**os.environ, 'SINGULARITYENV_OMP_NUM_THREADS': '1',
                            'SINGULARITYENV_OPENBLAS_NUM_THREADS': '1', 'SINGULARITYENV_MPLBACKEND': 'Agg'})
            metrics = validate_outputs(staging, config.subjects)
        except Exception as error:
            # Keep failed logs outside the study; no successful derivative is published.
            failed = config.paths.log_dir / 'fsqc-failed.log'
            failed.parent.mkdir(parents=True, exist_ok=True)
            failed.write_text(log.read_text() if log.exists() else str(error))
            raise RuntimeError(f'FSQC failed; see {failed}: {error}') from error
        runner(('datalad', 'create', '--force', str(staging)), check=True)
        identity = _git(staging, 'config', '--file', '.datalad/config', '--get', 'datalad.dataset.id', runner=runner)
        lineage_dir = staging / 'code/network_fmri/lineage'
        lineage_dir.mkdir(parents=True)
        for subject in config.subjects:
            products = [{'path': p.relative_to(staging).as_posix(), 'sha256': _sha256(p)}
                        for folder in ('screenshots', 'surfaces', 'metrics', 'status')
                        for p in (staging / folder / f'sub-{subject}').glob('*') if p.is_file()]
            inventory = surface_receipt['inventories'][subject]
            sources = [file_record(source_id, {'path': f'subjects/sub-{subject}/{p}', 'sha256': digest})
                       for p, digest in inventory.items() if p in INPUT_FILES or p.startswith("stats/")]
            lineage = transformation('fsqc', f'sub-{subject}', sources,
                                     [file_record(identity, p, availability='available') for p in products],
                                     software=inputs['software'], parameters=inputs['parameters'])
            (lineage_dir / f'sub-{subject}_fsqc.json').write_text(_json(lineage))
        (staging / 'dataset_description.json').write_text(_json({
            'Name': 'FreeSurfer surface quality evidence', 'BIDSVersion': '1.10.0',
            'DatasetType': 'derivative', 'GeneratedBy': [{'Name': 'FSQC', 'Version': VERSION}]}))
        outputs = [{'path': p.relative_to(staging).as_posix(), 'sha256': _sha256(p)}
                   for p in sorted(staging.rglob('*')) if p.is_file()
                   and not {'.git', '.datalad'}.intersection(p.relative_to(staging).parts)]
        (staging / RECEIPT).write_text(_json({'schema_version': 1, 'stage': 'fsqc', 'status': 'success',
                                           'inputs': inputs, 'subjects': metrics, 'outputs': outputs}))
        if _registered(surface, study, runner)[1] != commit:
            raise RuntimeError('surface evidence changed during FSQC')
        runner(('datalad', 'save', '-d', str(staging), '-m', 'Generate FSQC surface review evidence'), check=True)
        staging.rename(destination)
    runner(('datalad', 'save', '-d', str(study), '-m', 'Register FSQC evidence', str(destination)), check=True)
    return destination
