"""Final registration review uses pinned archives and survives controller restarts."""
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from tests.test_mriqc_evidence import Commands, git, init, save, register_source


def test_extracts_only_native_boldrefs(tmp_path):
    from network_fmri.registration_qc import extract_boldrefs
    archive = tmp_path / 'output.zip'
    name = 'sub-s03/ses-01/func/sub-s03_ses-01_task-rest_run-1_space-T1w_boldref.nii.gz'
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr('fMRIPrep/' + name, b'reference')
        stream.writestr('fMRIPrep/sub-s03/anat/anatomy.nii.gz', b'not needed')
    records = extract_boldrefs(archive, 's03', tmp_path / 'inputs')
    assert [row['path'] for row in records] == [name]
    assert (tmp_path / 'inputs' / name).read_bytes() == b'reference'
    assert len(list((tmp_path / 'inputs').rglob('*.nii.gz'))) == 1


@pytest.mark.parametrize('entry', ['fMRIPrep/../escape', '/absolute',
    'fMRIPrep/sub-s03/ses-01/func/sub-s03_space-T1w_boldref.nii.gz'])
def test_invalid_zip_is_rejected_before_writing(tmp_path, entry):
    from network_fmri.registration_qc import extract_boldrefs
    archive = tmp_path / 'output.zip'
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr('fMRIPrep/sub-s03/ses-01/func/sub-s03_space-T1w_boldref.nii.gz', b'ref')
        stream.writestr(entry, b'invalid')
    with pytest.raises(ValueError):
        extract_boldrefs(archive, 's03', tmp_path / 'inputs')
    assert not (tmp_path / 'inputs').exists()


def test_missing_t1w_boldrefs_fails(tmp_path):
    from network_fmri.registration_qc import extract_boldrefs
    archive = tmp_path / 'output.zip'
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr('fMRIPrep/sub-s03/func/sub-s03_space-MNI_boldref.nii.gz', b'ref')
    with pytest.raises(RuntimeError, match='T1w'):
        extract_boldrefs(archive, 's03', tmp_path / 'inputs')


@pytest.fixture
def project(tmp_path, monkeypatch):
    from network_fmri import registration_qc as qc
    study = tmp_path / 'study'
    source = study / 'derivatives/fMRIPrep-25.2.5+full+pilot'
    surface = study / 'derivatives/FreeSurfer-8.2.0+pilot+review'
    init(study, 'study')
    init(source, 'fmri')
    init(surface, 'fs')
    reconstruction = study / 'derivatives/FreeSurfer-8.2.0+pilot'
    init(reconstruction, 'reconstruction')
    (reconstruction / 'sub-s03_FreeSurfer.zip').write_bytes(b'approved reconstruction')
    reconstruction_commit = save(reconstruction)
    git(source, '-c', 'protocol.file.allow=always', 'clone', '-q', str(reconstruction), 'sourcedata/FreeSurfer-8.2.0')
    surface_receipt = surface / 'code/network_fmri/surface-evidence.json'
    surface_receipt.parent.mkdir(parents=True)
    surface_receipt.write_text(json.dumps({'source_dataset_commit': reconstruction_commit,
        'source_dataset_id': 'reconstruction', 'source_project': reconstruction.relative_to(study).as_posix()}))
    ribbon = surface / 'subjects/sub-s03/mri/ribbon.mgz'
    ribbon.parent.mkdir(parents=True)
    ribbon.write_bytes(b'approved ribbon')
    archive = source / 'sub-s03_fMRIPrep.zip'
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr('fMRIPrep/sub-s03/func/sub-s03_task-rest_space-T1w_boldref.nii.gz', b'ref')
    save(source)
    save(surface)
    save(study)
    config = SimpleNamespace(subjects=('s03',), mechababs=SimpleNamespace(study_dir=study))
    register_source(config, source)
    register_source(config, surface)
    stage = SimpleNamespace(stage='fmriprep', state='complete', project=source.relative_to(study).as_posix())
    monkeypatch.setattr(qc, 'ProcessingManager', lambda *a, **kw: SimpleNamespace(plan=lambda: [stage]))
    monkeypatch.setattr(qc, 'prepare_surface_evidence', lambda *a, **kw: surface / 'subjects')
    monkeypatch.setattr(qc, 'require_committed_surface_approval', lambda *a, **kw: None)
    class Runner(Commands):
        def __call__(self, command, **kwargs):
            if command[0] == 'fmriprepviz':
                self.calls.append(command)
                output = Path(command[command.index('-o') + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b'GIF89a')
                output.with_suffix('.html').write_text('<html>viewer</html>')
                return SimpleNamespace(returncode=0)
            return super().__call__(command, **kwargs)
    return config, stage, Runner()


def test_publishes_verified_outputs_once_and_records_sources(project):
    from network_fmri.registration_qc import prepare_registration_qc, RECEIPT
    config, stage, runner = project
    target = prepare_registration_qc(config, runner=runner)
    receipt = json.loads((target / RECEIPT).read_text())
    assert receipt['inputs']['source_commit'] == git(config.mechababs.study_dir / stage.project, 'rev-parse', 'HEAD')
    assert receipt['inputs']['software']['fmriprepviz']['commit']
    assert (target / 'sub-s03/sub-s03_desc-registration.html').is_file()
    assert not list(target.rglob('*.nii.gz'))
    from network_fmri.records.lineage import read_receipt
    lineage = read_receipt(target / 'code/network_fmri/lineage/sub-s03_registration.json')
    assert len(lineage['links']) == 4  # archive + ribbon -> GIF + HTML
    assert prepare_registration_qc(config, runner=runner) == target
    assert len([c for c in runner.calls if c[0] == 'fmriprepviz']) == 1
    (target / 'sub-s03/sub-s03_desc-registration.html').write_text('changed')
    save(target)
    register_source(config, target)
    with pytest.raises(RuntimeError, match='changed'):
        prepare_registration_qc(config, runner=runner)


def test_cannot_run_before_fmriprep_merge(project):
    from network_fmri.registration_qc import prepare_registration_qc
    config, stage, runner = project
    stage.state = 'active'
    with pytest.raises(RuntimeError, match='merged'):
        prepare_registration_qc(config, runner=runner)
    assert not runner.calls


def test_restart_accepts_verified_legacy_registration_receipt(project):
    from network_fmri.registration_qc import prepare_registration_qc, RECEIPT
    config, stage, runner = project
    target = prepare_registration_qc(config, runner=runner)
    path = target / RECEIPT
    receipt = json.loads(path.read_text())
    receipt['inputs'].pop('reconstruction', None)
    receipt.pop('reconstruction', None)
    path.write_text(json.dumps(receipt))
    save(target)
    register_source(config, target)
    assert prepare_registration_qc(config, runner=runner) == target
    assert len([c for c in runner.calls if c[0] == 'fmriprepviz']) == 1


@pytest.mark.parametrize('status,want', [('success','awaiting-output-review'),('issues','output-checks-failed')])
def test_final_boundary_extracts_and_checks_before_manual_review(tmp_path, status, want):
    from network_fmri import handoff, registration_qc, fmriprep_evidence
    from unittest.mock import patch
    evidence=tmp_path/'reports'
    receipt=evidence/fmriprep_evidence.RECEIPT
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({'status':status}))
    with patch.object(registration_qc, 'prepare_registration_qc', return_value=Path('/study/derivatives/qc')) as render, \
         patch.object(fmriprep_evidence, 'prepare_fmriprep_review', return_value=evidence):
        result=handoff._prepare_boundary(SimpleNamespace(mechababs=SimpleNamespace(study_dir=Path('/study'))), 'fmriprep')
    assert result['state']==want
    assert result['fmriprep_evidence']==str(evidence)
    if status == 'success':
        assert result['registration_qc']=='/study/derivatives/qc'
    else:
        render.assert_not_called()
        assert 'registration_qc' not in result


