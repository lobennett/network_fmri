"""Keep seed repairs scoped to one acquisition and the existing anatomy."""

import pytest
import zipfile
from pathlib import Path

from network_fmri.fmriprep_seed_rerun import derived_seeds, extract_anatomy, fresh_seeds, run_filter, build_command


def test_matches_archived_seed_derivation():
    assert derived_seeds(12345) == {'master': 12345, 'ants': 54608, 'numpy': 1333}


def test_fresh_seed_changes_master_and_ants():
    old = derived_seeds(12345)
    new = fresh_seeds([old])
    assert new['master'] != old['master']
    assert new['ants'] != old['ants']
    assert derived_seeds(new['master']) == new


def test_run_filter_selects_all_echoes_without_filtering_anatomy():
    assert run_filter('sub-s29_ses-07_task-shapeMatching_run-1') == {
        'bold': {'subject': 's29', 'session': '07', 'task': 'shapeMatching', 'run': '1'}
    }


@pytest.mark.parametrize('key', ['sub-s29_task-rest_run-*', '../sub-s29_task-rest_run-1',
                               'sub-s29_task-rest_run-1_echo-1', 'sub-s29_task-rest_run-1_run-2'])
def test_rejects_ambiguous_or_single_echo_filter(key):
    with pytest.raises(ValueError):
        run_filter(key)


def test_anatomy_reuse_never_exposes_functional_cache(tmp_path):
    archive = tmp_path / 'source.zip'
    anat = 'sub-s29/ses-04/anat/sub-s29_ses-04_desc-preproc_T1w.nii.gz'
    with zipfile.ZipFile(archive, 'w') as source:
        source.writestr('fMRIPrep/dataset_description.json', '{}')
        source.writestr('fMRIPrep/' + anat, b'anatomy')
        source.writestr('fMRIPrep/sub-s29/ses-07/func/sub-s29_ses-07_task-rest_run-1_desc-coreg_boldref.nii.gz', b'bad')
        source.writestr('fMRIPrep/sub-s29/log/old/fmriprep.toml', b'old seeds')
    target = tmp_path / 'anat'
    evidence = extract_anatomy(archive, 's29', target)
    assert (target / anat).read_bytes() == b'anatomy'
    assert {r['path'] for r in evidence} == {'dataset_description.json', anat}
    assert not list(target.glob('**/func'))
    assert not list(target.glob('**/fmriprep.toml'))
    with pytest.raises(FileExistsError):
        extract_anatomy(archive, 's29', target)


@pytest.mark.parametrize('member', ['fMRIPrep/sub-s29/ses-04/anat/../../../escape',
                                  'fMRIPrep/sub-s03/anat/sub-s03_T1w.nii.gz'])
def test_unsafe_anatomy_archive_does_not_write_target(tmp_path, member):
    archive = tmp_path / 'source.zip'
    with zipfile.ZipFile(archive, 'w') as source:
        source.writestr(member, b'data')
    with pytest.raises(ValueError):
        extract_anatomy(archive, 's29', tmp_path / 'anat')
    assert not (tmp_path / 'anat').exists()


def command_options(tmp_path):
    anatomy = tmp_path / 'anatomy'
    anatomy.mkdir()
    return dict(raw=tmp_path / 'raw', output=tmp_path / 'output', work=tmp_path / 'work',
                anatomy=anatomy, surfaces=tmp_path / 'subjects', license_file=tmp_path / 'license.txt',
                filter_file=tmp_path / 'filter.json', run='sub-s29_ses-07_task-shapeMatching_run-1',
                seeds=derived_seeds(54321), previous=[derived_seeds(12345)])


