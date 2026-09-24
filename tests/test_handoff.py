"""The MRIQC controller stops at review and never replaces human decisions."""
import json
from types import SimpleNamespace

import pytest

from network_fmri import handoff
from network_fmri.models import StageResult


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    config = SimpleNamespace(
        mechababs=SimpleNamespace(study_dir=tmp_path / 'study'),
        paths=SimpleNamespace(bids_dir=tmp_path / 'raw'),
    )
    evidence = tmp_path / 'evidence'
    calls = []
    monkeypatch.setattr(handoff, '_lock_path', lambda root: root / '.git/handoff.lock')
    monkeypatch.setattr(handoff, 'prepare_mriqc_review',
                        lambda c: calls.append('extract') or SimpleNamespace(evidence_dir=evidence))
    def generate(raw, *, mriqc_dir, output):
        calls.append('generate')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('flags\tdecision\n\treview\n')
        output.with_suffix('.meta.json').write_text(json.dumps({'identity': 'same'}))
        return StageResult('scan-decisions-generated', (output, output.with_suffix('.meta.json')))
    monkeypatch.setattr(handoff, 'generate_decisions', generate)
    monkeypatch.setattr(handoff, 'save_stage_result', lambda *a: calls.append('save'))
    return config, calls


class Manager:
    def __init__(self, states):
        self.states = iter(states)
        self.advances = []

    def plan(self):
        return [SimpleNamespace(stage='mriqc', state=next(self.states))]

    def advance(self, stage):
        self.advances.append(stage)


def test_runs_to_review_without_advancing_anatomy(workflow):
    config, calls = workflow
    manager = Manager(['ready', 'active', 'complete'])
    sleeps = []
    result = handoff.run_mriqc(config, manager=manager, sleep=sleeps.append, interval=1)
    assert result['state'] == 'awaiting-scan-review'
    assert manager.advances == ['mriqc', 'mriqc']
    assert sleeps == [1, 1]
    assert calls == ['extract', 'generate', 'save']


def test_failed_jobs_never_extract_or_retry(workflow):
    config, calls = workflow
    manager = Manager(['intervention-required'])
    with pytest.raises(RuntimeError, match='intervention-required'):
        handoff.run_mriqc(config, manager=manager)
    assert not calls and not manager.advances


def test_existing_review_is_preserved(workflow):
    config, calls = workflow
    first = handoff.run_mriqc(config, manager=Manager(['complete']))
    from pathlib import Path
    manifest = Path(first['manifest'])
    manifest.write_text('flags\tdecision\n\tkeep\n')
    calls.clear()
    result = handoff.run_mriqc(config, manager=Manager(['complete']))
    assert manifest.read_text().endswith('\tkeep\n')
    assert result['created'] is False
    assert 'save' not in calls


def test_stale_evidence_never_overwrites_review(workflow):
    config, calls = workflow
    from pathlib import Path
    first = handoff.run_mriqc(config, manager=Manager(['complete']))
    manifest = Path(first['manifest'])
    manifest.with_suffix('.meta.json').write_text('{"identity":"old"}')
    original = manifest.read_bytes()
    with pytest.raises(RuntimeError, match='evidence changed'):
        handoff.run_mriqc(config, manager=Manager(['complete']))
    assert manifest.read_bytes() == original


def test_partial_review_pair_is_not_overwritten(workflow):
    config, calls = workflow
    path = config.mechababs.study_dir / 'code/network_fmri/scan_decisions.tsv'
    path.parent.mkdir(parents=True)
    path.write_text('human work')
    with pytest.raises(RuntimeError, match='incomplete'):
        handoff.run_mriqc(config, manager=Manager(['complete']))
    assert path.read_text() == 'human work'


def test_threshold_mismatch_stops_as_evidence_error(workflow, monkeypatch):
    config, calls = workflow
    original = handoff.generate_decisions
    def generate(*a, **kw):
        result = original(*a, **kw)
        kw['output'].write_text('flags\tdecision\nfd_thres_mismatch\treview\n')
        return result
    monkeypatch.setattr(handoff, 'generate_decisions', generate)
    result = handoff.run_mriqc(config, manager=Manager(['complete']))
    assert result['state'] == 'evidence-error'
    assert result['flags'] == {'fd_thres_mismatch': 1}


def test_second_controller_cannot_write(workflow):
    import fcntl
    config, calls = workflow
    lock = handoff._lock_path(config.mechababs.study_dir)
    lock.parent.mkdir(parents=True)
    with lock.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match='another MRIQC'):
            handoff.run_mriqc(config, manager=Manager(['complete']))
    assert not calls


def test_modified_evidence_columns_are_rejected(workflow):
    from pathlib import Path
    config, calls = workflow
    result = handoff.run_mriqc(config, manager=Manager(['complete']))
    manifest = Path(result['manifest'])
    manifest.write_text('flags\tdecision\ntampered\treview\n')
    with pytest.raises(RuntimeError, match='evidence changed'):
        handoff.run_mriqc(config, manager=Manager(['complete']))
