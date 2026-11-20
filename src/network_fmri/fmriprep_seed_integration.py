"""Build complete corrected subject ZIPs while preserving unaffected entries."""
from __future__ import annotations

import copy
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

from network_fmri.bold_reference_qc import sha256
from network_fmri.fmriprep_evidence import run_key


def safe_member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '\\' in name or any(part in {'', '.', '..'} for part in name.rstrip('/').split('/')):
        raise ValueError(f'unsafe archive path: {name}')
    return path


def repaired_member(path, run):
    """Only acquisition-specific functional outputs and reportlets are replaced."""
    path = PurePosixPath(path)
    return run_key(path) == run and ('func' in path.parts or 'figures' in path.parts)


def fieldmap_mapping(paths, seed):
    """Give each independently fitted fieldmap an acquisition-specific identity."""
    labels = set()
    for path in paths:
        if 'fmap' in PurePosixPath(path).parts:
            labels.update(re.findall(r'_fmapid-([^_]+)_', PurePosixPath(path).name))
    labels = {label: f'{label}seed{seed}' for label in labels}
    mapping = {}
    for path in paths:
        renamed = path
        for old, new in labels.items():
            renamed = renamed.replace(f'_fmapid-{old}_', f'_fmapid-{new}_')
            if '_desc-fmap_xfm.' in renamed:
                renamed = renamed.replace(f'_to-{old}_', f'_to-{new}_')
        mapping[path] = renamed
    return mapping


def rewrite_sources(value, mapping, seed, key=None):
    """Resolve temporary anatomy links locally and keep seeded fieldmaps distinct."""
    if isinstance(value, dict):
        return {k: rewrite_sources(v, mapping, seed, k) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite_sources(v, mapping, seed, key) for v in value]
    if not isinstance(value, str):
        return value
    if value.startswith('bids:reuse:'):
        relative = value.removeprefix('bids:reuse:')
        if 'anat' not in safe_member(relative).parts:
            raise ValueError('reuse source is not original anatomy')
        return 'bids::' + relative
    if value.startswith('bids::'):
        relative = value.removeprefix('bids::')
        return 'bids::' + mapping.get(relative, relative)
    if key == 'B0FieldIdentifier':
        return value + f'seed{seed}'
    if key in {'AnatomicalReference', 'AssociatedCoefficients'}:
        matches = {PurePosixPath(new).name for old, new in mapping.items() if PurePosixPath(old).name == value}
        if len(matches) > 1:
            raise ValueError('ambiguous fieldmap basename')
        return next(iter(matches), value)
    return value


def stage_repaired_output(bundle, target, run, seed):
    """Extract only repaired functional data, fieldmaps and their reportlets."""
    target.mkdir(parents=True, exist_ok=False)
    with tarfile.open(bundle) as archive:
        selected = {}
        seen = set()
        for member in archive.getmembers():
            parts = safe_member(member.name).parts
            if parts[0] != 'fmriprep' or (not member.isfile() and not member.isdir()):
                raise ValueError('unexpected derivative archive root or member type')
            if member.name in seen:
                raise ValueError('duplicate derivative archive member')
            seen.add(member.name)
            if not member.isfile():
                continue
            relative = PurePosixPath(*parts[1:]).as_posix()
            is_fmap = 'fmap' in parts or ('figures' in parts and '_fmapid-' in parts[-1])
            if repaired_member(relative, run) or is_fmap:
                if parts[1] != run.split('_')[0]:
                    raise ValueError('repair contains another subject')
                selected[relative] = member
        if not any('func' in PurePosixPath(path).parts for path in selected):
            raise ValueError('repair archive has no selected functional output')
        mapping = fieldmap_mapping(selected, seed)
        if len(set(mapping.values())) != len(mapping):
            raise ValueError('renamed repair paths collide')
        for old, member in selected.items():
            destination = target/mapping[old]
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, destination.open('xb') as output:
                if old.endswith('.json'):
                    metadata = rewrite_sources(json.load(source), mapping, seed)
                    output.write((json.dumps(metadata, indent=2)+'\n').encode())
                else:
                    shutil.copyfileobj(source, output, length=8*1024*1024)
    return mapping


