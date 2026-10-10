import csv
import pytest
from network_fmri.stages.analysis_exclusions import install_known_exclusions


def test_exclusions_are_scoped_and_idempotent(tmp_path):
    (tmp_path / "sub-s03/ses-01").mkdir(parents=True)
    path = install_known_exclusions(tmp_path)
    before = path.read_bytes()
    assert install_known_exclusions(tmp_path).read_bytes() == before
    rows = list(csv.DictReader(path.open(), delimiter="\t"))
    assert len(rows) == 1
    assert rows[0]["analysis_scope"] == "task_first_level"
    assert rows[0]["reviewer"] == "LB"
    path.write_text(path.read_text().replace("unrecoverable_behavior", "other_reason"))
    with pytest.raises(ValueError, match="Conflicting"):
        install_known_exclusions(tmp_path)


def test_study_wrapper_installs_raw_subject_exclusions(tmp_path):
    (tmp_path / "sourcedata/raw/sub-s1292/ses-04").mkdir(parents=True)
    rows = list(csv.DictReader(install_known_exclusions(tmp_path).open(), delimiter="\t"))
    assert len(rows) == 1
    assert rows[0]["reason_code"] == "duplicated_behavior"
