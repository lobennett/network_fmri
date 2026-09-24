import hashlib
from pathlib import Path
import subprocess


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