def test_command_preserves_campaign_settings_and_supplies_fresh_seed(tmp_path):
    options = command_options(tmp_path)
    command = build_command(**options)
    assert command[:4] == ['fmriprep', str(options['raw']), str(options['output']), 'participant']
    assert command[command.index('--random-seed') + 1] == '54321'
    assert command[command.index('--derivatives') + 1] == 'reuse=' + str(options['anatomy'])
    assert command[command.index('--bids-filter-file') + 1] == str(options['filter_file'])
    assert command[command.index('--dummy-scans') + 1] == '0'
    assert command[command.index('--cifti-output') + 1] == '91k'
    assert '--project-goodvoxels' in command
    assert '--fs-no-resume' in command
    assert '--config-file' not in command


@pytest.mark.parametrize('existing', ['output', 'work'])
def test_command_rejects_stale_output_or_work(tmp_path, existing):
    options = command_options(tmp_path)
    options[existing].mkdir()
    with pytest.raises(ValueError, match='fresh'):
        build_command(**options)


def test_command_rejects_old_seed_and_functional_cache(tmp_path):
    options = command_options(tmp_path)
    options['seeds'] = options['previous'][0]
    with pytest.raises(ValueError, match='seed'):
        build_command(**options)
    options['seeds'] = derived_seeds(54321)
    (options['anatomy'] / 'func').mkdir()
    with pytest.raises(ValueError, match='functional'):
        build_command(**options)


def audit_selection_fixture():
    rows = [{'run': f'sub-s29_ses-07_task-test_run-{i}', 'status': 'pass',
             'intensities': {label: {'relative_median': .8} for label in ('coreg', 'corrected')}}
            for i in range(1, 21)]
    rows[0]['status'] = 'flagged'
    rows[1]['status'] = 'borderline'
    reviews = {
        rows[0]['run']: {'decision': 'rerun', 'reason': 'Weighting map misses brain; central darkening.'},
        rows[1]['run']: {'decision': 'keep', 'reason': 'Partial FOV; uniform reference intensity.'},
    }
    return {'audit_complete': True, 'stop_before_reruns': False, 'runs': rows}, reviews


def test_only_visually_confirmed_failures_are_selected():
    from network_fmri.fmriprep_seed_rerun import select_affected
    summary, reviews = audit_selection_fixture()
    selected = select_affected(summary, reviews)
    assert [row['run'] for row in selected] == [summary['runs'][0]['run']]
    assert selected[0]['review'] == reviews[selected[0]['run']]


def test_unreviewed_borderline_blocks_selection():
    from network_fmri.fmriprep_seed_rerun import select_affected
    summary, reviews = audit_selection_fixture()
    del reviews[summary['runs'][1]['run']]
    with pytest.raises(ValueError, match='visual review'):
        select_affected(summary, reviews)


def test_selection_recomputes_ten_percent_gate():
    from network_fmri.fmriprep_seed_rerun import select_affected
    summary, reviews = audit_selection_fixture()
    for row in summary['runs'][1:3]:
        row['status'] = 'flagged'
        reviews[row['run']] = {'decision': 'rerun', 'reason': 'Severe misalignment.'}
    with pytest.raises(ValueError, match='10%'):
        select_affected(summary, reviews)


@pytest.mark.parametrize('problem', ['unfinished', 'duplicate', 'healthy', 'unresolved'])
def test_selection_rejects_incomplete_or_inconsistent_review(problem):
    from network_fmri.fmriprep_seed_rerun import select_affected
    summary, reviews = audit_selection_fixture()
    if problem == 'unfinished':
        summary['runs'][-1]['status'] = 'not-audited'
    elif problem == 'duplicate':
        summary['runs'].append(summary['runs'][0])
    elif problem == 'healthy':
        reviews[summary['runs'][-1]['run']] = {'decision': 'rerun', 'reason': 'Unrelated finding.'}
    else:
        reviews[summary['runs'][0]['run']]['decision'] = 'unresolved'
    with pytest.raises(ValueError):
        select_affected(summary, reviews)


