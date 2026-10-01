"""An approved removal must survive the handoff without accepting later edits."""
import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from network_fmri import pipeline
from network_fmri.curation import apply_curation
from network_fmri.milestones import receipt_path
from tests.test_curation_stage import Runner, _dataset, _manifest, _write


def git(root, *args):
    return subprocess.run(('git', '-C', str(root), *args), check=True, capture_output=True)


def curated_dataset(root):
    _dataset(root)
    kept = _write(root / 'sub-s01/ses-01/func/sub-s01_ses-01_task-keep_run-1_echo-2_bold.nii.gz', 'keep')
    _write(kept.with_name(kept.name.replace('.nii.gz', '.json')), '{}')
    manifest = _manifest(root / 'code/network_fmri/scan_decisions.tsv')
    config = SimpleNamespace(paths=SimpleNamespace(bids_dir=root), mechababs=None)
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.org')
    approval = receipt_path(root, 'scan-decisions-approved')
    _write(approval, json.dumps({'stage': 'scan-decisions-approved', 'status': 'success', 'validation': {
        'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
        'metadata_sha256': hashlib.sha256(manifest.with_suffix('.meta.json').read_bytes()).hexdigest(),
    }}))
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Approved original inventory')
    result = apply_curation(root, manifest, root / 'validator.sif', Runner())
    _write(receipt_path(root, result.name), json.dumps({
        'stage': result.name, 'status': 'success', 'validation': result.details,
    }))
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'Curated inventory')
    return config, manifest, kept


def gate_runner(command, **kwargs):
    assert command[0] == 'git', 'curated inputs cannot be compared to the original MRIQC inventory'
    return subprocess.run(command, **kwargs)


@pytest.mark.parametrize('annex_link', [False, True])
def test_committed_curation_allows_processing_after_an_approved_drop(tmp_path, annex_link):
    config, _, kept = curated_dataset(tmp_path)
    if annex_link:
        target = _write(tmp_path / '.git/annex/objects/kept-content', kept.read_text())
        kept.unlink()
        kept.symlink_to(target)
        git(tmp_path, 'add', '-A')
        git(tmp_path, 'commit', '-qm', 'Store unchanged content in annex')
    pipeline.require_committed_approval(config, gate_runner)


@pytest.mark.parametrize('change', ['image', 'manifest', 'receipt'])
def test_curated_approval_rejects_unreviewed_changes_even_if_committed(tmp_path, change):
    config, manifest, kept = curated_dataset(tmp_path)
    if change == 'image':
        kept.write_text('unreviewed replacement')
    elif change == 'manifest':
        manifest.write_text(manifest.read_text().replace('\tdrop', '\tkeep'))
    else:
        path = receipt_path(tmp_path, 'bids-curated-validated')
        value = json.loads(path.read_text())
        value['validation'].pop('curated_inventory_sha256', None)
        path.write_text(json.dumps(value))
    git(tmp_path, 'add', '-A')
    git(tmp_path, 'commit', '-qm', 'Unreviewed edit')
    with pytest.raises(RuntimeError, match='curat|approval|decisions'):
        pipeline.require_committed_approval(config, gate_runner)
