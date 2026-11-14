import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from network_fmri.fmriprep_seed_integration import (
    copy_zip_with_overlay, fieldmap_mapping, rewrite_sources, safe_member, stage_repaired_output,
)


class Unseekable(io.BytesIO):
    def seekable(self):
        return False

    def seek(self, *args):
        raise OSError('unseekable')


@pytest.mark.parametrize('compression', [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
@pytest.mark.parametrize('descriptor', [False, True])
def test_zip_overlay_preserves_anatomy_and_unaffected_payloads(tmp_path, compression, descriptor):
    stream = Unseekable() if descriptor else io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=compression) as archive:
        archive.comment = b'original campaign'
        archive.writestr('fmriprep/sub-s29/anat/original.nii.gz', b'original-anatomy'*200)
        archive.writestr('fmriprep/sub-s29/func/good.nii.gz', b'good-acquisition'*200)
        with archive.open('fmriprep/sub-s29/func/bad.nii.gz', 'w', force_zip64=True) as file:
            file.write(b'damaged')
        with archive.open('fmriprep/sub-s29/anat/zip64.nii.gz', 'w', force_zip64=True) as file:
            file.write(b'unchanged ZIP64 record')
    original = tmp_path/'original.zip'
    original.write_bytes(stream.getvalue())
    fixed = tmp_path/'fixed.nii.gz'
    fixed.write_bytes(b'corrected')
    replacement = {'fmriprep/sub-s29/func/fixed.nii.gz': fixed}
    removed = {'fmriprep/sub-s29/func/bad.nii.gz'}
    output = tmp_path/'corrected.zip'
    proof = copy_zip_with_overlay(original, output, replacement, removed)
    assert proof['final_members'] == 4
    with zipfile.ZipFile(original) as before, zipfile.ZipFile(output) as after:
        assert after.testzip() is None
        assert after.comment == before.comment
        for name in set(before.namelist()) - removed:
            assert before.read(name) == after.read(name)
        assert after.read(next(iter(replacement))) == b'corrected'
        assert not set(after.namelist()) & removed
    assert original.read_bytes() == stream.getvalue()
    with pytest.raises(FileExistsError):
        copy_zip_with_overlay(original, output, replacement, removed)


def test_fieldmaps_and_anatomy_sources_resolve_without_overwriting_shared_originals():
    fmap = 'sub-s29/ses-07/fmap/sub-s29_ses-07_run-1_fmapid-s29ses07_desc-coeff_fieldmap.nii.gz'
    xfm = 'sub-s29/ses-07/func/sub-s29_ses-07_task-rest_run-1_from-boldref_to-s29ses07_mode-image_desc-fmap_xfm.txt'
    first = fieldmap_mapping([fmap, xfm], 16426)
    second = fieldmap_mapping([fmap, xfm], 1953)
    assert not set(first.values()) & set(second.values())
    assert 'to-s29ses07seed16426_' in first[xfm]
    metadata = {'Sources': ['bids::'+fmap, 'bids::'+xfm, 'bids:reuse:sub-s29/anat/original.h5', 'bids:raw:raw.nii.gz'],
                'AssociatedCoefficients': [Path(fmap).name], 'B0FieldIdentifier': 's29_ses-07'}
    changed = rewrite_sources(metadata, first, 16426)
    assert changed['Sources'] == ['bids::'+first[fmap], 'bids::'+first[xfm], 'bids::sub-s29/anat/original.h5', 'bids:raw:raw.nii.gz']
    assert changed['AssociatedCoefficients'] == [Path(first[fmap]).name]
    assert changed['B0FieldIdentifier'] == 's29_ses-07seed16426'
    assert metadata['B0FieldIdentifier'] == 's29_ses-07'
    with pytest.raises(ValueError, match='original anatomy'):
        rewrite_sources('bids:reuse:sub-s29/func/bad.nii.gz', first, 16426)


def test_rerun_extraction_excludes_new_anatomy_and_unselected_acquisitions(tmp_path):
    run = 'sub-s29_ses-07_task-rest_run-1'
    files = {
        f'fmriprep/sub-s29/ses-07/func/{run}_desc-coreg_boldref.nii.gz': b'fixed',
        f'fmriprep/sub-s29/figures/{run}_desc-summary_bold.html': b'new summary',
        'fmriprep/sub-s29/anat/new.nii.gz': b'must not replace original',
        'fmriprep/sub-s29/ses-07/func/sub-s29_ses-07_task-other_run-1_bold.nii.gz': b'not selected',
        'fmriprep/dataset_description.json': b'{"DatasetLinks":{"reuse":"temporary"}}',
    }
    bundle = tmp_path/'derivatives.tar.gz'
    with tarfile.open(bundle, 'w:gz') as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    target = tmp_path/'overlay'
    mapping = stage_repaired_output(bundle, target, run, 16426)
    assert len(mapping) == 2
    assert not (target/'sub-s29/anat').exists()
    assert not (target/'dataset_description.json').exists()
    assert (target/f'sub-s29/ses-07/func/{run}_desc-coreg_boldref.nii.gz').read_bytes() == b'fixed'


@pytest.mark.parametrize('path', ['/absolute/file', 'root/../escape', 'root/./file', 'root//file', 'root\\escape'])
def test_unsafe_overlay_paths_are_rejected(path):
    with pytest.raises(ValueError):
        safe_member(path)


@pytest.mark.parametrize('attempt,proof_digest,allowed', [(1, 'parent', True), (4, 'parent', False),
                                                       (4, 'extended', True), (1, 'unrelated', False)])
def test_extended_plan_accepts_parent_receipts_only_for_original_seeds(tmp_path, attempt, proof_digest, allowed):
    from network_fmri.bold_reference_qc import sha256
    from network_fmri.fmriprep_seed_integration import validated_attempt
    from network_fmri.fmriprep_seed_rerun import derived_seeds
    seeds = [derived_seeds(i) for i in range(1, 5)]
    chosen = seeds[attempt-1]
    run = 'sub-s29_ses-07_task-rest_run-1'
    (tmp_path/'after.json').write_text(json.dumps({'run': run, 'seeds': chosen, 'status': 'pass'}))
    (tmp_path/'derivatives.tar.gz').write_bytes(b'archive')
    (tmp_path/'provenance.json').write_text(json.dumps({'run': run, 'seeds': chosen, 'plan_sha256': proof_digest}))
    (tmp_path/'receipt.json').write_text(json.dumps({'seeds': chosen, 'automatic_checks_passed': True,
        'after_sha256': sha256(tmp_path/'after.json'), 'derivatives_sha256': sha256(tmp_path/'derivatives.tar.gz')}))
    planned = {'before': {'run': run}, 'attempt_seeds': seeds}
    if allowed:
        assert validated_attempt(tmp_path, planned, 'extended', 'parent')[0]['seeds'] == chosen
    else:
        with pytest.raises(ValueError, match='unrelated'):
            validated_attempt(tmp_path, planned, 'extended', 'parent')


def test_complete_subject_build_keeps_original_anatomy_and_good_runs(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from network_fmri.bold_reference_qc import sha256
    from network_fmri.fmriprep_seed_integration import build_subject
    run = 'sub-s29_ses-07_task-rest_run-1'
    root = 'fMRIPrep-25.2.7-rc1+full'
    anatomy = 'sub-s29/anat/sub-s29_desc-preproc_T1w.nii.gz'
    repaired = f'sub-s29/ses-07/func/{run}_desc-coreg_boldref.nii.gz'
    good = 'sub-s29/ses-07/func/sub-s29_ses-07_task-good_run-1_desc-preproc_bold.nii.gz'
    original = tmp_path/'MD5E-s100--test.zip'
    with zipfile.ZipFile(original, 'w') as archive:
        for name, data in {anatomy: b'original anatomy', repaired: b'bad reference', good: b'good data',
                           'sub-s29/figures/'+run+'_desc-old_bold.svg': b'old reportlet'}.items():
            archive.writestr(root+'/'+name, data)
    source = tmp_path/'sub-s29_fMRIPrep.zip'
    source.symlink_to(original)
    seeds = {'master': 16426, 'ants': 47450, 'numpy': 45715}
    entry = {'before': {'subject': 's29', 'run': run, 'archive': str(source), 'annex_key': original.name},
             'attempt_seeds': [seeds]}
    plan = tmp_path/'plan.json'
    plan.write_text(json.dumps({'runs': [entry]}))
    attempt = tmp_path/'attempt-2'
    attempt.mkdir()
    (attempt/'evidence').mkdir()
    (attempt/'evidence'/'review.png').write_bytes(b'visual evidence')
    after = {'run': run, 'seeds': seeds, 'status': 'pass'}
    (attempt/'after.json').write_text(json.dumps(after))
    with tarfile.open(attempt/'derivatives.tar.gz', 'w:gz') as archive:
        for name, data in {repaired: b'fixed reference', anatomy: b'do not replace',
                           'sub-s29/figures/'+run+'_desc-new_bold.svg': b'new reportlet'}.items():
            member = tarfile.TarInfo('fmriprep/'+name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    receipt = {'seeds': seeds, 'automatic_checks_passed': True, 'attempt': 2,
               'after_sha256': sha256(attempt/'after.json'),
               'derivatives_sha256': sha256(attempt/'derivatives.tar.gz')}
    (attempt/'receipt.json').write_text(json.dumps(receipt))
    (attempt/'provenance.json').write_text(json.dumps({'run': run, 'seeds': seeds,
        'plan_sha256': sha256(plan), 'anatomy_evidence': [], 'surface_proof': {}}))
    attempts = tmp_path/'attempts.json'
    attempts.write_text(json.dumps({run: str(attempt)}))
    image = tmp_path/'image.sif'
    image.write_bytes(b'image')
    output = tmp_path/'final'
    output.mkdir()
    local = tmp_path/'local'
    local.mkdir()
    monkeypatch.setenv('SLURM_JOB_ID', 'unit-test')
    monkeypatch.setenv('L_SCRATCH_JOB', str(local))

    def render(command, **kwargs):
        spec = json.loads(Path(command[-2]).read_text())
        reports = Path(spec['report_root'])
        assert not (reports/'sub-s29/figures'/f'{run}_desc-old_bold.svg').exists()
        assert (reports/'sub-s29/figures'/f'{run}_desc-new_bold.svg').read_bytes() == b'new reportlet'
        filename = 'sub-s29_ses-07_func.html'
        (reports/filename).write_text('rebuilt session report')
        Path(command[-1]).write_text(json.dumps({filename: sha256(reports/filename)}))
    monkeypatch.setattr('network_fmri.fmriprep_seed_integration.subprocess.run', render)
    args = SimpleNamespace(plan=plan, attempts=attempts, subject='s29', output=output,
                           image=image, image_sha256=sha256(image), code=tmp_path)
    build_subject(args)
    with zipfile.ZipFile(output/source.name) as archive:
        assert archive.testzip() is None
        assert archive.read(root+'/'+anatomy) == b'original anatomy'
        assert archive.read(root+'/'+good) == b'good data'
        assert archive.read(root+'/'+repaired) == b'fixed reference'
        assert archive.read(root+'/sub-s29_ses-07_func.html') == b'rebuilt session report'
        assert archive.read(root+'/code/seed-repairs/'+run+'/evidence/review.png') == b'visual evidence'
    with zipfile.ZipFile(source) as archive:
        assert archive.read(root+'/'+repaired) == b'bad reference'
    with pytest.raises(ValueError, match='overwrite'):
        build_subject(args)