def anatomical_cache_fixture(tmp_path):
    import nibabel as nib
    import numpy as np
    from network_fmri.bold_reference_qc import sha256

    root = tmp_path / 'anatomy'
    root.mkdir()
    cache, evidence = {}, []

    def add(name, image=False):
        path = root / name
        if image:
            nib.save(nib.Nifti1Image(np.ones((3, 4, 5), dtype=np.float32), np.eye(4)), path)
        else:
            path.write_text(name)
        evidence.append({'path': name, 'sha256': sha256(path)})
        return str(path)

    for key, suffix in [('preproc', 'desc-preproc_T1w'), ('mask', 'desc-brain_mask'), ('dseg', 'dseg')]:
        cache['t1w_' + key] = add('sub-s29_' + suffix + '.nii.gz', image=True)
    cache['t1w_tpms'] = [add(f'sub-s29_label-{label}_probseg.nii.gz', image=True)
                         for label in ('GM', 'WM', 'CSF')]
    cache['transforms'] = {
        space: {direction: add(f'sub-s29_{space}_{direction}_xfm.h5')
                for direction in ('forward', 'reverse')}
        for space in ('MNI152NLin2009cAsym', 'MNI152NLin6Asym', 'fsnative')
    }
    for key in ('white', 'pial', 'midthickness', 'sphere', 'sphere_reg', 'thickness', 'sulc',
                'sphere_reg_fsLR', 'sphere_reg_msm', 'cortex_mask'):
        cache[key] = [add(f'sub-s29_hemi-{hemi}_{key}.gii') for hemi in ('L', 'R')]
    return cache, root, evidence


def test_complete_original_anatomy_can_be_reused(tmp_path):
    from network_fmri.fmriprep_seed_rerun import validate_anatomical_cache
    cache, root, evidence = anatomical_cache_fixture(tmp_path)
    checked = validate_anatomical_cache(cache, root, evidence)
    assert checked['t1w_preproc'] == cache['t1w_preproc']
    assert checked['transforms'] == cache['transforms']


@pytest.mark.parametrize('problem', ['template', 'mask', 'duplicate', 'tissue_order', 'surface', 'changed', 'outside'])
def test_anatomy_reuse_rejects_incomplete_or_changed_coordinate_inputs(tmp_path, problem):
    from network_fmri.fmriprep_seed_rerun import validate_anatomical_cache
    from pathlib import Path
    cache, root, evidence = anatomical_cache_fixture(tmp_path)
    if problem == 'template':
        del cache['transforms']['MNI152NLin6Asym']['reverse']
    elif problem == 'mask':
        del cache['t1w_mask']
    elif problem == 'duplicate':
        cache['t1w_preproc'] = [cache['t1w_preproc']] * 2
    elif problem == 'tissue_order':
        cache['t1w_tpms'].reverse()
    elif problem == 'surface':
        cache['white'] = [cache['white'][0]] * 2
    elif problem == 'changed':
        Path(cache['transforms']['fsnative']['reverse']).write_text('recomputed')
    else:
        outside = tmp_path / 'unverified.h5'
        outside.write_text('different anatomy')
        cache['transforms']['fsnative']['reverse'] = str(outside)
    with pytest.raises(ValueError):
        validate_anatomical_cache(cache, root, evidence)


def test_passing_replay_requires_review_when_archived_intensity_disagrees():
    from network_fmri.fmriprep_seed_rerun import select_affected
    summary, reviews = audit_selection_fixture()
    row = summary['runs'][-1]
    row['intensities']['coreg']['relative_median'] = .5
    with pytest.raises(ValueError, match='visual review'):
        select_affected(summary, reviews)
    reviews[row['run']] = {'decision': 'rerun', 'reason': 'Archived coreg has central darkening.'}
    assert len(select_affected(summary, reviews)) == 2


