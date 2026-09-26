"""Campaign controller advances only when the corresponding human gate passes."""
from types import SimpleNamespace

import pytest


class Manager:
    def __init__(self, states):
        self.states = states
        self.advanced = []

    def plan(self):
        return [SimpleNamespace(stage=name, state=state) for name, state in self.states.items()]

    def advance(self, stage):
        self.advanced.append(stage)
        self.states[stage] = "complete"

    def dependencies(self, stage):
        return ("mriqc", "anatomical") if stage == "fmriprep" else ()


@pytest.fixture
def config(tmp_path, monkeypatch):
    from network_fmri import handoff
    monkeypatch.setattr(handoff, "_lock_path", lambda root: root / ".git/handoff.lock")
    monkeypatch.setattr(handoff, "_refresh_records", lambda *args: None, raising=False)
    return SimpleNamespace(mechababs=SimpleNamespace(study_dir=tmp_path))


def test_surface_pause_does_not_submit_fmriprep(config):
    from network_fmri.handoff import run_processing
    manager = Manager({"mriqc": "complete", "anatomical": "complete", "fmriprep": "ready"})
    def review(stage):
        return {"state": "awaiting-surface-review"} if stage == "anatomical" else None
    result = run_processing(config, manager=manager, prepare_review=review, sleep=lambda _: None)
    assert result["state"] == "awaiting-surface-review"
    assert manager.advanced == []


def test_restart_skips_merged_jobs_and_advances_approved_work(config):
    from network_fmri.handoff import run_processing
    manager = Manager({"mriqc": "complete", "anatomical": "ready", "fmriprep": "blocked"})
    def sleep(_):
        if manager.states["anatomical"] == "complete" and manager.states["fmriprep"] == "blocked":
            manager.states["fmriprep"] = "ready"
    result = run_processing(config, manager=manager, prepare_review=lambda _: None, sleep=sleep)
    assert manager.advanced == ["anatomical", "fmriprep"]
    assert result["state"] == "awaiting-output-review"


def test_failure_does_not_retry_or_prepare_review(config):
    from network_fmri.handoff import run_processing
    manager = Manager({"mriqc": "intervention-required", "anatomical": "blocked", "fmriprep": "blocked"})
    with pytest.raises(RuntimeError, match="intervention-required"):
        run_processing(config, manager=manager, prepare_review=lambda _: pytest.fail("unexpected review"))
    assert not manager.advanced


def test_controller_refreshes_records_after_review_preparation(config):
    from network_fmri.handoff import run_processing
    events = []
    manager = Manager({"mriqc": "complete"})
    def prepare(stage):
        events.append("review")
        return {"state": "awaiting-scan-review"}
    run_processing(config, manager=manager, prepare_review=prepare,
                   observe=lambda: events.append("refresh"))
    assert events == ["refresh", "review", "refresh"]


def test_independent_anatomy_finishes_while_scan_review_is_pending(config):
    from network_fmri.handoff import run_processing
    manager = Manager({"mriqc": "complete", "anatomical": "ready", "fmriprep": "blocked"})
    def review(stage):
        return {"state": "awaiting-scan-review" if stage == "mriqc" else "awaiting-surface-review"}
    result = run_processing(config, manager=manager, prepare_review=review, sleep=lambda _: None)
    assert manager.advanced == ["anatomical"]
    assert result["state"] == "awaiting-reviews"
    assert len(result["reviews"]) == 2


def test_both_independent_jobs_advance_before_poll_sleep(config):
    from network_fmri.handoff import run_processing
    manager = Manager({"mriqc": "ready", "anatomical": "ready", "fmriprep": "blocked"})
    def sleep(_):
        assert manager.advanced == ["mriqc", "anatomical"]
    run_processing(config, manager=manager,
        prepare_review=lambda stage: {"state": "awaiting-review", "stage": stage}, sleep=sleep)


@pytest.mark.parametrize('decision,state', [('approved','complete'), ('needs-correction','output-correction-required'), (None,'awaiting-output-review')])
def test_final_boundary_respects_recorded_subject_review(config, monkeypatch, decision, state):
    import json
    from network_fmri import handoff, fmriprep_evidence, registration_qc, output_review
    config.subjects = ('s03',)
    receipt = config.mechababs.study_dir / fmriprep_evidence.RECEIPT
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({'status':'success'}))
    registration = config.mechababs.study_dir / registration_qc.RECEIPT
    registration.parent.mkdir(parents=True, exist_ok=True)
    registration.write_text(json.dumps({'inputs':{'source_project':'derivatives/fMRIPrep-current'}}))
    monkeypatch.setattr(fmriprep_evidence,'prepare_fmriprep_review',lambda c: config.mechababs.study_dir)
    monkeypatch.setattr(registration_qc,'prepare_registration_qc',lambda c: config.mechababs.study_dir)
    monkeypatch.setattr(output_review,'read_review',lambda *a, **kw: {'decision':decision} if decision else None)
    assert handoff._prepare_boundary(config,'fmriprep')['state'] == state
