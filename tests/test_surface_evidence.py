import zipfile
import json
from types import SimpleNamespace

import pytest

from network_fmri.qa.freesurfer import REQUIRED_OUTPUTS
from network_fmri.stages import StageError


def archive(path, extra=None):
    with zipfile.ZipFile(path, "w") as stream:
        for relative in REQUIRED_OUTPUTS:
            stream.writestr(f"FreeSurfer-8.2.0/subjects/sub-s03/{relative}", "surface")
        if extra:
            stream.writestr(extra, "unsafe")


def test_extracts_reconstruction_and_checks_complete_inventory(tmp_path):
    from network_fmri.surface_evidence import extract_subject_archive
    source = tmp_path / "source.zip"
    archive(source)
    result = extract_subject_archive(source, "s03", tmp_path / "out")
    assert result["surf/lh.white"]
    assert (tmp_path / "out/sub-s03/surf/lh.white").read_text() == "surface"


@pytest.mark.parametrize("entry", ["../escape", "/absolute", "FreeSurfer-8.2.0/subjects/sub-s03/../../escape",
    "FreeSurfer-8.2.0/subjects/sub-s03/surf/lh.white"])
def test_rejects_unsafe_or_duplicate_archive_before_extraction(tmp_path, entry):
    from network_fmri.surface_evidence import extract_subject_archive
    source = tmp_path / "source.zip"
    archive(source, entry)
    with pytest.raises(StageError):
        extract_subject_archive(source, "s03", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_missing_reconstruction_is_not_publishable(tmp_path):
    from network_fmri.surface_evidence import extract_subject_archive
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as stream:
        stream.writestr("FreeSurfer-8.2.0/subjects/sub-s03/surf/lh.white", "surface")
    with pytest.raises(StageError, match="incomplete"):
        extract_subject_archive(source, "s03", tmp_path / "out")


def test_evidence_is_bound_to_real_commits_and_rejects_changed_raw(tmp_path, monkeypatch):
    from network_fmri import surface_evidence
    from tests.test_mriqc_evidence import init, save, git, Commands
    study, raw = tmp_path / "study", tmp_path / "raw"
    source = study / "derivatives/FreeSurfer-8.2.0+pilot"
    init(study, "study")
    init(raw, "raw")
    raw_commit = save(raw)
    init(source, "surfaces")
    git(source, "-c", "protocol.file.allow=always", "clone", "-q", str(raw), "sourcedata/raw")
    git(source, "update-index", "--add", "--cacheinfo", f"160000,{raw_commit},sourcedata/raw")
    archive(source / "sub-s03_FreeSurfer-8.2.0.zip")
    source_commit = save(source)
    project = source.relative_to(study).as_posix()
    git(study, "update-index", "--add", "--cacheinfo", f"160000,{source_commit},{project}")
    save(study)
    config = SimpleNamespace(subjects=("s03",), paths=SimpleNamespace(bids_dir=raw),
                             mechababs=SimpleNamespace(study_dir=study))
    stage = SimpleNamespace(stage="anatomical", state="complete", application="FreeSurfer-8.2.0", project=project)
    monkeypatch.setattr(surface_evidence, "ProcessingManager", lambda *a, **k: SimpleNamespace(plan=lambda: [stage]))
    runner = Commands()
    path = surface_evidence.prepare_surface_evidence(config, runner=runner)
    receipt = json.loads((path.parent / surface_evidence.RECEIPT).read_text())
    assert receipt["input_datalad_commit"] == raw_commit
    assert receipt["source_dataset_commit"] == source_commit
    assert surface_evidence.prepare_surface_evidence(config, runner=runner) == path
    (path.parent / "notes.txt").write_text("review notes")
    save(path.parent)
    with pytest.raises(StageError, match="registered"):
        surface_evidence.prepare_surface_evidence(config, runner=runner)
    runner(("datalad", "save", "-d", str(study), str(path.parent)))
    (raw / "changed.json").write_text("{}")
    save(raw)
    with pytest.raises(StageError, match="curated BIDS"):
        surface_evidence.prepare_surface_evidence(config, runner=runner)
