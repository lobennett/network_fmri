"""Prepare isolated, acquisition-specific fMRIPrep seed repairs."""

from __future__ import annotations

import random
import argparse
import copy
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from network_fmri.bold_reference_qc import ARCHIVED_RELATIVE_MEDIAN_MIN, review_reasons, sha256


def select_affected(summary, reviews):
    """Require a complete audit and recorded visual decisions before repairs."""
    if not summary.get('audit_complete') or summary.get('stop_before_reruns', True):
        raise ValueError('audit requires stopping and reporting before reruns')
    rows = summary['runs']
    by_run = {row['run']: row for row in rows}
    if len(by_run) != len(rows):
        raise ValueError('duplicate audit run')
    assessed_statuses = {'pass', 'flagged', 'borderline'}
    unassessed_statuses = {'missing-archive', 'unavailable-archive', 'incomplete-output'}
    if any(row['status'] not in assessed_statuses | unassessed_statuses for row in rows):
        raise ValueError('audit has unfinished or invalid results')
    assessed = sum(row['status'] in assessed_statuses for row in rows)
    flagged = sum(row['status'] == 'flagged' or 'archived-intensity' in review_reasons(row) for row in rows)
    if not assessed or flagged > 0.1 * assessed:
        raise ValueError('more than 10% flagged, or no assessed runs: stop and report')
    if any('invalid-intensity' in review_reasons(row) for row in rows):
        raise ValueError('audit has invalid or missing intensity measurements')
    required = {row['run'] for row in rows if review_reasons(row)}
    if set(reviews) != required:
        raise ValueError('visual review must cover exactly the coverage and intensity screen candidates')
    selected = []
    for key in sorted(required):
        review = reviews[key]
        if review.get('decision') not in {'rerun', 'keep'} or not str(review.get('reason', '')).strip():
            raise ValueError(f'unresolved visual review: {key}')
        if review['decision'] == 'rerun':
            selected.append(dict(by_run[key], review=dict(review)))
    if len(selected) > 0.1 * assessed:
        raise ValueError('visual review identified more than 10% for rerun: stop and report')
    return selected


def derived_seeds(master):
    """Match fMRIPrep 25.2.7-rc1 config.seeds without changing process PRNG state."""
    if not isinstance(master, int) or isinstance(master, bool) or not 1 <= master <= 65536:
        raise ValueError("master seed must be an integer from 1 through 65536")
    generator = random.Random(master)
    return {"master": master, "ants": generator.randint(1, 65536), "numpy": generator.randint(1, 65536)}


def fresh_seeds(previous):
    """Reject both master-seed reuse and collisions in the derived ANTs seed."""
    masters = {item["master"] for item in previous}
    ants = {item["ants"] for item in previous}
    for _ in range(128):
        candidate = derived_seeds(secrets.randbelow(65536) + 1)
        if candidate['master'] not in masters and candidate['ants'] not in ants:
            return candidate
    candidates = [seed for seed in range(1, 65537) if seed not in masters and derived_seeds(seed)["ants"] not in ants]
    if not candidates:
        raise ValueError("no unused master/ANTs seed pair remains")
    return derived_seeds(secrets.choice(candidates))


def run_filter(key):
    """Filter BOLD only; retain anatomy from other sessions and all echoes."""
    names = {"sub": "subject", "ses": "session", "task": "task", "acq": "acquisition",
             "ce": "ceagent", "rec": "reconstruction", "dir": "direction", "run": "run", "part": "part"}
    result = {}
    for entity in key.split("_"):
        match = re.fullmatch(r"([A-Za-z]+)-([A-Za-z0-9]+)", entity)
        if not match or match[1] not in names or names[match[1]] in result:
            raise ValueError(f"invalid or ambiguous run identity: {key}")
        value = match[2]
        if match[1] == "run":
            if not value.isdigit() or int(value) < 1:
                raise ValueError("run index must be positive")
            # This image's config parser applies 'Query' in value to scalars.
            value = str(int(value))
        result[names[match[1]]] = value
    if not {"subject", "task", "run"} <= result.keys():
        raise ValueError("run identity needs subject, task and run")
    return {"bold": result}