def test_native_registered_sphere_can_be_converted_without_new_registration(tmp_path):
    from network_fmri.fmriprep_seed_rerun import validate_anatomical_cache
    cache, root, evidence = anatomical_cache_fixture(tmp_path)
    del cache['sphere_reg']
    assert validate_anatomical_cache(cache, root, evidence) is cache


def plan_fixture():
    import copy
    summary, reviews = audit_selection_fixture()
    summary.update(archive_commit='archive-revision', raw_commit='raw-revision', assessed=20)
    for row in summary['runs']:
        row.update(subject='s29', archive='/archive/sub-s29.zip', annex_key='key',
                   config_sha256='digest', seeds=derived_seeds(12345), members={'hmc': 'reference'})
    inventory = copy.deepcopy(summary)
    inventory.update(raw_root='/raw', archive_root='/archive')
    for row in inventory['runs']:
        row['status'] = 'ready'
    return summary, reviews, inventory


def test_plan_contains_only_selected_acquisitions_and_bounded_distinct_seeds():
    from network_fmri.fmriprep_seed_rerun import make_plan
    plan = make_plan(*plan_fixture())
    assert len(plan['runs']) == 1
    seeds = plan['runs'][0]['attempt_seeds']
    assert len(seeds) == len({s['master'] for s in seeds}) == len({s['ants'] for s in seeds}) == 3
    assert all(s['master'] != 12345 and s['ants'] != 54608 for s in seeds)
    assert plan['archive_commit'] == 'archive-revision'


@pytest.mark.parametrize('field', ['archive_commit', 'raw_commit', 'annex_key', 'config_sha256'])
def test_plan_rejects_changed_source_evidence(field):
    from network_fmri.fmriprep_seed_rerun import make_plan
    summary, reviews, inventory = plan_fixture()
    if field.endswith('commit'):
        inventory[field] = 'changed'
    else:
        inventory['runs'][0][field] = 'changed'
    with pytest.raises(ValueError, match='differ'):
        make_plan(summary, reviews, inventory)


@pytest.mark.parametrize('location', ['source', 'source/repair', '.'])
def test_staging_cannot_contain_or_live_inside_sources(tmp_path, location):
    from network_fmri.fmriprep_seed_rerun import isolated_stage
    with pytest.raises(ValueError, match='separate'):
        isolated_stage(tmp_path/location, [tmp_path/'source'])


def test_staging_rejects_symlink_into_source(tmp_path):
    from network_fmri.fmriprep_seed_rerun import isolated_stage
    (tmp_path/'alias').symlink_to(tmp_path/'source', target_is_directory=True)
    with pytest.raises(ValueError):
        isolated_stage(tmp_path/'alias'/'repair', [tmp_path/'source'])
    assert isolated_stage(tmp_path/'stage', [tmp_path/'source']) == tmp_path/'stage'


def test_intensity_flags_count_toward_campaign_stop_gate():
    from network_fmri.fmriprep_seed_rerun import select_affected
    summary, reviews = audit_selection_fixture()
    for row in summary['runs'][-2:]:
        row['intensities']['coreg']['relative_median'] = .654
        reviews[row['run']] = {'decision': 'rerun', 'reason': 'Archived intensity damage.'}
    with pytest.raises(ValueError, match='10%'):
        select_affected(summary, reviews)


def test_partial_derivatives_are_retained_without_claiming_qc_success(tmp_path):
    import tarfile
    from network_fmri.bold_reference_qc import sha256
    from network_fmri.fmriprep_seed_rerun import archive_attempt_output
    output, attempt = tmp_path/'output', tmp_path/'attempt'
    output.mkdir()
    attempt.mkdir()
    (output/'completed-reference.nii.gz').write_bytes(b'completed intermediate')
    digest = archive_attempt_output(output, attempt)
    assert digest == sha256(attempt/'derivatives.tar.gz')
    with tarfile.open(attempt/'derivatives.tar.gz') as archive:
        assert archive.extractfile('fmriprep/completed-reference.nii.gz').read() == b'completed intermediate'
    assert not (attempt/'receipt.json').exists()
    with pytest.raises(FileExistsError):
        archive_attempt_output(output, attempt)
    assert archive_attempt_output(tmp_path/'missing', attempt) is None