def test_render_failure_does_not_publish_success(project):
    import subprocess
    from network_fmri.registration_qc import prepare_registration_qc
    config, stage, runner = project
    def fail(command, **kwargs):
        if command[0] == 'fmriprepviz':
            kwargs['stdout'].write('renderer failed: no overlap')
            raise subprocess.CalledProcessError(1, command)
        return runner(command, **kwargs)
    with pytest.raises(RuntimeError, match='no overlap'):
        prepare_registration_qc(config, runner=fail)
    assert not list((config.mechababs.study_dir / 'derivatives').glob('fmriprepviz-*'))


def test_rejects_surfaces_other_than_those_used_by_fmriprep(project):
    from network_fmri.registration_qc import prepare_registration_qc
    config, stage, runner = project
    source = config.mechababs.study_dir / stage.project
    git(source, 'update-index', '--cacheinfo', '160000,' + 'b' * 40 + ',sourcedata/FreeSurfer-8.2.0')
    git(source, 'commit', '-qm', 'different reconstruction')
    register_source(config, source)
    with pytest.raises(RuntimeError, match='reconstruction used by fMRIPrep'):
        prepare_registration_qc(config, runner=runner)


@pytest.mark.parametrize('changed', [False, True])
def test_merged_surface_commit_requires_identical_tracked_content(project, changed):
    from network_fmri.registration_qc import prepare_registration_qc, RECEIPT
    config, stage, runner = project
    study = config.mechababs.study_dir
    reconstruction = study / 'derivatives/FreeSurfer-8.2.0+pilot'
    surface = study / 'derivatives/FreeSurfer-8.2.0+pilot+review'
    used = git(reconstruction, 'rev-parse', 'HEAD')
    if changed:
        (reconstruction / 'sub-s03_FreeSurfer.zip').write_bytes(b'different reconstruction')
        save(reconstruction)
    else:
        git(reconstruction, 'commit', '--allow-empty', '-qm', 'Record BABS merge')
    reviewed = git(reconstruction, 'rev-parse', 'HEAD')
    path = surface / 'code/network_fmri/surface-evidence.json'
    receipt = json.loads(path.read_text())
    receipt['source_dataset_commit'] = reviewed
    path.write_text(json.dumps(receipt))
    save(surface)
    save(study)
    if changed:
        with pytest.raises(RuntimeError, match='reconstruction used by fMRIPrep'):
            prepare_registration_qc(config, runner=runner)
        assert not list((study / 'derivatives').glob('fmriprepviz-*'))
    else:
        target = prepare_registration_qc(config, runner=runner)
        proof = json.loads((target / RECEIPT).read_text())['reconstruction']
        assert proof['fmriprep_input_commit'] == used
        assert proof['reviewed_commit'] == reviewed
        assert proof['tree'] == git(reconstruction, 'rev-parse', used + '^{tree}')
        assert prepare_registration_qc(config, runner=runner) == target
