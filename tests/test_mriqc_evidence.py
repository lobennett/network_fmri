"""Archive handoff tests use real Git snapshots and ZIP files."""
import json
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from network_fmri.mriqc import prepare_mriqc_review
from tests.test_processing import configuration, Runner


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def init(root, identity):
    root.mkdir(parents=True, exist_ok=True)
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.org')
    (root / '.datalad').mkdir()
    (root / '.datalad/config').write_text(f'[datalad "dataset"]\n\tid = {identity}\n')


def save(root):
    git(root, 'add', '.')
    git(root, 'commit', '--allow-empty', '-qm', 'fixture save')
    return git(root, 'rev-parse', 'HEAD')


def register_source(config, source):
    study = config.mechababs.study_dir
    git(study, 'update-index', '--add', '--cacheinfo',
        f'160000,{git(source, "rev-parse", "HEAD")},{source.relative_to(study)}')
    git(study, 'commit', '--allow-empty', '-qm', 'updated source')


class Commands:
    def __init__(self):
        self.calls = []
        self.upstream = Runner(('merged', 'not started', 'not started'))

    def __call__(self, command, **kwargs):
        command = tuple(map(str, command))
        self.calls.append(command)
        if command[0].endswith('/mechababs'):
            return self.upstream(command, **kwargs)
        if command[0] == 'datalad':
            if command[1] == 'create':
                init(Path(command[-1]), 'evidence-id')
            elif command[1] == 'save':
                root = Path(command[command.index('-d') + 1])
                # Registration is represented by a real Git gitlink in the study.
                if 'study' == root.name:
                    target = Path(command[-1])
                    relative = target.relative_to(root)
                    git(root, 'update-index', '--add', '--cacheinfo',
                        f'160000,{git(target, "rev-parse", "HEAD")},{relative}')
                    git(root, 'commit', '--allow-empty', '-qm', 'register evidence')
                else:
                    save(root)
            return SimpleNamespace(stdout='', returncode=0)
        return subprocess.run(command, **kwargs)


@pytest.fixture
def project(tmp_path):
    config = configuration(tmp_path)
    study = config.mechababs.study_dir
    init(study, 'study-id')
    source = study / 'derivatives/MRIQC-24.0.2+network-v1'
    init(source, 'babs-id')
    raw = tmp_path / 'raw'
    init(raw, 'raw-id')
    raw_commit = save(raw)
    git(source, '-c', 'protocol.file.allow=always', 'clone', '-q', str(raw), 'sourcedata/raw')
    git(source, 'update-index', '--add', '--cacheinfo', f'160000,{raw_commit},sourcedata/raw')
    (source / 'code').mkdir()
    (source / 'code/processing_inclusion.csv').write_text('sub_id\nsub-s01\n')
    archive = source / 'sub-s01_MRIQC-24.0.2-24-0-2.zip'
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr('MRIQC-24.0.2/logs/boilerplate.json', '{}')
        stream.writestr('MRIQC-24.0.2/dataset_description.json', json.dumps({
            'Name': 'MRIQC', 'GeneratedBy': [{'Name': 'MRIQC', 'Version': '24.0.2'}]}))
        stream.writestr('MRIQC-24.0.2/sub-s01/ses-01/anat/sub-s01_ses-01_T1w.json',
                        json.dumps({'cjv': .5, 'provenance': {'version': '24.0.2'}}))
        stream.writestr('MRIQC-24.0.2/sub-s01_ses-01_T1w.html', '<html>report</html>')
        stream.writestr('MRIQC-24.0.2/sub-s01/ses-01/anat/image.nii.gz', b'do not copy imaging')
    source_commit = save(source)
    git(study, 'update-index', '--add', '--cacheinfo',
        f'160000,{source_commit},{source.relative_to(study)}')
    save(study)
    # Raw may have advanced since BABS ran. This must never replace the gitlink pin.
    (raw / 'later.txt').write_text('later raw milestone')
    save(raw)
    return config, source, archive, raw_commit, Commands()