def extract_anatomy(archive_path, subject, target):
    """Stage anatomy alone so --derivatives cannot reuse a bad functional fit."""
    if not re.fullmatch(r's[0-9]+', subject):
        raise ValueError('invalid subject')
    selected, seen, roots = [], set(), set()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            parts = member.filename.rstrip('/').split('/')
            subjects = set(re.findall(r'(?:^|[/_])(sub-[A-Za-z0-9]+)(?=[/_.]|$)', member.filename))
            if (any(p in {'', '.', '..', '.git', '.datalad'} for p in parts)
                    or '\\' in member.filename or '\x00' in member.filename
                    or member.filename in seen or member.flag_bits & 1
                    or stat.S_IFMT(member.external_attr >> 16) not in {0, stat.S_IFREG, stat.S_IFDIR}
                    or subjects - {f'sub-{subject}'}):
                raise ValueError(f'unsafe archive member: {member.filename}')
            roots.add(parts[0])
            seen.add(member.filename)
            relative = Path(*parts[1:])
            if member.is_dir():
                continue
            anatomical = len(parts) >= 4 and parts[1] == f'sub-{subject}' and (
                parts[2] == 'anat' or (len(parts) >= 5 and parts[2].startswith('ses-') and parts[3] == 'anat'))
            if anatomical or relative == Path('dataset_description.json'):
                selected.append((member, relative))
        if len(roots) != 1 or not any(p.name.endswith('_desc-preproc_T1w.nii.gz') for _, p in selected):
            raise ValueError('expected one archive root containing preprocessed T1w anatomy')
        target.mkdir(parents=True, exist_ok=False)
        evidence = []
        for member, relative in selected:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, destination.open('xb') as dst:
                shutil.copyfileobj(src, dst)
            evidence.append({'path': relative.as_posix(), 'archive_member': member.filename, 'sha256': sha256(destination)})
    return evidence


def validate_anatomical_cache(cache, anatomy, evidence):
    """Require original coordinate inputs, never silently recompute missing ones.

    ``cache`` must come from the pinned image's sMRIPrep collector, not a
    filename approximation. ``evidence`` is the original archive extraction
    inventory. Both atlas spaces are needed: CIFTI adds MNI152NLin6Asym.
    """
    original = {row['path']: row['sha256'] for row in evidence}
    if len(original) != len(evidence):
        raise ValueError('duplicate anatomical evidence')

    def verify(path):
        if not isinstance(path, str):
            raise ValueError('missing or ambiguous anatomical input')
        file = Path(path)
        if not file.is_file() or not file.resolve().is_relative_to(anatomy.resolve()):
            raise ValueError(f'anatomical input outside staging: {path}')
        relative = file.resolve().relative_to(anatomy.resolve()).as_posix()
        if original.get(relative) != sha256(file):
            raise ValueError(f'anatomical input differs from original: {path}')

    for key in ('t1w_preproc', 't1w_mask', 't1w_dseg'):
        verify(cache.get(key))
    tpms = cache.get('t1w_tpms')
    if not isinstance(tpms, list) or len(tpms) != 3:
        raise ValueError('three original tissue maps required')
    for label, path in zip(('GM', 'WM', 'CSF'), tpms):
        verify(path)
        if f'_label-{label}_' not in Path(path).name:
            raise ValueError('tissue maps must be ordered GM, WM, CSF')
    transforms = cache.get('transforms', {})
    for space in ('MNI152NLin2009cAsym', 'MNI152NLin6Asym'):
        for direction in ('forward', 'reverse'):
            verify(transforms.get(space, {}).get(direction))
    verify(transforms.get('fsnative', {}).get('reverse'))
    # Native sphere_reg can be converted from the unchanged FreeSurfer surface;
    # fsLR and MSM registrations themselves must already exist.
    keys = ['white', 'pial', 'midthickness', 'sphere', 'thickness', 'sulc',
            'sphere_reg_fsLR', 'sphere_reg_msm', 'cortex_mask']
    if 'sphere_reg' in cache:
        keys.append('sphere_reg')
    for key in keys:
        pair = cache.get(key)
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError(f'original bilateral surface data required: {key}')
        for path in pair:
            verify(path)
        hemispheres = [re.search(r'_hemi-([LR])_', Path(path).name) for path in pair]
        if any(h is None for h in hemispheres) or {h[1] for h in hemispheres} != {'L', 'R'}:
            raise ValueError(f'expected one original file per hemisphere: {key}')

    import nibabel as nib
    from network_fmri.bold_reference_qc import _same_grid
    reference = nib.load(cache['t1w_preproc'])
    for path in [cache['t1w_mask'], cache['t1w_dseg'], *tpms]:
        _same_grid(reference, nib.load(path))
    return cache