def consumed_fixture(tmp_path, number=1):
    import json
    entry = {'before': {'run': 'sub-s29_ses-07_task-shapeMatching_run-1'},
             'attempt_seeds': [derived_seeds(seed) for seed in (9620, 16426, 1953)]}
    directory = tmp_path/f'attempt-{number}'
    directory.mkdir()
    proof = {'run': entry['before']['run'], 'seeds': entry['attempt_seeds'][number-1],
             'plan_sha256': 'frozen-plan'}
    (directory/'provenance.json').write_text(json.dumps(proof))
    (directory/'fmriprep.log').write_text('fMRIPrep finished successfully!')
    return entry, directory, proof


def test_executed_pilot_seed_is_consumed_even_without_a_success_receipt(tmp_path):
    from network_fmri.fmriprep_seed_rerun import consumed_attempts
    entry, directory, _ = consumed_fixture(tmp_path)
    history = consumed_attempts(entry, [directory], 'frozen-plan')
    assert [row['attempt'] for row in history] == [1]
    assert history[0]['seeds']['master'] == 9620
    assert entry['attempt_seeds'][len(history)]['master'] == 16426


@pytest.mark.parametrize('problem', ['run', 'plan', 'seed', 'duplicate', 'never_started', 'measured_seed', 'marker'])
def test_resuming_rejects_unrelated_or_inconsistent_execution_proof(tmp_path, problem):
    import json
    from network_fmri.fmriprep_seed_rerun import consumed_attempts
    entry, directory, proof = consumed_fixture(tmp_path)
    directories = [directory]
    if problem == 'run':
        proof['run'] = 'another-run'
    elif problem == 'plan':
        proof['plan_sha256'] = 'different-plan'
    elif problem == 'seed':
        proof['seeds'] = derived_seeds(12345)
    elif problem == 'duplicate':
        directories.append(directory)
    elif problem == 'never_started':
        (directory/'fmriprep.log').unlink()
    elif problem == 'measured_seed':
        (directory/'after.json').write_text(json.dumps({'seeds': derived_seeds(12345)}))
    else:
        (directory/'execution.json').write_text(json.dumps({'attempt': 2, 'seeds': proof['seeds']}))
    (directory/'provenance.json').write_text(json.dumps(proof))
    with pytest.raises(ValueError):
        consumed_attempts(entry, directories, 'frozen-plan')


def test_resuming_requires_all_prior_attempts_and_enforces_total_bound(tmp_path):
    from network_fmri.fmriprep_seed_rerun import consumed_attempts
    directories = []
    for number in (1, 2, 3):
        entry, directory, _ = consumed_fixture(tmp_path, number)
        directories.append(directory)
    with pytest.raises(ValueError, match='prefix'):
        consumed_attempts(entry, directories[1:2], 'frozen-plan')
    assert len(consumed_attempts(entry, directories[:2], 'frozen-plan')) == 2
    with pytest.raises(ValueError, match='three'):
        consumed_attempts(entry, directories, 'frozen-plan')


def retry_fixture(tmp_path):
    import json
    from network_fmri.bold_reference_qc import sha256
    entry = {'before': {'run': 'sub-s29_ses-07_task-shapeMatching_run-1',
                         'seeds': derived_seeds(12345)},
             'attempt_seeds': [derived_seeds(seed) for seed in (9620, 16426, 1953)]}
    parent = tmp_path/'parent.json'
    parent.write_text(json.dumps({'runs': [entry], 'archive_commit': 'unchanged'}))
    directories = []
    for number, seeds in enumerate(entry['attempt_seeds'], 1):
        directory = tmp_path/f'attempt-{number}'
        directory.mkdir()
        (directory/'provenance.json').write_text(json.dumps({'run': entry['before']['run'],
            'seeds': seeds, 'plan_sha256': sha256(parent)}))
        (directory/'execution.json').write_text(json.dumps({'attempt': number, 'seeds': seeds}))
        (directory/'after.json').write_text(json.dumps({'seeds': seeds}))
        (directory/'receipt.json').write_text(json.dumps({'seeds': seeds,
            'automatic_checks_passed': False, 'after_sha256': sha256(directory/'after.json')}))
        directories.append(str(directory))
    return parent, {entry['before']['run']: directories}


