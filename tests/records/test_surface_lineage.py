import json


def test_surface_lineage_uses_exact_reconstruction_inputs_not_dataset_dependency(tmp_path):
    from network_fmri.records.native_lineage import collect_surface_lineage
    path = tmp_path / 'code/network_fmri/surface-evidence.json'
    path.parent.mkdir(parents=True)
    value = {'schema_version': 1, 'inventories': {'s03': {'mri/ribbon.mgz': 'b'*64}},
             'reconstructions': {'s03': {'build': 'freesurfer-8.2.0',
                 'inputs': [{'path': 'sub-s03/anat/sub-s03_T1w.nii.gz', 'sha256': 'a'*64}]}}}
    path.write_text(json.dumps(value))
    receipt, = collect_surface_lineage(tmp_path, 'surfaces', 'raw')
    source, output = receipt['artifacts']
    assert source['dataset_id'] == 'raw'
    assert source['content_id'] == 'sha256:' + 'a'*64
    assert output['path'] == 'subjects/sub-s03/mri/ribbon.mgz'
    assert receipt['links'][0]['relation'] == 'freesurfer'
    del value['reconstructions']
    path.write_text(json.dumps(value))
    assert collect_surface_lineage(tmp_path, 'surfaces', 'raw') == ()
