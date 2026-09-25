"""Contracts for the upstream command interface and study-specific gates."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from network_fmri.campaign import read_table
from network_fmri.config import MechaBABSAppConfig, MechaBABSConfig
from network_fmri.processing import ProcessingManager

SHORTS = ('MRIQC-24.0.2', 'fMRIPrep-25.2.5+anat', 'fMRIPrep-25.2.5+full')


def configuration(tmp_path):
    mechababs = MechaBABSConfig(
        study_dir=tmp_path / 'study', durable_sibling=tmp_path / 'oak',
        campaign='network-v1', raw_slot='raw', container_dataset=tmp_path / 'containers',
        mechababs_commit='d' * 40, babs_commit='e' * 40, cluster_file=Path('sherlock.yaml'),
        apps=tuple(MechaBABSAppConfig(name, Path(short + '.yaml')) for name, short in
                   zip(('mriqc', 'anatomical', 'fmriprep'), SHORTS)),
    )
    return SimpleNamespace(mechababs=mechababs, paths=SimpleNamespace(bids_dir=tmp_path / 'raw'))


def table(rows, columns):
    # Upstream status.render format, including blank cells.
    widths = {c: max(len(c), *(len(r.get(c, '')) for r in rows)) for c in columns}
    return '\n'.join('  '.join(row.get(c, '').ljust(widths[c]) for c in columns).rstrip()
                     for row in [dict(zip(columns, columns)), *rows]) + '\n'


class Runner:
    def __init__(self, states=('not started', 'waiting on MRIQC-24.0.2', 'waiting on fMRIPrep-25.2.5+anat'), apps=SHORTS):
        self.calls = []
        self.states = states
        self.apps = apps

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[1] == 'status' and command[0] != 'git':
            stdout = table([dict(source_dataset='sourcedata/raw', app=app, state=state, jobs='')
                            for app, state in zip(self.apps, self.states)],
                           ('source_dataset', 'app', 'state', 'jobs'))
        elif command[1] == 'jobs':
            stdout = table([dict(source_dataset='sourcedata/raw', app=SHORTS[0], sub_id='sub-s01',
                                ses_id='', job_id='12_1', state='R', logs='/scratch/logs')],
                           ('source_dataset', 'app', 'sub_id', 'ses_id', 'job_id', 'state', 'logs'))
        else:
            stdout = 'a' * 40 + '\n' if command[:2] == ('git', 'rev-parse') else ''
        return SimpleNamespace(stdout=stdout, returncode=0)


def test_status_reads_upstream_status_and_jobs(tmp_path):
    runner = Runner()
    config = configuration(tmp_path)
    status = ProcessingManager(config, runner=runner).status()
    assert [s.state for s in status.stages] == ['ready', 'blocked', 'blocked']
    assert status.jobs[0]['ses_id'] == ''
    assert status.jobs[0]['job_id'] == '12_1'
    for command, kwargs in runner.calls:
        assert '--campaign-path' not in command and '--output' not in command
        assert kwargs['cwd'] == str(config.mechababs.study_dir)
        assert kwargs['env']['MECHABABS_CAMPAIGN'] == 'network-v1'
        assert kwargs['env']['PATH'].startswith(str(config.mechababs.campaign_dir / '.venv/bin'))


@pytest.mark.parametrize('stage,index', [('mriqc', 0), ('anatomical', 1), ('fmriprep', 2)])
def test_only_requested_app_advances_after_gate(tmp_path, monkeypatch, stage, index):
    runner = Runner(tuple('merged' if n < index else 'not started' for n in range(3)))
    gates = []
    monkeypatch.setattr('network_fmri.processing.require_stage_gate', lambda _, name, __: gates.append(name))
    result = ProcessingManager(configuration(tmp_path), runner=runner).advance(stage)
    assert result.advanced
    assert gates == [stage]
    assert runner.calls[-1][0][1:] == ('iterate', '--app', SHORTS[index], '--batch', '1')


def test_predecessor_blocks_advancement(tmp_path):
    with pytest.raises(RuntimeError, match='mriqc must be complete'):
        ProcessingManager(configuration(tmp_path), runner=Runner()).advance('anatomical')


def standalone_config(tmp_path):
    from dataclasses import replace
    config = configuration(tmp_path)
    config.subjects = ('s03',)
    apps = list(config.mechababs.apps)
    apps[1] = MechaBABSAppConfig('anatomical', Path('FreeSurfer-8.2.0.yaml'))
    config.mechababs = replace(config.mechababs, apps=tuple(apps))
    return config


def test_standalone_freesurfer_advances_while_mriqc_is_active(tmp_path, monkeypatch):
    config = standalone_config(tmp_path)
    runner = Runner(('active', 'not started', 'not started'),
                    apps=tuple(app.file.stem for app in config.mechababs.apps))
    monkeypatch.setattr('network_fmri.processing.require_stage_gate', lambda *a: None)
    manager = ProcessingManager(config, runner=runner)
    assert [s.state for s in manager.plan()] == ['active', 'ready', 'blocked']
    assert manager.advance('anatomical').advanced
    with pytest.raises(RuntimeError, match='mriqc must be complete'):
        manager.advance('fmriprep')


def test_standalone_gate_requires_validated_unambiguous_anatomy(tmp_path, monkeypatch):
    from network_fmri.processing import require_stage_gate
    config = standalone_config(tmp_path)
    checked = []
    monkeypatch.setattr('network_fmri.processing._require_committed_milestone',
                        lambda root, name, runner: checked.append(name))
    monkeypatch.setattr('network_fmri.pipeline.require_committed_approval',
                        lambda *a: pytest.fail('MRIQC approval must not gate standalone anatomy'))
    with pytest.raises(ValueError, match='expected one T1w'):
        require_stage_gate(config, 'anatomical')
    t1 = config.paths.bids_dir / 'sub-s03/ses-01/anat/sub-s03_ses-01_T1w.nii.gz'
    t1.parent.mkdir(parents=True)
    t1.write_bytes(b'defaced anatomy')
    require_stage_gate(config, 'anatomical')
    assert checked == ['bids-precuration-validated'] * 2


def test_fmriprep_still_requires_scan_approval(tmp_path, monkeypatch):
    from network_fmri.processing import require_stage_gate
    def unapproved(*a):
        raise RuntimeError('scan approval missing')
    monkeypatch.setattr('network_fmri.pipeline.require_committed_approval', unapproved)
    with pytest.raises(RuntimeError, match='scan approval missing'):
        require_stage_gate(standalone_config(tmp_path), 'fmriprep')


def test_failure_blocks_advancement(tmp_path):
    with pytest.raises(RuntimeError, match='intervention-required'):
        ProcessingManager(configuration(tmp_path), runner=Runner(('FAILED', 'not started', 'not started'))).advance('mriqc')


def test_merged_derivative_is_already_in_study(tmp_path):
    runner = Runner(('merged', 'not started', 'not started'))
    result = ProcessingManager(configuration(tmp_path), runner=runner).advance('mriqc')
    assert not result.advanced
    assert not any(command[0] == 'datalad' for command, _ in runner.calls)


def test_unexpected_table_is_rejected():
    with pytest.raises(RuntimeError, match='schema'):
        read_table('unexpected output\n')


def test_fresh_campaign_has_no_jobs(tmp_path):
    base = Runner()

    def runner(command, **kwargs):
        if command[1] == "jobs":
            return SimpleNamespace(stdout="", returncode=0)
        return base(command, **kwargs)

    assert ProcessingManager(configuration(tmp_path), runner=runner).status().jobs == ()


def test_dirty_raw_blocks_submission(tmp_path, monkeypatch):
    base = Runner()
    monkeypatch.setattr('network_fmri.processing.require_stage_gate', lambda *args: None)

    def runner(command, **kwargs):
        if command[:2] == ('git', 'status'):
            return SimpleNamespace(stdout=' M participants.tsv\n', returncode=0)
        return base(command, **kwargs)

    with pytest.raises(RuntimeError, match='raw dataset is dirty'):
        ProcessingManager(configuration(tmp_path), runner=runner).advance('mriqc')
    assert not any('iterate' in command for command, _ in base.calls)


def test_raw_update_saves_only_raw_subdataset_and_current_identity(tmp_path):
    config = configuration(tmp_path)
    installed = config.mechababs.study_dir / 'sourcedata/raw'
    manifest = config.mechababs.study_dir / 'code/network_fmri/study.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'raw_commit': 'b' * 40, 'study_id': 'study-id'}))
    unrelated = config.mechababs.study_dir / 'notes.txt'
    unrelated.write_text('Uncommitted operator notes')
    commands = []
    installed_reads = 0

    def runner(command, **kwargs):
        nonlocal installed_reads
        commands.append(command)
        output = ''
        if command[:2] == ('git', 'rev-parse'):
            output = 'a' * 40
            if kwargs['cwd'] == str(installed):
                installed_reads += 1
                if installed_reads == 1:
                    output = 'b' * 40
        return SimpleNamespace(stdout=output, returncode=0)

    ProcessingManager(config, runner=runner)._sync_raw_subdataset()
    assert commands[-1][-2:] == (str(installed), str(manifest))
    assert commands[-1][:2] == ('datalad', 'save')
    assert json.loads(manifest.read_text()) == {'raw_commit': 'a' * 40, 'study_id': 'study-id'}
    assert unrelated.read_text() == 'Uncommitted operator notes'
    saves = sum(command[:2] == ('datalad', 'save') for command in commands)
    ProcessingManager(config, runner=runner)._sync_raw_subdataset()
    assert sum(command[:2] == ('datalad', 'save') for command in commands) == saves


def test_raw_update_refuses_dirty_identity_before_updating(tmp_path):
    config = configuration(tmp_path)
    installed = config.mechababs.study_dir / 'sourcedata/raw'
    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        output = ''
        if command[:2] == ('git', 'rev-parse'):
            output = ('b' if kwargs['cwd'] == str(installed) else 'a') * 40
        elif command[:2] == ('git', 'status') and '--' in command:
            output = ' M code/network_fmri/study.json'
        return SimpleNamespace(stdout=output, returncode=0)

    with pytest.raises(RuntimeError, match='identity manifest is dirty'):
        ProcessingManager(config, runner=runner)._sync_raw_subdataset()
    assert not any(command[0] == 'datalad' for command in commands)


def test_fmriprep_rejects_approval_for_another_surface_derivative(tmp_path, monkeypatch):
    from dataclasses import replace
    from network_fmri.processing import require_stage_gate
    config = configuration(tmp_path)
    config.mechababs = replace(config.mechababs, apps=(
        MechaBABSAppConfig('anatomical', Path('FreeSurfer-8.2.0.yaml')),
    ))
    monkeypatch.setattr('network_fmri.pipeline.require_committed_surface_approval', lambda *a: None)
    monkeypatch.setattr('network_fmri.pipeline.require_committed_approval', lambda *a: None)
    monkeypatch.setattr('network_fmri.processing._require_committed_milestone', lambda *a: None)
    monkeypatch.setattr('network_fmri.surface_evidence.prepare_surface_evidence',
                        lambda *a, **k: tmp_path / 'new-surfaces')
    metadata = config.mechababs.study_dir / 'code/network_fmri/surface_review.meta.json'
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({'surface_root': str(tmp_path / 'old-surfaces')}))
    with pytest.raises(RuntimeError, match='current standalone'):
        require_stage_gate(config, 'fmriprep', Runner())