def copy_zip_with_overlay(source, destination, replacements, removed):
    """Copy unchanged compressed members verbatim; write new central offsets.

    ZIP files cannot share individual members through filesystem hardlinks.
    Copying their compressed records avoids decompressing/recompressing terabytes
    of unchanged images. The few ZipFile writer internals used here are exercised
    against stored/deflated records, data descriptors and ZIP64 in the tests.
    """
    destination = Path(destination)
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, 'x', allowZip64=True) as output:
        members = original.infolist()
        names = [item.filename for item in members]
        if len(set(names)) != len(names):
            raise ValueError('duplicate original ZIP entry')
        for name in names:
            safe_member(name)
        for name in replacements:
            safe_member(name)
        ordered = sorted(members, key=lambda item: item.header_offset)
        ends = {item.header_offset: (ordered[i+1].header_offset if i+1 < len(ordered) else original.start_dir)
                for i, item in enumerate(ordered)}
        for item in ordered:
            if item.filename in removed or item.filename in replacements:
                continue
            if item.flag_bits & 1:
                raise ValueError('encrypted original member')
            size = ends[item.header_offset] - item.header_offset
            if size <= 0:
                raise ValueError('invalid original ZIP record offsets')
            original.fp.seek(item.header_offset)
            cloned = copy.copy(item)
            cloned.header_offset = output.fp.tell()
            remaining = size
            while remaining:
                chunk = original.fp.read(min(8*1024*1024, remaining))
                if not chunk:
                    raise ValueError('truncated original ZIP record')
                output.fp.write(chunk)
                remaining -= len(chunk)
            output.filelist.append(cloned)
            output.NameToInfo[cloned.filename] = cloned
            output.start_dir = output.fp.tell()
            output._didModify = True
        for name, path in sorted(replacements.items()):
            # NIfTI/GIFTI and SVG payloads are already compressed or modest in size.
            output.write(path, name, compress_type=zipfile.ZIP_STORED)
        output.comment = original.comment
    with zipfile.ZipFile(destination) as built:
        expected = (set(names)-set(removed)) | set(replacements)
        if set(built.namelist()) != expected:
            raise ValueError('integrated ZIP membership differs from overlay')
        for item in members:
            if item.filename not in removed and item.filename not in replacements:
                current = built.getinfo(item.filename)
                if (current.CRC, current.file_size, current.compress_size, current.compress_type) != (
                        item.CRC, item.file_size, item.compress_size, item.compress_type):
                    raise ValueError('unchanged ZIP member metadata changed')
    return {'original_members': len(names), 'final_members': len(expected),
            'replaced_or_added': len(replacements), 'removed': len(set(names) & set(removed)),
            'bytes': destination.stat().st_size}


def validate_surface_records(directory, receipt):
    """Verify retained surface evidence, including receipts predating the summary flag."""
    from network_fmri.fmriprep_seed_rerun import inventory_difference
    before, after, difference, report = (
        json.loads((directory/name).read_text()) for name in (
            'surface-before.json', 'surface-after.json', 'surface-diff.json', 'surface-derivations.json'))
    if ('surface_changes_expected' in receipt and receipt['surface_changes_expected'] is not True):
        raise ValueError('unexpected surface changes need consistency assessment')
    if difference != inventory_difference(before, after):
        raise ValueError('surface difference disagrees with retained inventories')
    allowed = {'surf/lh.midthickness', 'surf/rh.midthickness'}
    if difference['changed'] or difference['removed'] or set(difference['added']) - allowed:
        raise ValueError('original surface files changed or unexpected files were added')
    if 'derived_geometry' in report:
        if (report['difference'] != difference
                or report['expected_midthickness_derivation_only'] is not True):
            raise ValueError('surface derivation report does not confirm expected additions')
        geometry = report['derived_geometry']
    else:
        geometry = report
    if set(geometry) != set(difference['added']):
        raise ValueError('surface geometry evidence does not cover every addition')
    for name, digest in difference['added'].items():
        item = geometry[name]
        if (item['sha256'] != digest or item['white_topology_unchanged'] is not True
                or item['vertices'] <= 0 or item['triangles'] <= 0):
            raise ValueError('surface geometry evidence differs from expected derivation')
    return {'legacy_receipt': 'surface_changes_expected' not in receipt,
            'evidence_sha256': {name: sha256(directory/name) for name in (
                'surface-before.json', 'surface-after.json', 'surface-diff.json', 'surface-derivations.json')}}


