import hashlib
from pathlib import Path
import subprocess
import pytest


@pytest.mark.parametrize('present', [False, True])
def test_md5_annex_key_verifies_missing_image_content(tmp_path, present):
    from network_fmri.records.inventory import inventory_dataset
    git(tmp_path, 'init', '-q')
    payload = b'image bytes'
    digest = hashlib.md5(payload).hexdigest()
    key = f'MD5E-s{len(payload)}--{digest}.nii.gz'
    target = tmp_path / '.git/annex/objects/ab/cd' / key / key
    image = tmp_path / 'sub-s03_echo-1_bold.nii.gz'
    image.symlink_to(target.relative_to(tmp_path))
    if present:
        target.parent.mkdir(parents=True)
        target.write_bytes(payload)
    git(tmp_path, 'add', image.name)
    git(tmp_path, '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', 'annex image')
    artifact = inventory_dataset(tmp_path, 'ds')['artifacts'][0]
    expected = 'sha256:' + hashlib.sha256(payload).hexdigest() if present else 'md5:' + digest
    assert artifact['content_id'] == expected


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def test_inventory_retains_missing_annex_identity_and_labels_dirty_content(tmp_path):
    from network_fmri.records.inventory import inventory_dataset
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.org')
    (tmp_path / '.datalad').mkdir()
    (tmp_path / '.datalad/config').write_text('[datalad "dataset"]\n id = ds\n')
    (tmp_path / 'report.txt').write_text('committed report')
    missing = tmp_path / 'sub-s03_bold.nii.gz'
    key = 'SHA256E-s100--' + 'a' * 64 + '.nii.gz'
    missing.symlink_to('.git/annex/objects/ab/cd/' + key + '/' + key)
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'fixture')
    commit = git(tmp_path, 'rev-parse', 'HEAD')
    result = inventory_dataset(tmp_path, 'ds')
    by_path = {a['path']: a for a in result['artifacts']}
    assert by_path[missing.name]['availability'] == 'unavailable'
    assert by_path[missing.name]['content_id'] == 'sha256:' + 'a' * 64
    assert by_path['report.txt']['commit'] == commit
    (tmp_path / 'report.txt').write_text('changed report')
    after = inventory_dataset(tmp_path, 'ds')
    changed = next(a for a in after['artifacts'] if a['path'] == 'report.txt')
    assert changed['commit'] is None
    assert changed['content_id'] == 'sha256:' + hashlib.sha256(b'changed report').hexdigest()
