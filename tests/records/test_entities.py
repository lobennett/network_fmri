from pathlib import Path

from network_fmri.records.entities import entity_from_path
from network_fmri.records.models import Artifact, Decision, Entity, Finding, StageAttempt


def test_raw_bids_path_has_stable_entity_key():
    entity = entity_from_path(
        Path("sub-s03/ses-07/func/sub-s03_ses-07_task-goNogo_acq-mb_run-02_echo-2_bold.nii.gz")
    )

    assert entity == Entity(
        namespace="raw", subject="s03", session="07", datatype="func",
        task="goNogo", run="2", acquisition="mb", echo="2", suffix="bold",
    )
    assert entity.key == "raw|s03|07|func|goNogo|2|mb|2|bold"


def test_derivative_namespace_distinguishes_same_bids_entities():
    raw = entity_from_path(Path("sub-s01/func/sub-s01_task-rest_bold.nii.gz"))
    derivative = entity_from_path(
        Path("derivatives/fmriprep/sub-s01/func/sub-s01_task-rest_space-MNI_desc-preproc_bold.nii.gz")
    )

    assert raw.namespace == "raw"
    assert derivative.namespace == "fmriprep"
    assert raw.key != derivative.key
    assert derivative.subject == raw.subject == "s01"
    assert derivative.task == raw.task == "rest"


def test_record_types_are_frozen_and_hold_only_normalized_paths():
    entity = Entity(namespace="raw", subject="s01", suffix="T1w")
    records = (
        StageAttempt("mriqc", "sub-s01", 1, "merged", "log/mriqc.log"),
        Finding(entity.key, "motion", "review", "code/findings.json", "{}"),
        Decision(entity.key, "preprocessing", "keep", "LB", "motion review"),
        Artifact("mriqc", "derivatives/mriqc/report.html", entity.key),
    )

    assert records[0].log_path == "log/mriqc.log"
    assert all("/tmp" not in repr(record) for record in records)