def test_retry_extension_preserves_parent_and_schedules_only_one_fresh_seed(tmp_path):
    import json
    from network_fmri.fmriprep_seed_rerun import extend_plan, load_plan, scheduled_attempts
    parent, attempts = retry_fixture(tmp_path)
    original = parent.read_bytes()
    extended = extend_plan(parent, attempts, 10)
    path = tmp_path/'extended.json'
    path.write_text(json.dumps(extended))
    assert load_plan(path) == extended
    entry = extended['runs'][0]
    assert len(entry['attempt_seeds']) == 13
    assert len({seed['ants'] for seed in entry['attempt_seeds']}) == 13
    history, scheduled = scheduled_attempts(extended, entry, 'new-digest', [], 7)
    assert len(history) == 3
    assert scheduled == [(7, entry['attempt_seeds'][6])]
    assert parent.read_bytes() == original
    for number in (None, 3, 14):
        with pytest.raises(ValueError):
            scheduled_attempts(extended, entry, 'new-digest', [], number)


@pytest.mark.parametrize('problem', ['source', 'scope', 'seed', 'bound', 'history', 'parent'])
def test_retry_extension_rejects_changed_scope_history_or_seed_reuse(tmp_path, problem):
    import json
    from network_fmri.fmriprep_seed_rerun import extend_plan, load_plan
    parent, attempts = retry_fixture(tmp_path)
    extended = extend_plan(parent, attempts, 10)
    if problem == 'source':
        extended['archive_commit'] = 'changed'
    elif problem == 'scope':
        extended['runs'][0]['before']['seeds'] = derived_seeds(1)
    elif problem == 'seed':
        extended['runs'][0]['attempt_seeds'][3] = extended['runs'][0]['attempt_seeds'][0]
    elif problem == 'bound':
        extended['retry_extension']['additional_attempts'] = 11
    elif problem == 'history':
        extended['retry_extension']['runs'][next(iter(attempts))][0]['sha256'] = 'changed'
    else:
        parent.write_text(parent.read_text()+'\n')
    path = tmp_path/'extended.json'
    path.write_text(json.dumps(extended))
    with pytest.raises(ValueError):
        load_plan(path)


def test_retry_extension_requires_exhausted_unsuccessful_original_attempts(tmp_path):
    import json
    from network_fmri.fmriprep_seed_rerun import extend_plan
    parent, attempts = retry_fixture(tmp_path)
    run = next(iter(attempts))
    with pytest.raises(ValueError, match='three'):
        extend_plan(parent, {run: attempts[run][:2]}, 10)
    receipt = Path(attempts[run][0])/'receipt.json'
    value = json.loads(receipt.read_text())
    value['automatic_checks_passed'] = True
    receipt.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='unsuccessful'):
        extend_plan(parent, attempts, 10)


def test_surface_inventory_diff_distinguishes_added_cache_from_changed_inputs():
    from network_fmri.fmriprep_seed_rerun import inventory_difference
    before = {'surf/lh.white': 'original-white', 'mri/brain.mgz': 'original-brain'}
    after = {'surf/lh.white': 'changed-white', 'surf/lh.midthickness': 'new-cache'}
    assert inventory_difference(before, after) == {
        'added': {'surf/lh.midthickness': 'new-cache'},
        'removed': {'mri/brain.mgz': 'original-brain'},
        'changed': {'surf/lh.white': {'before': 'original-white', 'after': 'changed-white'}},
    }