def build_command(*, raw, output, work, anatomy, surfaces, license_file,
                  filter_file, run, seeds, previous):
    """Retain full-app settings while selecting one acquisition and fresh work."""
    entities = run_filter(run)['bold']
    if seeds != derived_seeds(seeds['master']) or any(
        seeds['master'] == old['master'] or seeds['ants'] == old['ants'] for old in previous
    ):
        raise ValueError('seed must be new in both master and ANTs sequences')
    for directory in (output, work):
        if directory.exists() or directory.is_symlink():
            raise ValueError('output and work directories must be fresh')
        if directory.resolve().is_relative_to(raw.resolve()):
            raise ValueError('output and work must be outside the raw dataset')
    if not anatomy.is_dir():
        raise ValueError('staged anatomy is missing')
    if any(path.name == 'func' or path.name == 'fmriprep.toml' or '_bold' in path.name
           for path in anatomy.rglob('*')):
        raise ValueError('anatomy reuse must not expose functional cache or old configuration')
    return [
        'fmriprep', str(raw), str(output), 'participant',
        '--participant-label', entities['subject'], '--bids-filter-file', str(filter_file),
        '--derivatives', 'reuse=' + str(anatomy), '-w', str(work),
        '--level', 'full', '--dummy-scans', '0',
        '--output-spaces', 'MNI152NLin2009cAsym:res-2', 'T1w', 'fsnative', 'fsaverage6',
        '--cifti-output', '91k', '--project-goodvoxels', '--random-seed', str(seeds['master']),
        '--skull-strip-fixed-seed', '--skull-strip-t1w', 'force', '--no-submm-recon',
        '--use-syn-sdc', 'warn', '--me-output-echos', '--notrack', '--md-only-boilerplate',
        '--skip-bids-validation', '--stop-on-first-crash', '--fs-license-file', str(license_file),
        '--fs-no-resume', '--no-track-sessions', '--fs-subjects-dir', str(surfaces),
        '--nprocs', '4', '--omp-nthreads', '2', '--mem-mb', '32000',
    ]


def make_plan(summary, reviews, inventory):
    """Freeze the adjudicated scope and three distinct seed attempts per run."""
    for key in ('archive_commit', 'raw_commit'):
        if summary[key] != inventory[key]:
            raise ValueError('audit and input inventory revisions differ')
    sources = {row['run']: row for row in inventory['runs']}
    used = [row['seeds'] for row in inventory['runs'] if 'seeds' in row]
    selected = select_affected(summary, reviews)
    runs = []
    for row in selected:
        source = sources[row['run']]
        for key in ('archive', 'annex_key', 'config_sha256', 'seeds', 'members'):
            if row[key] != source[key]:
                raise ValueError('audit and input inventory source differs')
        attempts = []
        for _ in range(3):
            seeds = fresh_seeds(used)
            used.append(seeds)
            attempts.append(seeds)
        runs.append({'before': row, 'attempt_seeds': attempts})
    return {'schema_version': 1, 'raw_root': inventory['raw_root'],
            'archive_root': inventory['archive_root'],
            'raw_commit': inventory['raw_commit'], 'archive_commit': inventory['archive_commit'],
            'assessed': summary['assessed'], 'runs': runs}


