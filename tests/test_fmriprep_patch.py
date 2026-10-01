import pytest


SOURCE = '''
def anatomical_workflow():
    return AI(transform=('Affine', 0.1))

def init_enhance_and_skullstrip_bold_wf():
    init_aff = AI(transform=('Affine', 0.1), convergence=(10, 1e-6, 10))
    norm = Registration(transform=('Affine', 0.1))
    return init_aff, norm
'''


def test_patch_changes_bold_initializer_and_preserves_affine_refinement():
    from network_fmri.fmriprep_patch import patch_initializer

    patched = patch_initializer(SOURCE)
    namespace = {
        'AI': lambda **kwargs: kwargs,
        'Registration': lambda **kwargs: kwargs,
    }
    exec(patched, namespace)
    initializer, refinement = namespace['init_enhance_and_skullstrip_bold_wf']()
    assert initializer['transform'] == ('Rigid', 0.1)
    assert initializer['convergence'] == (10, 1e-6, 10)
    assert refinement['transform'] == ('Affine', 0.1)
    assert namespace['anatomical_workflow']()['transform'] == ('Affine', 0.1)


@pytest.mark.parametrize('source', [
    SOURCE.replace("AI(transform=('Affine', 0.1),", "AI(transform=('Rigid', 0.1),"),
    SOURCE.replace("AI(transform=('Affine', 0.1),", "AI(transform=('Affine', 0.2),"),
    SOURCE.replace('init_enhance_and_skullstrip_bold_wf', 'different_workflow'),
])
def test_patch_refuses_changed_upstream_initializer(source):
    from network_fmri.fmriprep_patch import patch_initializer

    with pytest.raises(ValueError):
        patch_initializer(source)
