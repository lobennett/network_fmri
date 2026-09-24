"""Contracts for the upstream command interface and study-specific gates."""
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
    def __init__(self, states=('not started', 'waiting on MRIQC-24.0.2', 'waiting on fMRIPrep-25.2.5+anat')):
        self.calls = []
        self.states = states

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[1] == 'status' and command[0] != 'git':
            stdout = table([dict(source_dataset='sourcedata/raw', app=app, state=state, jobs='')
                            for app, state in zip(SHORTS, self.states)],
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


def test_raw_update_saves_only_raw_subdataset(tmp_path):
    config = configuration(tmp_path)
    installed = config.mechababs.study_dir / 'sourcedata/raw'
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
    assert commands[-1][-1] == str(installed)
    assert commands[-1][:2] == ('datalad', 'save')
