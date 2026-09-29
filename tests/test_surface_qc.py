"""FSQC evidence must be complete and pinned before it can support review."""
import pytest


def output(root, subject='s03'):
    status=root/f'status/sub-{subject}/status.txt';status.parent.mkdir(parents=True)
    status.write_text('metrics:0\nscreenshots:0\nsurfaces:0\nshape:2\n')
    for folder in ('screenshots','surfaces'):
        p=root/f'{folder}/sub-{subject}/view.png';p.parent.mkdir(parents=True);p.write_bytes(b'PNG')
    (root/'fsqc-results.csv').write_text(f'subject,wm_snr_orig,holes_lh,holes_rh\nsub-{subject},12.5,3,4\n')


def test_metrics_and_render_status_required(tmp_path):
    from network_fmri.surface_qc import validate_outputs
    output(tmp_path)
    assert validate_outputs(tmp_path,('s03',))['s03']['holes_lh']==3
    (tmp_path/'status/sub-s03/status.txt').write_text('metrics:0\nscreenshots:1\nsurfaces:0\n')
    with pytest.raises(RuntimeError,match='screenshots'):
        validate_outputs(tmp_path,('s03',))


def test_missing_subject_is_not_success(tmp_path):
    from network_fmri.surface_qc import validate_outputs
    output(tmp_path)
    with pytest.raises(RuntimeError,match='subjects'):
        validate_outputs(tmp_path,('s03','s10'))


def test_nonfinite_metrics_are_json_null(tmp_path):
    from network_fmri.surface_qc import validate_outputs
    output(tmp_path)
    p=tmp_path/'fsqc-results.csv';p.write_text(p.read_text().replace('12.5','nan'))
    assert validate_outputs(tmp_path,('s03',))['s03']['wm_snr_orig'] is None


def test_render_missing_despite_zero_status_blocks(tmp_path):
    from network_fmri.surface_qc import validate_outputs
    output(tmp_path)
    (tmp_path/'surfaces/sub-s03/view.png').unlink()
    with pytest.raises(RuntimeError,match='surfaces'):
        validate_outputs(tmp_path,('s03',))


def test_pinned_derivative_restarts_without_rendering_and_rejects_changes(tmp_path):
    import json
    from types import SimpleNamespace
    from pathlib import Path
    from network_fmri.surface_qc import prepare_surface_qc, RECEIPT
    from network_fmri.mriqc import _sha256
    from tests.test_mriqc_evidence import Commands, init, save, register_source
    study=tmp_path/'study';surface=study/'derivatives/FreeSurfer-8.2.0+test+review'
    init(study,'study');init(surface,'surface')
    anatomy=surface/'subjects/sub-s03/mri/norm.mgz';anatomy.parent.mkdir(parents=True);anatomy.write_bytes(b'anatomy')
    receipt=surface/'code/network_fmri/surface-evidence.json';receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({'source_project':'derivatives/FreeSurfer-8.2.0+test','inventories':{'s03':{'mri/norm.mgz':_sha256(anatomy)}}}))
    save(surface);save(study)
    image=tmp_path/'fsqc.sif';image.write_bytes(b'image')
    config=SimpleNamespace(subjects=('s03',),mechababs=SimpleNamespace(study_dir=study),paths=SimpleNamespace(log_dir=tmp_path/'logs'),fsqc=SimpleNamespace(image=image,version='2.1.4',sha256=_sha256(image)))
    register_source(config,surface)
    class Runner(Commands):
        def __call__(self,command,**kwargs):
            if command[0]=='singularity':
                self.calls.append(command)
                mount=command[command.index('--bind')+1]
                assert f'{surface}:/input:ro' in mount
                output(Path(mount.split(',')[1].removesuffix(':/output')))
                return SimpleNamespace(returncode=0)
            return super().__call__(command,**kwargs)
    runner=Runner();dest=prepare_surface_qc(config,surface/'subjects',runner=runner)
    assert json.loads((dest/RECEIPT).read_text())['subjects']['s03']['holes_lh']==3
    assert prepare_surface_qc(config,surface/'subjects',runner=runner)==dest
    assert len([c for c in runner.calls if c[0]=='singularity'])==1
    (dest/'screenshots/sub-s03/view.png').write_bytes(b'changed');save(dest);register_source(config,dest)
    with pytest.raises(RuntimeError,match='output changed'):
        prepare_surface_qc(config,surface/'subjects',runner=runner)
