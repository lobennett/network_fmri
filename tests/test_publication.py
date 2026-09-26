"""Publishing preserves dataset identity and refuses divergent work."""
from pathlib import Path
from types import SimpleNamespace
import pytest


def test_publisher_refuses_dirty_study_before_copy(tmp_path, monkeypatch):
    from network_fmri import publication
    study = tmp_path/'study'; study.mkdir()
    config = SimpleNamespace(mechababs=SimpleNamespace(study_dir=study,durable_sibling=tmp_path/'oak',raw_slot='raw'))
    calls=[]
    def runner(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(stdout=' M important.json\n')
    with pytest.raises(RuntimeError,match='uncommitted'):
        publication.publish_study(config,runner=runner)
    assert all(c[0]=='git' for c in calls)


def test_existing_destination_must_match_identity_and_fast_forward(tmp_path):
    from network_fmri import publication
    source=tmp_path/'source'; target=tmp_path/'target'; source.mkdir();target.mkdir();(target/'.git').mkdir()
    calls=[]
    def runner(cmd, **kw):
        calls.append(cmd)
        args=cmd[1:]
        if 'datalad.dataset.id' in args:return SimpleNamespace(stdout='source-id' if kw['cwd']==str(source) else 'other-id')
        if 'rev-parse' in args:return SimpleNamespace(stdout='revision')
        return SimpleNamespace(stdout='')
    with pytest.raises(RuntimeError,match='identity'):
        publication.publish_dataset(source,target,runner=runner)
    assert not any(c[0]=='datalad' for c in calls)


def test_wrong_oak_remote_stops_before_publication(tmp_path):
    from network_fmri import publication
    study=tmp_path/'study';study.mkdir()
    config=SimpleNamespace(mechababs=SimpleNamespace(study_dir=study,durable_sibling=tmp_path/'oak',raw_slot='raw'))
    calls=[]
    def runner(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(stdout=str(tmp_path/'wrong') if 'get-url' in cmd else '')
    with pytest.raises(RuntimeError,match='remote'):
        publication.publish_study(config,runner=runner)
    assert all(c[0]=='git' for c in calls)