def test_native_midthickness_derivation_preserves_original_reconstruction(tmp_path):
    import nibabel as nib
    import numpy as np
    from network_fmri.fmriprep_seed_rerun import inventory_difference, validate_surface_derivations
    from network_fmri.surface_inventory import reconstruction_inventory
    root = tmp_path/'sub-s29'/'surf'
    root.mkdir(parents=True)
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
    faces = np.array([[0, 1, 2]], dtype=np.int32)
    for hemi in ('lh', 'rh'):
        nib.freesurfer.write_geometry(root/f'{hemi}.white', vertices, faces)
    before = reconstruction_inventory(tmp_path, 's29')
    assert validate_surface_derivations(inventory_difference(before, before), tmp_path, 's29') == {}
    for hemi in ('lh', 'rh'):
        nib.freesurfer.write_geometry(root/f'{hemi}.midthickness', vertices + .5, faces)
    difference = inventory_difference(before, reconstruction_inventory(tmp_path, 's29'))
    proof = validate_surface_derivations(difference, tmp_path, 's29')
    assert set(proof) == {'surf/lh.midthickness', 'surf/rh.midthickness'}
    assert all(row['white_topology_unchanged'] for row in proof.values())
    # Even a pre-existing midthickness file cannot be modified by a repair.
    before = reconstruction_inventory(tmp_path, 's29')
    nib.freesurfer.write_geometry(root/'lh.midthickness', vertices + 1, faces)
    with pytest.raises(ValueError, match='original FreeSurfer'):
        validate_surface_derivations(inventory_difference(before, reconstruction_inventory(tmp_path, 's29')),
                                     tmp_path, 's29')


@pytest.mark.parametrize('problem', ['white_changed', 'brain_removed', 'unexpected_addition'])
def test_surface_derivation_exception_cannot_hide_original_changes(tmp_path, problem):
    from network_fmri.fmriprep_seed_rerun import validate_surface_derivations
    difference = {'added': {}, 'removed': {}, 'changed': {}}
    if problem == 'white_changed':
        difference['changed']['surf/lh.white'] = {'before': 'original', 'after': 'modified'}
    elif problem == 'brain_removed':
        difference['removed']['mri/brain.mgz'] = 'original'
    else:
        difference['added']['surf/lh.unexpected'] = 'new'
    with pytest.raises(ValueError, match='original FreeSurfer'):
        validate_surface_derivations(difference, tmp_path, 's29')
    from network_fmri.fmriprep_seed_rerun import surface_change_report
    report = surface_change_report(difference, tmp_path, 's29')
    assert report['difference'] == difference
    assert report['warning']
    assert report['expected_midthickness_derivation_only'] is False


@pytest.mark.parametrize('problem', ['nonfinite', 'topology', 'vertex_count'])
def test_invalid_generated_midthickness_is_rejected(tmp_path, problem):
    import nibabel as nib
    import numpy as np
    from network_fmri.fmriprep_seed_rerun import validate_surface_derivations
    root = tmp_path/'sub-s29'/'surf'
    root.mkdir(parents=True)
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
    faces = np.array([[0, 1, 2]], dtype=np.int32)
    nib.freesurfer.write_geometry(root/'lh.white', vertices, faces)
    if problem == 'nonfinite':
        vertices[0, 0] = np.nan
    elif problem == 'topology':
        faces = faces[:, ::-1]
    else:
        vertices = np.vstack([vertices, [1, 1, 1]])
    nib.freesurfer.write_geometry(root/'lh.midthickness', vertices, faces)
    with pytest.raises(ValueError, match='topology'):
        validate_surface_derivations({'added': {'surf/lh.midthickness': 'new'}, 'removed': {}, 'changed': {}},
                                     tmp_path, 's29')
