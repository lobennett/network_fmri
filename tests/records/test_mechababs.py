from types import SimpleNamespace

from network_fmri.processing import ProcessingStage, ProcessingStatus
from network_fmri.records.mechababs import collect_attempts


class Manager:
    def __init__(self, status):
        self.value = status
        self.calls = 0

    def status(self):
        self.calls += 1
        return self.value


def test_collects_every_job_attempt_and_cell_without_overwriting_failures():
    jobs = (
        {"pipeline": "MRIQC-24.0.2", "subject": "s01", "session": "01", "job_id": "10",
         "state": "FAILED", "submitted_at": "2026-01-01", "log_path": "logs/10.log", "error": "oom"},
        {"pipeline": "MRIQC-24.0.2", "subject": "s01", "session": "01", "job_id": "11",
         "state": "COMPLETED", "submitted_at": "2026-01-02", "finished_at": "2026-01-03",
         "result_branch": "job-11", "output_commit": "a" * 40, "log_path": "logs/11.log"},
    )
    manager = Manager(ProcessingStatus(
        (ProcessingStage("mriqc", "MRIQC-24.0.2", "complete", "derivatives/mriqc"),), jobs,
    ))

    attempts = collect_attempts(manager)

    assert manager.calls == 1
    assert [item.attempt for item in attempts] == [1, 2]
    assert [item.state for item in attempts] == ["failed", "completed"]
    assert attempts[0].error == "oom"
    assert attempts[1].output_commit == "a" * 40


def test_records_cells_without_jobs_including_intervention_state():
    manager = Manager(ProcessingStatus((
        ProcessingStage("mriqc", "MRIQC-24.0.2", "planned"),
        ProcessingStage("anatomical", "fMRIPrep-25.2.5+anat", "intervention-required"),
    ), ()))

    attempts = collect_attempts(manager)

    assert [(item.stage, item.state) for item in attempts] == [
        ("mriqc", "planned"), ("anatomical", "intervention-required"),
    ]