def _git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def check_sources(plan, surface_root, subject):
    """Require the exact original source trees, including FreeSurfer lineage."""
    for root, commit in [('raw_root', 'raw_commit'), ('archive_root', 'archive_commit')]:
        if _git(plan[root], 'rev-parse', 'HEAD') != plan[commit]:
            raise ValueError('source dataset revision changed since audit')
    tree = _git(plan['archive_root'], 'ls-tree', plan['archive_commit'], 'sourcedata/FreeSurfer-8.2.0').split()
    if len(tree) != 4 or tree[:2] != ['160000', 'commit']:
        raise ValueError('missing original FreeSurfer gitlink')
    used = tree[2]
    if _git(surface_root, 'rev-parse', used + '^{tree}') != _git(surface_root, 'rev-parse', 'HEAD^{tree}'):
        raise ValueError('FreeSurfer reconstruction differs from the one used originally')
    archives = list(surface_root.glob(f'sub-{subject}_FreeSurfer-*.zip'))
    if len(archives) != 1 or not archives[0].is_file():
        raise ValueError('missing or ambiguous FreeSurfer archive')
    return archives[0], {'input_commit': used, 'current_commit': _git(surface_root, 'rev-parse', 'HEAD'),
                         'tree': _git(surface_root, 'rev-parse', 'HEAD^{tree}'),
                         'archive': str(archives[0]), 'sha256': sha256(archives[0])}


def isolated_stage(stage, roots):
    stage = stage.resolve()
    for root in roots:
        source = Path(root).resolve()
        if stage.is_relative_to(source) or source.is_relative_to(stage):
            raise ValueError('staging must be separate from all source datasets')
    return stage


def archive_attempt_output(output, attempt):
    """Keep completed or partial derivatives even when later checks fail."""
    if not output.is_dir():
        return None
    bundle = attempt/'derivatives.tar.gz'
    with tarfile.open(bundle, 'x:gz') as archive:
        archive.add(output, arcname='fmriprep')
    return sha256(bundle)


def consumed_attempts(planned, directories, plan_sha256, *, allow_exhausted=False):
    """Continue a frozen plan without recycling an executed seed after a crash."""
    consumed = {}
    for directory in directories:
        provenance_path = directory/'provenance.json'
        proof = json.loads(provenance_path.read_text())
        if proof['run'] != planned['before']['run'] or proof['plan_sha256'] != plan_sha256:
            raise ValueError('previous attempt belongs to a different run or plan')
        seeds = proof['seeds']
        if seeds not in planned['attempt_seeds']:
            raise ValueError('previous seed is not in the frozen plan')
        index = planned['attempt_seeds'].index(seeds)
        if index in consumed:
            raise ValueError('duplicate previous attempt')
        marker = directory/'execution.json'
        if marker.exists():
            execution = json.loads(marker.read_text())
            if execution['seeds'] != seeds or execution['attempt'] != index + 1:
                raise ValueError('execution marker differs from planned attempt')
        elif not (directory/'fmriprep.log').is_file():
            # The first pilot predates execution markers; its app log survives.
            raise ValueError('previous attempt never reached app execution')
        after = directory/'after.json'
        if after.exists() and json.loads(after.read_text())['seeds'] != seeds:
            raise ValueError('previous measured seed differs from provenance')
        consumed[index] = {'attempt': index + 1, 'seeds': seeds,
                           'provenance': str(provenance_path), 'sha256': sha256(provenance_path)}
    if set(consumed) != set(range(len(consumed))):
        raise ValueError('previous attempts must be a contiguous prefix of the plan')
    if len(consumed) >= 3 and not allow_exhausted:
        raise ValueError('all three planned attempts have been consumed')
    return [consumed[i] for i in sorted(consumed)]


