"""Final approval is committed, subject-specific and invalidated by changed evidence."""
import json
from types import SimpleNamespace

import pytest


def test_record_and_reject_require_current_evidence(tmp_path, monkeypatch):
    from network_fmri import output_review as review
    config = SimpleNamespace(subjects=('s03',), mechababs=SimpleNamespace(study_dir=tmp_path))
    proof = {'source_project': 'derivatives/fMRIPrep-current', 'source_commit': 'abc'}
    monkeypatch.setattr(review, 'current_evidence', lambda *args, **kw: (proof, []))
    from network_fmri import processing
    monkeypatch.setattr(processing, 'ProcessingManager', lambda *a, **kw: SimpleNamespace(plan=lambda: [SimpleNamespace(stage='fmriprep', state='complete', project=proof['source_project'])]))
    calls = []
    def runner(command, **kw):
        calls.append(command)
    path = review.record_review(config, 's03', 'approved', 'LB', 'Reviewed reports', runner=runner)
    value = json.loads(path.read_text())
    assert value['decision'] == 'approved' and value['reviewer'] == 'LB'
    assert value['inputs'] == proof and value['reviewed_at']
    assert calls[-1][-1] == str(path) and calls[-1][0:2] == ('datalad', 'save')
    with pytest.raises(ValueError, match='notes'):
        review.record_review(config, 's03', 'needs-correction', 'LB', '', runner=runner)
    monkeypatch.setattr(review, 'current_evidence', lambda *args, **kw: (proof, ['bad TR']))
    with pytest.raises(RuntimeError, match='bad TR'):
        review.record_review(config, 's03', 'approved', 'LB', '', runner=runner)
    review.record_review(config, 's03', 'needs-correction', 'LB', 'Inspect TR', runner=runner)
    assert json.loads(path.read_text())['decision'] == 'needs-correction'
    with pytest.raises(ValueError, match='subject'):
        review.record_review(config, 's04', 'approved', 'LB', '', runner=runner)


def test_changed_or_uncommitted_review_is_not_approval(tmp_path, monkeypatch):
    from network_fmri import output_review as review
    path = review.review_path(tmp_path, 's03')
    path.parent.mkdir(parents=True)
    value = {'subject':'s03', 'decision':'approved', 'reviewer':'LB', 'reviewed_at':'now',
             'inputs': {'source_project':'derivatives/fMRIPrep-current', 'source_commit':'abc'}}
    path.write_text(json.dumps(value))
    monkeypatch.setattr(review, '_committed_json', lambda *a, **kw: value)
    monkeypatch.setattr(review, 'current_evidence', lambda *a, **kw: (value['inputs'], []))
    assert review.read_review(tmp_path, 's03')['decision'] == 'approved'
    monkeypatch.setattr(review, 'current_evidence', lambda *a, **kw: ({**value['inputs'], 'source_commit':'new'}, []))
    assert review.read_review(tmp_path, 's03') is None
    monkeypatch.setattr(review, '_committed_json', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('uncommitted')))
    assert review.read_review(tmp_path, 's03') is None


def test_real_git_evidence_cannot_reuse_approval_after_outputs_change(tmp_path):
    import subprocess
    from network_fmri import output_review as review
    from network_fmri.milestones import write_json_atomic

    def git(root, *args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    def initialize(root):
        root.mkdir(parents=True, exist_ok=True)
        git(root, 'init', '-q')
        git(root, 'config', 'user.email', 'test@example.org')
        git(root, 'config', 'user.name', 'Test')
    def commit(root):
        git(root, 'add', '.')
        git(root, 'commit', '-qm', 'evidence')
        return git(root, 'rev-parse', 'HEAD')
    initialize(tmp_path)
    source_name = 'derivatives/fMRIPrep-test'
    source = tmp_path / source_name
    initialize(source)
    (source / 'output.txt').write_text('original')
    source_commit = commit(source)
    checks_root = source.with_name(source.name + '+review')
    viz_root = source.with_name('fmriprepviz-0.1.0-test')
    for root, name, value in (
        (checks_root, 'fmriprep-evidence.json', {'status':'success', 'inputs':{'source_commit':source_commit},
                                             'subjects':[{'subject':'s03', 'issues':[]}]}),
        (viz_root, 'registration-qc.json', {'status':'success', 'inputs':{'source_commit':source_commit,
                                              'source_project':source_name, 'subjects':[{'subject':'s03'}]}})):
        initialize(root)
        write_json_atomic(root / 'code/network_fmri' / name, value)
        commit(root)
    for root in (source, checks_root, viz_root):
        git(tmp_path, 'update-index', '--add', '--cacheinfo', '160000', git(root,'rev-parse','HEAD'), root.relative_to(tmp_path).as_posix())
    git(tmp_path, 'commit', '-qm', 'register outputs')
    inputs, issues = review.current_evidence(tmp_path, source_name, 's03')
    assert not issues
    path = review.review_path(tmp_path, 's03')
    write_json_atomic(path, {'subject':'s03', 'decision':'approved', 'reviewer':'LB', 'reviewed_at':'now',
                            'notes':'reviewed', 'inputs':inputs})
    assert review.read_review(tmp_path, 's03') is None  # Uncommitted approval.
    commit(tmp_path)
    assert review.read_review(tmp_path, 's03')['decision'] == 'approved'
    new_report = source / 'new-report.html'
    new_report.write_text('unreviewed')
    assert review.read_review(tmp_path, 's03') is None
    new_report.unlink()
    (source / 'output.txt').write_text('changed')
    assert review.read_review(tmp_path, 's03') is None
    commit(source)
    assert review.read_review(tmp_path, 's03') is None


@pytest.mark.parametrize('value', [None, [], {'inputs':None}, {'inputs':[]}, {'inputs':{}}])
def test_malformed_committed_review_is_not_approval(tmp_path, monkeypatch, value):
    from network_fmri import output_review as review
    path = review.review_path(tmp_path,'s03')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(value))
    monkeypatch.setattr(review,'_committed_json',lambda *a, **kw:value)
    assert review.read_review(tmp_path,'s03') is None