def validated_attempt(directory, planned, plan_digest, parent_digest=None):
    proof = json.loads((directory/'provenance.json').read_text())
    receipt = json.loads((directory/'receipt.json').read_text())
    after = json.loads((directory/'after.json').read_text())
    run = planned['before']['run']
    lineage_matches = proof['plan_sha256'] == plan_digest or (
        parent_digest is not None and proof['plan_sha256'] == parent_digest
        and proof['seeds'] in planned['attempt_seeds'][:3])
    if (proof['run'] != run or after['run'] != run or not lineage_matches
            or proof['seeds'] not in planned['attempt_seeds']
            or proof['seeds'] != receipt['seeds'] or proof['seeds'] != after['seeds']
            or not receipt['automatic_checks_passed'] or after['status'] != 'pass'):
        raise ValueError(f'unaccepted or unrelated attempt: {run}')
    validate_surface_records(directory, receipt)
    if receipt['after_sha256'] != sha256(directory/'after.json'):
        raise ValueError('after metrics differ from accepted receipt')
    if receipt['derivatives_sha256'] != sha256(directory/'derivatives.tar.gz'):
        raise ValueError('derivative archive differs from accepted receipt')
    return proof, receipt, after


def build_subject(args):
    from network_fmri.fmriprep_seed_rerun import load_plan
    if not os.environ.get('SLURM_JOB_ID') or not os.environ.get('L_SCRATCH_JOB'):
        raise RuntimeError('integration requires a Slurm compute allocation')
    plan = load_plan(args.plan)
    planned = [entry for entry in plan['runs'] if entry['before']['subject'] == args.subject]
    selection = json.loads(args.attempts.read_text())
    if not planned or set(selection) != {entry['before']['run'] for entry in planned}:
        raise ValueError('subject integration must cover exactly its selected acquisitions')
    archives = {entry['before']['archive'] for entry in planned}
    keys = {entry['before']['annex_key'] for entry in planned}
    if len(archives) != 1 or len(keys) != 1:
        raise ValueError('ambiguous original subject archive')
    original = Path(next(iter(archives)))
    if not original.is_symlink() or Path(os.readlink(original)).name != next(iter(keys)):
        raise ValueError('original archive identity changed')
    if sha256(args.image) != args.image_sha256:
        raise ValueError('integration must use the pinned image')
    destination = args.output/original.name
    if destination.exists():
        raise ValueError('refusing to overwrite a completed subject archive')
    partial = args.output/(original.name + '.partial')
    report = {'subject': args.subject, 'original_archive': str(original), 'annex_key': next(iter(keys)),
              'plan_sha256': sha256(args.plan), 'runs': {}, 'job_id': os.environ['SLURM_JOB_ID'],
              'integrator_sha256': sha256(__file__)}
    with tempfile.TemporaryDirectory(prefix='seed-integration-', dir=os.environ['L_SCRATCH_JOB']) as temporary:
        local = Path(temporary)
        report_root = local/'reports'
        report_root.mkdir()
        replacements = {}
        removed = set()
        with zipfile.ZipFile(original) as archive:
            original_files = {member.filename: member for member in archive.infolist()}
            roots = {safe_member(name).parts[0] for name in original_files}
            if len(roots) != 1:
                raise ValueError('original archive has multiple dataset roots')
            archive_root = next(iter(roots))
            for name, member in original_files.items():
                relative = PurePosixPath(name).relative_to(archive_root)
                if member.is_dir():
                    continue
                if ('figures' in relative.parts or relative.parts[0] == 'logs'
                        or relative.as_posix() == 'dataset_description.json'):
                    target = report_root/relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, target.open('xb') as output:
                        shutil.copyfileobj(source, output)
        for entry in planned:
            run = entry['before']['run']
            directory = Path(selection[run])
            proof, receipt, after = validated_attempt(directory, entry, report['plan_sha256'],
                plan.get('retry_extension', {}).get('parent_sha256'))
            stage = local/run
            mapping = stage_repaired_output(directory/'derivatives.tar.gz', stage, run, proof['seeds']['master'])
            previous = {name for name in original_files if repaired_member(name, run)}
            previous_functional = {PurePosixPath(name).relative_to(archive_root).as_posix()
                                   for name in previous if 'func' in PurePosixPath(name).parts}
            if not previous_functional <= mapping.keys():
                raise ValueError(f'rerun is missing original functional products: {sorted(previous_functional-mapping.keys())}')
            removed.update(previous)
            for name in previous:
                relative = PurePosixPath(name).relative_to(archive_root)
                if 'figures' in relative.parts:
                    (report_root/relative).unlink(missing_ok=True)
            for old, relative in mapping.items():
                name = archive_root+'/'+relative
                if name in replacements:
                    raise ValueError('multiple acquisitions write the same integrated path')
                replacements[name] = stage/relative
                if 'figures' in PurePosixPath(relative).parts:
                    target = report_root/relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(stage/relative, target)
            run_report = {'seeds': proof['seeds'], 'attempt': receipt['attempt'],
                          'before': entry['before'], 'after': after,
                          'anatomy_evidence': proof['anatomy_evidence'],
                          'surface_proof': proof['surface_proof'],
                          'surface_integrity': proof.get('surface_integrity'),
                          'attempt_root': str(directory), 'receipt': receipt, 'renamed_paths': mapping}
            provenance = local/(run+'.json')
            provenance.write_text(json.dumps(run_report, indent=2)+'\n')
            replacements[archive_root+'/code/seed-repairs/'+run+'.json'] = provenance
            for name in ('provenance.json', 'receipt.json', 'after.json', 'preflight.json', 'review-decision.json',
                         'execution.json', 'surface-before.json', 'surface-after.json',
                         'surface-diff.json', 'surface-derivations.json'):
                path = directory/name
                if path.is_file():
                    replacements[archive_root+'/code/seed-repairs/'+run+'/'+name] = path
            for path in (directory/'evidence').iterdir():
                if not path.is_file() or path.is_symlink():
                    raise ValueError('unexpected N4 evidence entry')
                replacements[archive_root+'/code/seed-repairs/'+run+'/evidence/'+path.name] = path
            report['runs'][run] = {'attempt_root': str(directory), 'seeds': proof['seeds'],
                                    'receipt_sha256': sha256(directory/'receipt.json')}
        sessions = sorted({re.search(r'(?:^|_)ses-([^_]+)', run)[1] for run in selection})
        spec = local/'reports-spec.json'
        spec.write_text(json.dumps({'report_root': str(report_root), 'subject': args.subject,
                                    'sessions': sessions, 'run_uuid': 'network-seed-repairs-v3'}))
        report_receipt = local/'reports.json'
        subprocess.run(['apptainer', 'exec', '--cleanenv', '--containall',
                        '-B', str(local)+':'+str(local), '-B', str(local)+':/tmp',
                        '-B', str(args.code)+':/code:ro',
                        '--env', 'PYTHONPATH=/code/src,SLURM_JOB_ID='+os.environ['SLURM_JOB_ID'],
                        str(args.image), 'python', '-m', 'network_fmri.fmriprep_seed_workflow',
                        'reports', str(spec), str(report_receipt)], check=True)
        for name in json.loads(report_receipt.read_text()):
            replacements[archive_root+'/'+name] = report_root/name
        report['zip'] = copy_zip_with_overlay(original, partial, replacements, removed)
        report['sha256'] = sha256(partial)
        report['replacements'] = {name: sha256(path) for name, path in replacements.items()}
        report['original_anatomy_and_unselected_members'] = 'Compressed ZIP records copied verbatim.'
        with (args.output/(original.name+'.integration.json')).open('x') as stream:
            json.dump(report, stream, indent=2)
        os.replace(partial, destination)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'attempts', 'output', 'image', 'code'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--subject', required=True)
    parser.add_argument('--image-sha256', required=True)
    build_subject(parser.parse_args(argv))


if __name__ == '__main__':
    main()