def extend_plan(parent_path, attempts, additional_attempts):
    """Freeze an explicitly authorized, bounded retry extension without changing its parent."""
    parent = json.loads(parent_path.read_text())
    if 'retry_extension' in parent or not 1 <= additional_attempts <= 10 or not attempts:
        raise ValueError('one extension of one through ten additional attempts is supported')
    by_run = {entry['before']['run']: entry for entry in parent['runs']}
    if not set(attempts) <= by_run.keys():
        raise ValueError('retry extension contains an unselected acquisition')
    digest = sha256(parent_path)
    histories = {}
    for run, directories in attempts.items():
        paths = [Path(path) for path in directories]
        history = consumed_attempts(by_run[run], paths, digest, allow_exhausted=True)
        if len(history) != 3 or len(by_run[run]['attempt_seeds']) != 3:
            raise ValueError('retry extension requires all three original attempts')
        for directory in paths:
            receipt = json.loads((directory/'receipt.json').read_text())
            after = json.loads((directory/'after.json').read_text())
            proof = json.loads((directory/'provenance.json').read_text())
            if (receipt['automatic_checks_passed'] or receipt['seeds'] != proof['seeds']
                    or after['seeds'] != proof['seeds']
                    or receipt['after_sha256'] != sha256(directory/'after.json')):
                raise ValueError('retry extension requires measured unsuccessful attempts')
        histories[run] = history
    result = copy.deepcopy(parent)
    used = [entry['before']['seeds'] for entry in parent['runs']]
    used += [seed for entry in parent['runs'] for seed in entry['attempt_seeds']]
    for entry in result['runs']:
        if entry['before']['run'] in histories:
            for _ in range(additional_attempts):
                seeds = fresh_seeds(used)
                used.append(seeds)
                entry['attempt_seeds'].append(seeds)
    result['retry_extension'] = {'parent_plan': str(parent_path.resolve()),
        'parent_sha256': digest, 'additional_attempts': additional_attempts, 'runs': histories}
    return result


def load_plan(path):
    """Verify retry lineage, unchanged scope, consumed evidence and fresh seeds."""
    plan = json.loads(path.read_text())
    extension = plan.get('retry_extension')
    if extension is None:
        return plan
    parent_path = Path(extension['parent_plan'])
    if sha256(parent_path) != extension['parent_sha256']:
        raise ValueError('retry parent plan checksum changed')
    parent = json.loads(parent_path.read_text())
    count = extension['additional_attempts']
    if 'retry_extension' in parent or not isinstance(count, int) or not 1 <= count <= 10:
        raise ValueError('invalid retry bound or nested extension')
    original = copy.deepcopy(plan)
    original.pop('retry_extension')
    by_run = {entry['before']['run']: entry for entry in parent['runs']}
    if not extension['runs'] or not set(extension['runs']) <= by_run.keys():
        raise ValueError('retry extension contains an unselected acquisition')
    used = [entry['before']['seeds'] for entry in parent['runs']]
    used += [seed for entry in parent['runs'] for seed in entry['attempt_seeds']]
    for entry in original['runs']:
        run = entry['before']['run']
        if run not in extension['runs']:
            continue
        history = extension['runs'][run]
        directories = [Path(item['provenance']).parent for item in history]
        verified = consumed_attempts(by_run[run], directories, extension['parent_sha256'], allow_exhausted=True)
        if history != verified or len(history) != 3 or len(entry['attempt_seeds']) != 3 + count:
            raise ValueError('retry consumed history or attempt count changed')
        for seeds in entry['attempt_seeds'][3:]:
            if (seeds != derived_seeds(seeds['master'])
                    or any(seeds['master'] == old['master'] or seeds['ants'] == old['ants'] for old in used)):
                raise ValueError('retry seed is invalid or reused')
            used.append(seeds)
        entry['attempt_seeds'] = entry['attempt_seeds'][:3]
    if original != parent:
        raise ValueError('retry extension changed original scope or evidence')
    return plan


def scheduled_attempts(plan, planned, digest, previous, number=None):
    extension = plan.get('retry_extension', {})
    history = extension.get('runs', {}).get(planned['before']['run'])
    if history is not None:
        if previous or number is None or not 4 <= number <= len(planned['attempt_seeds']):
            raise ValueError('extended retries require one explicit attempt number and no previous-attempt arguments')
        return history, [(number, planned['attempt_seeds'][number - 1])]
    if number is not None:
        raise ValueError('explicit attempt numbers require a verified retry extension')
    history = consumed_attempts(planned, previous, digest)
    return history, list(enumerate(planned['attempt_seeds'][len(history):], len(history) + 1))


