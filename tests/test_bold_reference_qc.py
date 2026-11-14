"""Detect displaced N4 weights independently of BOLD intensity scale."""

import nibabel as nib
import numpy as np
import pytest
import subprocess
import zipfile

from network_fmri.bold_reference_qc import VERSION, SUFFIXES, classify, inventory, summarize, weight_metrics


def image(values, affine=None):
    return nib.Nifti1Image(np.asarray(values, dtype="float32"), np.eye(4) if affine is None else affine)


def test_displaced_weight_is_detected_even_with_normal_brain_intensity():
    brain = np.zeros((12, 12, 12))
    brain[2:6, 2:6, 2:6] = 1
    weight = np.zeros_like(brain)
    weight[7:11, 7:11, 7:11] = 1
    result = weight_metrics(image(weight), image(brain))
    assert result["brain_coverage"] == 0
    assert result["weight_dice"] == 0
    assert result["centroid_distance_mm"] == pytest.approx(np.sqrt(75))
    assert classify(result) == "flagged"


def test_aligned_probability_weights_pass():
    brain = np.zeros((12, 12, 12))
    brain[2:6, 2:6, 2:6] = 1
    result = weight_metrics(image(brain * 0.9), image(brain))
    assert result["brain_coverage"] == 1
    assert result["mean_brain_weight"] == pytest.approx(0.9)
    assert result["weight_dice"] == 1
    assert result["centroid_distance_mm"] == 0
    assert classify(result) == "pass"


@pytest.mark.parametrize("coverage,status", [(0.249, "flagged"), (0.25, "borderline"), (0.749, "borderline"), (0.75, "pass")])
def test_coverage_boundaries(coverage, status):
    assert classify({"brain_coverage": coverage}) == status


def test_empty_weight_is_flagged_but_empty_brain_is_invalid():
    empty, full = image(np.zeros((2, 2, 2))), image(np.ones((2, 2, 2)))
    result = weight_metrics(empty, full)
    assert result["centroid_distance_mm"] is None
    assert classify(result) == "flagged"
    with pytest.raises(ValueError, match="empty brain"):
        weight_metrics(full, empty)


def test_mismatched_affine_and_nonfinite_weights_are_invalid():
    full = np.ones((2, 2, 2))
    with pytest.raises(ValueError, match="grid"):
        weight_metrics(image(full, np.diag([2, 2, 2, 1])), image(full))
    full[0, 0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        weight_metrics(image(full), image(np.ones((2, 2, 2))))


def inventory_fixture(tmp_path):
    raw, archives = tmp_path / 'raw', tmp_path / 'archives'
    for path in (raw, archives):
        path.mkdir()
        subprocess.run(['git', 'init', '-q', str(path)], check=True)
        subprocess.run(['git', '-C', str(path), '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '--allow-empty', '-qm', 'Fixture'], check=True)
    roster = tmp_path / 'subjects.txt'
    roster.write_text(''.join(f's{i:02}\n' for i in range(46)))
    for i in range(46):
        directory = raw / f'sub-s{i:02}/ses-01/func'
        directory.mkdir(parents=True)
        for echo in range(1, 4):
            (directory / f'sub-s{i:02}_ses-01_task-rest_run-1_echo-{echo}_bold.nii.gz').touch()
    return raw, archives, roster


def test_inventory_accounts_for_missing_subjects_and_groups_echoes(tmp_path):
    raw, archives, roster = inventory_fixture(tmp_path)
    prefix = 'fMRIPrep/sub-s00/ses-01/func/sub-s00_ses-01_task-rest_run-1'
    with zipfile.ZipFile(archives / 'sub-s00_fMRIPrep-test.zip', 'w') as z:
        for suffix in SUFFIXES.values():
            z.writestr(prefix + suffix, b'header is read in the audit')
        config = f'[environment]\nversion = "{VERSION}"\n[nipype]\nomp_nthreads = 2\n[seeds]\nmaster = 12345\nants = 54608\nnumpy = 1333\n'
        z.writestr('fMRIPrep/sub-s00/log/id/fmriprep.toml', config)
    result = inventory(raw, archives, roster)
    assert len(result['runs']) == 46
    ready = [row for row in result['runs'] if row['status'] == 'ready']
    assert len(ready) == 1
    assert ready[0]['seeds'] == {'master': 12345, 'ants': 54608, 'numpy': 1333}
    assert sum(row['status'] == 'missing-archive' for row in result['runs']) == 45
    assert len(result['archive_commit']) == 40


def test_inventory_rejects_incomplete_roster_and_sbref(tmp_path):
    raw, archives, roster = inventory_fixture(tmp_path)
    (raw / 'sub-s00/ses-01/func/sub-s00_ses-01_task-rest_sbref.nii.gz').touch()
    with pytest.raises(ValueError, match='SBRef'):
        inventory(raw, archives, roster)
    roster.write_text('s00\n')
    with pytest.raises(ValueError, match='46'):
        inventory(raw, archives, roster)


def test_summary_blocks_incomplete_audit_and_more_than_ten_percent():
    manifest = {'archive_commit': 'a', 'raw_commit': 'b', 'runs': [{'run': str(i), 'status': 'ready'} for i in range(10)]}
    results = [dict(row, status='pass') for row in manifest['runs']]
    results[0]['status'] = 'flagged'
    summary = summarize(manifest, results)
    assert summary['flagged_fraction'] == 0.1
    assert not summary['stop_before_reruns']
    results[1]['status'] = 'flagged'
    assert summarize(manifest, results)['stop_before_reruns']
    incomplete = summarize(manifest, results[2:])
    assert incomplete['counts']['not-audited'] == 2
    assert incomplete['stop_before_reruns']


def test_summary_rejects_duplicate_and_changed_source_results():
    row = {'run': 'scan', 'status': 'ready', 'annex_key': 'original'}
    manifest = {'archive_commit': 'a', 'raw_commit': 'b', 'runs': [row]}
    with pytest.raises(ValueError, match='duplicate'):
        summarize(manifest, [dict(row, status='pass'), dict(row, status='pass')])
    with pytest.raises(ValueError, match='source mismatch'):
        summarize(manifest, [dict(row, annex_key='different')])


def test_summary_rejects_unknown_status_and_mixed_detector_versions():
    rows = [{'run': str(i), 'status': 'ready'} for i in range(2)]
    manifest = {'archive_commit': 'a', 'raw_commit': 'b', 'runs': rows}
    with pytest.raises(ValueError, match='status'):
        summarize(manifest, [dict(rows[0], status='skipped')])
    results = [dict(row, status='pass', detector_sha256=str(i)) for i, row in enumerate(rows)]
    with pytest.raises(ValueError, match='detector'):
        summarize(manifest, results)


@pytest.mark.parametrize('archived,replayed,expected', [
    (.8, .8, []), (.5, .8, ['archived-intensity', 'replay-disagreement']),
    (.8, .5, ['replay-disagreement']), (.6, .6, ['archived-intensity']),
    (None, .8, ['invalid-intensity']), (float('nan'), .8, ['invalid-intensity']),
])
def test_archived_intensity_screen_catches_imperfect_replays(archived, replayed, expected):
    from network_fmri.bold_reference_qc import review_reasons
    row = {'status': 'pass', 'intensities': {
        'coreg': {'relative_median': archived}, 'corrected': {'relative_median': replayed}}}
    assert review_reasons(row) == expected
