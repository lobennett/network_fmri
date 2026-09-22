import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from network_fmri.config import ParticipantsSource
from network_fmri.stages import StageError
from network_fmri.stages.participants import ingest_participants


class GitRunner:
    def __init__(self, source: Path, head: str, status: str = "") -> None:
        self.source = source
        self.head = head
        self.status = status

    def __call__(self, command, **_kwargs):
        command = tuple(map(str, command))
        assert command[:3] == ("git", "-C", str(self.source))
        if command[3] == "rev-parse":
            return SimpleNamespace(stdout=self.head + "\n")
        if command[3] == "status":
            return SimpleNamespace(stdout=self.status)
        raise AssertionError(f"unexpected command: {command}")


def source_files(source: Path, subjects: tuple[str, ...], *, describe_age: bool = True) -> None:
    source.mkdir()
    rows = ["participant_id\tage", *(f"sub-{subject}\t{20 + index}" for index, subject in enumerate(subjects))]
    (source / "participants.tsv").write_text("\n".join(rows) + "\n")
    metadata = {"age": {"Description": "Age in years"}} if describe_age else {}
    (source / "participants.json").write_text(json.dumps(metadata) + "\n")


def configuration(tmp_path: Path, source_subjects: tuple[str, ...] | None = None):
    roster = tuple(f"s{number}" for number in range(1, 47))
    source = tmp_path / "canonical-demographics"
    source_files(source, source_subjects or roster)
    roster_file = tmp_path / "subjects.txt"
    roster_file.write_text("\n".join(roster) + "\n")
    bids = tmp_path / "bids"
    bids.mkdir()
    config = SimpleNamespace(
        paths=SimpleNamespace(bids_dir=bids),
        subjects_file=roster_file,
        subjects=roster,
        participants=ParticipantsSource(source, "c" * 40),
    )
    return config, GitRunner(source, "c" * 40)


def test_ingest_publishes_validated_bids_participant_files(tmp_path):
    config, runner = configuration(tmp_path)

    result = ingest_participants(config, runner)

    tsv = config.paths.bids_dir / "participants.tsv"
    sidecar = config.paths.bids_dir / "participants.json"
    assert tsv.read_text().splitlines()[1] == "sub-s1\t20"
    assert len(tsv.read_text().splitlines()) == 47
    assert json.loads(sidecar.read_text()) == {"age": {"Description": "Age in years"}}
    assert result.outputs == (tsv, sidecar)
    assert result.details == {
        "source": str(config.participants.source),
        "commit": "c" * 40,
        "participants": 46,
    }


@pytest.mark.parametrize(
    "source_subjects",
    [
        tuple(f"s{number}" for number in range(1, 46)),
        tuple(f"s{number}" for number in range(1, 47)) + ("s47",),
    ],
)
def test_ingest_rejects_demographics_that_do_not_exactly_cover_roster(tmp_path, source_subjects):
    config, runner = configuration(tmp_path, source_subjects)

    with pytest.raises(StageError, match="participant roster"):
        ingest_participants(config, runner)

    assert not (config.paths.bids_dir / "participants.tsv").exists()


def test_ingest_requires_metadata_for_every_non_id_column(tmp_path):
    config, runner = configuration(tmp_path)
    (config.participants.source / "participants.json").write_text("{}\n")

    with pytest.raises(StageError, match="age"):
        ingest_participants(config, runner)


@pytest.mark.parametrize(
    ("head", "status", "message"),
    [("d" * 40, "", "commit"), ("c" * 40, "?? raw-identifiers.csv\n", "clean")],
)
def test_ingest_requires_exact_clean_source_revision(tmp_path, head, status, message):
    config, _ = configuration(tmp_path)
    runner = GitRunner(config.participants.source, head, status)

    with pytest.raises(StageError, match=message):
        ingest_participants(config, runner)


def test_ingest_rolls_back_first_file_if_second_publish_fails(tmp_path, monkeypatch):
    config, runner = configuration(tmp_path)
    from network_fmri.stages import participants

    real_replace = participants.os.replace

    def fail_on_sidecar(source, destination):
        if Path(destination).name == "participants.json":
            raise OSError("synthetic publish failure")
        return real_replace(source, destination)

    monkeypatch.setattr(participants.os, "replace", fail_on_sidecar)

    with pytest.raises(StageError, match="publish"):
        ingest_participants(config, runner)

    assert not (config.paths.bids_dir / "participants.tsv").exists()
    assert not (config.paths.bids_dir / "participants.json").exists()