def inventory_difference(before, after):
    return {
        'added': {key: value for key, value in after.items() if key not in before},
        'removed': {key: value for key, value in before.items() if key not in after},
        'changed': {key: {'before': value, 'after': after[key]}
                    for key, value in before.items() if key in after and value != after[key]},
    }


def validate_surface_derivations(difference, surfaces, subject):
    """Allow only the pinned sMRIPrep workflow's new native midthickness cache."""
    allowed = {'surf/lh.midthickness', 'surf/rh.midthickness'}
    if difference['changed'] or difference['removed'] or set(difference['added']) - allowed:
        raise ValueError('original FreeSurfer files changed or unexpected files were added')
    import nibabel as nib
    import numpy as np

    geometry = {}
    for relative, digest in difference['added'].items():
        path = surfaces/f'sub-{subject}'/relative
        vertices, faces = nib.freesurfer.read_geometry(path)
        white, white_faces = nib.freesurfer.read_geometry(path.with_suffix('.white'))
        if (vertices.shape != white.shape or not np.isfinite(vertices).all()
                or not np.array_equal(faces, white_faces)):
            raise ValueError('derived midthickness geometry differs from original white topology')
        geometry[relative] = {'sha256': digest, 'vertices': len(vertices),
                              'triangles': len(faces), 'white_topology_unchanged': True}
    return geometry


def surface_change_report(difference, surfaces, subject):
    """Preserve exact staged differences without blocking derivative generation."""
    report = {'difference': difference, 'warning': None, 'derived_geometry': {}}
    if any(difference.values()):
        report['warning'] = 'Node-local FreeSurfer files changed; shared originals remain read-only.'
    try:
        report['derived_geometry'] = validate_surface_derivations(difference, surfaces, subject)
        report['expected_midthickness_derivation_only'] = True
    except (ValueError, OSError) as error:
        report['expected_midthickness_derivation_only'] = False
        report['warning'] = str(error)
    return report


