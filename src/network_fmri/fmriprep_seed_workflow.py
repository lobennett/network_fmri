"""Exact-image preflight and direct measurements for isolated seed repairs.

These commands run inside the pinned fMRIPrep image on a Slurm compute node.
They inspect upstream workflows; they do not change registration algorithms.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tomllib

import nibabel as nib

from network_fmri.bold_reference_qc import (
    VERSION, SUFFIXES, _run_key, _same_grid, classify, intensity_metrics, montage, sha256, weight_metrics,
)
from network_fmri.fmriprep_seed_rerun import run_filter, validate_anatomical_cache


def preflight(spec):
    from fmriprep import config
    from fmriprep.cli.parser import parse_args
    from fmriprep.cli.workflow import build_workflow
    from smriprep.utils.bids import collect_derivatives

    anatomy = Path(spec['anatomy'])
    cache = collect_derivatives(anatomy, spec['subject'],
                               ['MNI152NLin2009cAsym', 'MNI152NLin6Asym', 'fsnative'])
    validate_anatomical_cache(cache, anatomy, spec['anatomy_evidence'])
    parse_args(spec['command'][1:])
    if config.environment.version != VERSION or config.seeds.get() != spec['seeds']:
        raise ValueError('container version or initialized seeds differ from provenance')
    selected = config.execution.layout.get(return_type='filename', suffix='bold',
        extension='.nii.gz', **run_filter(spec['run'])['bold'])
    if not selected or {_run_key(p) for p in selected} != {spec['run']}:
        raise ValueError('BIDS filter did not select exactly the requested acquisition')
    if sorted(selected) != sorted(spec['bold_files']):
        raise ValueError('BIDS filter omitted an echo or selected unexpected data')
    config_file = config.execution.work_dir / 'preflight.toml'
    config.to_filename(config_file)
    result = build_workflow(str(config_file), {})
    if result['return_code'] or result['workflow'] is None:
        raise RuntimeError('upstream full-app workflow could not be built')
    wf = result['workflow']
    names = wf.list_node_names()
    anat_names = [n for n in names if 'anat_fit_wf' in n.split('.')]
    forbidden = {'anat_template_wf', 'brain_extraction_wf', 'register_template_wf',
                 'fast', 'msm_sulc_wf', 'fsLR_reg_wf'}
    if not anat_names or any(forbidden.intersection(n.split('.')) for n in anat_names):
        raise ValueError('original anatomical/template fits would be recomputed')
    def node(suffix):
        matches = [n for n in names if n.endswith(suffix)]
        if len(matches) != 1:
            raise ValueError(f'expected exactly one workflow node: {suffix}')
        return wf.get_node(matches[0])
    checks = {
        't1w': node('anat_fit_wf.anat_validate').inputs.in_file == cache['t1w_preproc'],
        'mask': node('anat_fit_wf.t1w_buffer').inputs.t1w_mask == cache['t1w_mask'],
        'dseg': node('anat_fit_wf.seg_buffer').inputs.t1w_dseg == cache['t1w_dseg'],
        'tpms': node('anat_fit_wf.seg_buffer').inputs.t1w_tpms == cache['t1w_tpms'],
        'fsnative': node('anat_fit_wf.fs2anat_buffer').inputs.fsnative2anat_xfm == cache['transforms']['fsnative']['reverse'],
    }
    templates = node('anat_fit_wf.template_buffer').inputs.in1
    checks['template_spaces'] = set(templates) == {'MNI152NLin2009cAsym', 'MNI152NLin6Asym'}
    for index, template in enumerate(templates):
        for buffer, direction in [('anat2std_buffer', 'forward'), ('std2anat_buffer', 'reverse')]:
            checks[template + direction] = node('anat_fit_wf.' + buffer).inputs.in1[index] == cache['transforms'][template][direction]
    for key in ('white', 'pial', 'midthickness', 'sphere', 'thickness', 'sulc'):
        checks[key] = getattr(node('anat_fit_wf.surfaces_buffer').inputs, key) == sorted(cache[key])
    for buffer, key in [('fsLR_buffer', 'sphere_reg_fsLR'), ('msm_buffer', 'sphere_reg_msm')]:
        checks[key] = getattr(node('anat_fit_wf.' + buffer).inputs, key) == sorted(cache[key])
    checks['cortex_mask'] = node('anat_fit_wf.outputnode').inputs.cortex_mask == sorted(cache['cortex_mask'])
    checks['all_echoes'] = sorted(node('.merge_bold_sources').inputs.in1) == sorted(selected)
    initializer = node('.enhance_and_skullstrip_bold_wf.init_aff')
    checks['rigid_initializer'] = tuple(initializer.inputs.transform) == ('Rigid', .1)
    node('.enhance_and_skullstrip_bold_wf.n4_correct')
    if not all(checks.values()):
        raise ValueError(f'workflow reuse failed: {checks}')
    return {'checks': checks, 'nodes': names, 'cache': cache,
            'bold_files': selected, 'seeds': config.seeds.get(),
            't2w_recomputed': any('t2w_template_wf' in n for n in names)}


def measure(spec):
    """Measure the actual completed N4 invocation, without a second random replay."""
    from nipype.interfaces import afni
    from nipype.utils.filemanip import loadpkl

    output, work, target = (Path(spec[k]) for k in ('output', 'work', 'evidence'))
    target.mkdir(exist_ok=False)
    paths = {}
    for label, suffix in SUFFIXES.items():
        matches = list(output.glob('**/' + spec['run'] + suffix))
        if len(matches) != 1:
            raise ValueError(f'missing or ambiguous repaired {label}')
        paths[label] = matches[0]
    results = list(work.glob('**/enhance_and_skullstrip_bold_wf/n4_correct/result_n4_correct.pklz'))
    if len(results) != 1:
        raise ValueError('expected one actual BOLD reference N4 result')
    n4 = loadpkl(str(results[0]))
    weight = Path(n4.inputs['weight_image'])
    paths['corrected'] = Path(n4.outputs.output_image)
    automask = afni.Automask(in_file=str(paths['hmc']), out_file=str(target/'automask.nii.gz'),
                            outputtype='NIFTI_GZ', dilate=1, num_threads=1).run(cwd=str(target))
    paths['archived_mask'], paths['mask'] = paths['mask'], Path(automask.outputs.out_file)
    metrics = weight_metrics(nib.load(weight), nib.load(paths['mask']))
    intensities = {key: intensity_metrics(nib.load(paths[key]), nib.load(paths['mask']))
                   for key in ('hmc', 'corrected', 'coreg')}
    intensities['t1w'] = intensity_metrics(nib.load(paths['t1w']), nib.load(paths['t1w_mask']))
    configs = list(output.glob(f"sub-{spec['subject']}/log/*/fmriprep.toml"))
    if len(configs) != 1 or tomllib.loads(configs[0].read_text())['seeds'] != spec['seeds']:
        raise ValueError('completed app did not use the recorded fresh seeds')
    for label in ('hmc', 'corrected', 'coreg'):
        shutil.copyfile(paths[label], target / (label + '.nii.gz'))
    shutil.copyfile(weight, target / 'weight.nii.gz')
    shutil.copyfile(configs[0], target / 'fmriprep.toml')
    t2w = []
    for item in spec['anatomy_evidence']:
        if not item['path'].endswith('_desc-preproc_T2w.nii.gz'):
            continue
        original = Path(spec['anatomy']) / item['path']
        matches = list(output.glob('**/' + original.name))
        if len(matches) != 1:
            raise ValueError('expected recomputed T2w output for comparison')
        before_image, after_image = nib.load(original), nib.load(matches[0])
        _same_grid(before_image, after_image)
        import numpy as np
        before_data, after_data = before_image.get_fdata(), after_image.get_fdata()
        if not np.isfinite(after_data).all():
            raise ValueError('nonfinite recomputed T2w')
        t2w.append({'path': item['path'], 'original_sha256': item['sha256'],
                    'rerun_sha256': sha256(matches[0]), 'same_grid': True,
                    'mean_absolute_difference': float(np.abs(before_data-after_data).mean())})
    montage(paths, weight, target/'review.png', spec['run'] + ': actual rerun N4 weights',
            n4_label='Actual N4', coreg_label='Repaired coreg')
    return {'run': spec['run'], 'status': classify(metrics), 'metrics': metrics,
            'intensities': intensities, 'seeds': spec['seeds'],
            't2w_comparison': t2w,
            'measurement': 'actual completed N4 result, independent AFNI HMC automask dilate=1',
            'automask_command': automask.runtime.cmdline, 'n4_command': n4.runtime.cmdline,
            'sha256': {p.name: sha256(p) for p in target.iterdir() if p.is_file()}}


def reports(spec):
    """Rebuild affected session reports from original and replaced reportlets."""
    from fmriprep import data
    from fmriprep.reports.core import run_reports
    root = Path(spec['report_root'])
    generated = {}
    for session in spec['sessions']:
        filename = f"sub-{spec['subject']}_ses-{session}_func.html"
        error = run_reports(root, spec['subject'], spec['run_uuid'],
                            bootstrap_file=data.load('reports-spec-func.yml'),
                            out_filename=filename, reportlets_dir=root,
                            subject=spec['subject'], session=session)
        if error or not (root/filename).is_file():
            raise RuntimeError(f'corrected report generation failed: {filename}')
        generated[filename] = sha256(root/filename)
    return generated


def main():
    if not os.environ.get('SLURM_JOB_ID'):
        raise RuntimeError('preflight and measurement require a Slurm allocation')
    action, spec_file, receipt = sys.argv[1:]
    spec = json.loads(Path(spec_file).read_text())
    result = {'preflight': preflight, 'measure': measure, 'reports': reports}[action](spec)
    with Path(receipt).open('x') as stream:
        json.dump(result, stream, indent=2)


if __name__ == '__main__':
    main()