def test_handoff_binds_real_archive_snapshot_and_is_idempotent(project):
    config, source, archive, raw_commit, runner = project
    result = prepare_mriqc_review(config, runner=runner)
    assert result.created
    target = result.evidence_dir
    assert target == source.with_name(source.name + '+review')
    receipt = json.loads((target / 'code/network_fmri/mriqc-evidence.json').read_text())
    assert receipt['source_dataset_id'] == 'babs-id'
    assert receipt['source_dataset_name'] == source.name
    assert 'source_dataset_path' not in receipt
    assert receipt['source_dataset_commit'] == git(source, 'rev-parse', 'HEAD')
    assert receipt['input_datalad_commit'] == raw_commit
    assert receipt['raw_gitlink'] == 'sourcedata/raw'
    assert not list(target.rglob('*.nii.gz'))
    assert (target / 'sub-s01_ses-01_T1w.html').is_file()
    assert not (target / 'code/network_fmri/run-receipts').exists()
    before = git(target, 'rev-parse', 'HEAD'), git(config.mechababs.study_dir, 'rev-parse', 'HEAD')
    repeated = prepare_mriqc_review(config, runner=runner)
    assert not repeated.created
    assert before == (git(target, 'rev-parse', 'HEAD'), git(config.mechababs.study_dir, 'rev-parse', 'HEAD'))
    assert not any(call[:2] == ('datalad', 'get') for call in runner.calls)
    from network_qa.compiler import _archive_evidence_commit
    assert _archive_evidence_commit(target) == raw_commit


@pytest.mark.parametrize('member', ['../outside.json', '/absolute.json',
    'MRIQC-24.0.2/../../outside.json', 'MRIQC-24.0.2/.git/config',
    'MRIQC-24.0.2/sub-s02_ses-01_T1w.html', 'MRIQC-24.0.2/back\\slash.json'])
def test_unsafe_or_wrong_subject_members_fail_before_publication(project, member):
    config, source, archive, _, runner = project
    with zipfile.ZipFile(archive, 'a') as stream:
        stream.writestr(member, '{}')
    save(source)
    git(config.mechababs.study_dir, 'update-index', '--cacheinfo',
        f'160000,{git(source, "rev-parse", "HEAD")},{source.relative_to(config.mechababs.study_dir)}')
    git(config.mechababs.study_dir, 'commit', '-qm', 'updated source')
    with pytest.raises((ValueError, RuntimeError), match='member|subject'):
        prepare_mriqc_review(config, runner=runner)
    assert not source.with_name(source.name + '+review').exists()


def test_zip_symlink_fails_even_when_not_an_evidence_extension(project):
    config, source, archive, _, runner = project
    with zipfile.ZipFile(archive, 'a') as stream:
        member = zipfile.ZipInfo('MRIQC-24.0.2/redirect')
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        stream.writestr(member, '/outside')
    save(source)
    register_source(config, source)
    with pytest.raises((ValueError, RuntimeError)):
        prepare_mriqc_review(config, runner=runner)
    assert not source.with_name(source.name + '+review').exists()


def test_duplicate_zip_member_rejected(project):
    config, source, archive, _, runner = project
    with pytest.warns(UserWarning, match='Duplicate'):
        with zipfile.ZipFile(archive, 'a') as stream:
            stream.writestr('MRIQC-24.0.2/sub-s01_ses-01_T1w.html', 'duplicate')
    save(source)
    register_source(config, source)
    with pytest.raises((ValueError, RuntimeError)):
        prepare_mriqc_review(config, runner=runner)


def test_existing_changed_evidence_is_never_overwritten(project):
    config, source, _, _, runner = project
    result = prepare_mriqc_review(config, runner=runner)
    report = result.evidence_dir / 'sub-s01_ses-01_T1w.html'
    report.write_text('human changed this file')
    with pytest.raises(RuntimeError, match='existing|dirty|mismatch'):
        prepare_mriqc_review(config, runner=runner)
    assert report.read_text() == 'human changed this file'


def test_missing_archive_content_fails_without_publication(project):
    config, source, archive, _, runner = project
    archive.unlink()
    archive.symlink_to('.git/annex/objects/missing.zip')
    save(source)
    register_source(config, source)
    with pytest.raises((OSError, ValueError, RuntimeError, subprocess.CalledProcessError)):
        prepare_mriqc_review(config, runner=runner)
    assert not source.with_name(source.name + '+review').exists()


def test_unmerged_mriqc_blocks_handoff(project):
    config, source, _, _, runner = project
    runner.upstream = Runner(('active', 'not started', 'not started'))
    with pytest.raises(RuntimeError, match='merged|complete'):
        prepare_mriqc_review(config, runner=runner)
    assert not source.with_name(source.name + '+review').exists()