def execute_plan(args):
    """Run one planned acquisition; preserve evidence and never promote results."""
    from network_fmri.bold_reference_qc import _run_key
    from network_fmri.surface_evidence import extract_subject_archive
    from network_fmri.surface_inventory import reconstruction_inventory

    if not os.environ.get('SLURM_JOB_ID') or not os.environ.get('L_SCRATCH_JOB'):
        raise RuntimeError('repairs require Slurm and node-local work storage')
    plan = load_plan(args.plan)
    planned = [r for r in plan['runs'] if r['before']['run'] == args.run]
    if len(planned) != 1:
        raise ValueError('requested acquisition is not uniquely selected in the reviewed plan')
    plan_digest = sha256(args.plan)
    history, scheduled = scheduled_attempts(plan, planned[0], plan_digest,
        args.previous_attempt, getattr(args, 'attempt_number', None))
    row = planned[0]['before']
    subject = row['subject']
    if run_filter(args.run)['bold']['subject'] != subject:
        raise ValueError('run identity and subject disagree')
    raw = Path(plan['raw_root'])
    stage = isolated_stage(args.stage, [raw, plan['archive_root'], args.surface_root]) / args.run
    stage.mkdir(parents=True, exist_ok=False)
    if sha256(args.image) != args.image_sha256:
        raise ValueError('container checksum differs from the audited image')
    surface_archive, surface_proof = check_sources(plan, args.surface_root, subject)
    if not Path(row['archive']).is_symlink() or Path(os.readlink(row['archive'])).name != row['annex_key']:
        raise ValueError('original fMRIPrep archive identity changed')
    bold_files = sorted(str(p) for p in (raw/f'sub-{subject}').glob('ses-*/func/*_bold.nii.gz')
                        if _run_key(p) == args.run)
    if not bold_files:
        raise ValueError('no raw echoes for requested acquisition')
    previous = [row['seeds'], *[item['seeds'] for item in history]]
    receipts = []
    for number, seeds in scheduled:
        if number > 3 and 'retry_extension' not in plan:
            raise ValueError('bounded maximum is three attempts')
        attempt = stage / f'attempt-{number}'
        attempt.mkdir()
        with tempfile.TemporaryDirectory(prefix='seed-repair-', dir=os.environ['L_SCRATCH_JOB']) as directory:
            local = Path(directory)
            anatomy, surfaces = local/'anatomy', local/'subjects'
            anatomy_evidence = extract_anatomy(row['archive'], subject, anatomy)
            surface_hashes = extract_subject_archive(surface_archive, subject, surfaces)
            (attempt/'surface-before.json').write_text(json.dumps(surface_hashes, indent=2)+'\n')
            filter_file = local/'filter.json'
            filter_file.write_text(json.dumps(run_filter(args.run), indent=2))
            spec = {'run': args.run, 'subject': subject, 'seeds': seeds, 'before': row,
                    'anatomy': str(anatomy), 'anatomy_evidence': anatomy_evidence,
                    'bold_files': bold_files, 'surface_proof': surface_proof,
                    'output': str(local/'output'), 'work': str(local/'work'),
                    'evidence': str(local/'evidence')}
            options = dict(raw=raw, output=local/'output', work=local/'work', anatomy=anatomy,
                           surfaces=surfaces, license_file=args.license_file, filter_file=filter_file,
                           run=args.run, seeds=seeds, previous=previous)
            spec['command'] = build_command(**options)
            preview = dict(spec, command=build_command(**dict(options,
                           output=local/'preview-output', work=local/'preview-work')))
            container = ['apptainer', 'exec', '--cleanenv', '--containall',
                '-B', os.environ['OAK'] + ':' + os.environ['OAK'] + ':ro',
                '-B', str(args.code) + ':/code:ro', '-B', str(local) + ':' + str(local),
                '-B', str(local) + ':/tmp', '-B', str(attempt) + ':' + str(attempt),
                '-B', str(args.license_file) + ':' + str(args.license_file) + ':ro',
                '-B', str(args.templateflow) + ':/templateflow:ro',
                '--env', 'PYTHONPATH=/code/src,TEMPLATEFLOW_HOME=/templateflow,SLURM_JOB_ID=' +
                os.environ['SLURM_JOB_ID'] + ',OMP_NUM_THREADS=2,OPENBLAS_NUM_THREADS=1', str(args.image)]
            preview_file = local/'preflight-spec.json'
            preview_file.write_text(json.dumps(preview, indent=2))
            spec_file = local/'spec.json'
            spec_file.write_text(json.dumps(spec, indent=2))
            receipt = dict(spec, plan_sha256=plan_digest, image_sha256=args.image_sha256,
                           previous_attempts=history,
                           slurm_job_id=os.environ['SLURM_JOB_ID'],
                           driver_sha256=sha256(__file__),
                           workflow_helper_sha256=sha256(args.code/'src/network_fmri/fmriprep_seed_workflow.py'))
            (attempt/'provenance.json').write_text(json.dumps(receipt, indent=2)+'\n')
            with (attempt/'preflight.log').open('x') as log:
                subprocess.run(container + ['python', '-m', 'network_fmri.fmriprep_seed_workflow',
                    'preflight', str(preview_file), str(attempt/'preflight.json')],
                    stdout=log, stderr=subprocess.STDOUT, check=True)
            if args.preflight_only:
                return
            execution = {'attempt': number, 'seeds': seeds, 'status': 'started'}
            (attempt/'execution.json').write_text(json.dumps(execution, indent=2)+'\n')
            with (attempt/'fmriprep.log').open('x') as log:
                completed = subprocess.run(container + spec['command'], stdout=log, stderr=subprocess.STDOUT)
            execution.update(status='returned', returncode=completed.returncode)
            (attempt/'execution.json').write_text(json.dumps(execution, indent=2)+'\n')
            bundle_sha256 = archive_attempt_output(local/'output', attempt)
            surface_after = reconstruction_inventory(surfaces, subject)
            surface_diff = inventory_difference(surface_hashes, surface_after)
            (attempt/'surface-after.json').write_text(json.dumps(surface_after, indent=2)+'\n')
            (attempt/'surface-diff.json').write_text(json.dumps(surface_diff, indent=2)+'\n')
            surface_report = surface_change_report(surface_diff, surfaces, subject)
            receipt['surface_integrity'] = surface_report
            (attempt/'provenance.json').write_text(json.dumps(receipt, indent=2)+'\n')
            (attempt/'surface-derivations.json').write_text(json.dumps(surface_report, indent=2)+'\n')
            if completed.returncode:
                (attempt/'failure.json').write_text(json.dumps({
                    'phase': 'fmriprep', 'returncode': completed.returncode,
                    'seeds': seeds, 'derivatives_sha256': bundle_sha256,
                    'status': 'unvalidated', 'requires_diagnosis': True}, indent=2)+'\n')
                completed.check_returncode()
            with (attempt/'measurement.log').open('x') as log:
                measured = subprocess.run(container + ['python', '-m', 'network_fmri.fmriprep_seed_workflow',
                    'measure', str(spec_file), str(attempt/'after.json')],
                    stdout=log, stderr=subprocess.STDOUT)
            if (local/'evidence').is_dir():
                shutil.copytree(local/'evidence', attempt/'evidence')
            measured.check_returncode()
            for item in anatomy_evidence:
                if sha256(anatomy/item['path']) != item['sha256']:
                    raise ValueError('anatomical reuse inputs changed during repair')
            after = json.loads((attempt/'after.json').read_text())
            accepted = (after['status'] == 'pass' and
                        after['intensities']['coreg']['relative_median'] is not None and
                        after['intensities']['coreg']['relative_median'] >= ARCHIVED_RELATIVE_MEDIAN_MIN)
            record = {'attempt': number, 'seeds': seeds, 'status': after['status'],
                      'automatic_checks_passed': accepted,
                      'derivatives_sha256': bundle_sha256, 'after_sha256': sha256(attempt/'after.json'),
                      'surface_integrity_warning': surface_report['warning'],
                      'surface_changes_expected': surface_report['expected_midthickness_derivation_only'],
                      'requires_visual_review': True}
            receipts.append(record)
            (attempt/'receipt.json').write_text(json.dumps(record, indent=2)+'\n')
            (stage/'attempts.json').write_text(json.dumps(receipts, indent=2)+'\n')
            previous.append(seeds)
            if accepted:
                return
    raise RuntimeError('no passing result in scheduled fresh seed attempts; retain staged evidence')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    plan_parser = commands.add_parser('plan')
    for key in ('summary', 'reviews', 'inventory', 'output'):
        plan_parser.add_argument('--'+key, type=Path, required=True)
    extend_parser = commands.add_parser('extend')
    for key in ('plan', 'attempts', 'output'):
        extend_parser.add_argument('--'+key, type=Path, required=True)
    extend_parser.add_argument('--additional-attempts', type=int, required=True)
    run_parser = commands.add_parser('run')
    for key in ('plan', 'stage', 'surface-root', 'image', 'license-file', 'templateflow', 'code'):
        run_parser.add_argument('--'+key, type=Path, required=True)
    run_parser.add_argument('--run', required=True)
    run_parser.add_argument('--image-sha256', required=True)
    run_parser.add_argument('--preflight-only', action='store_true')
    run_parser.add_argument('--attempt-number', type=int,
                            help='Execute exactly one additional seed from a verified retry extension')
    run_parser.add_argument('--previous-attempt', type=Path, action='append', default=[],
                            help='Executed attempt directory from this same frozen plan; repeat for each prior attempt')
    args = parser.parse_args(argv)
    if args.command == 'plan':
        plan = make_plan(*[json.loads(getattr(args, key).read_text()) for key in ('summary', 'reviews', 'inventory')])
        with args.output.open('x') as stream:
            json.dump(plan, stream, indent=2)
    elif args.command == 'extend':
        plan = extend_plan(args.plan, json.loads(args.attempts.read_text()), args.additional_attempts)
        with args.output.open('x') as stream:
            json.dump(plan, stream, indent=2)
    else:
        execute_plan(args)


if __name__ == '__main__':
    main()
