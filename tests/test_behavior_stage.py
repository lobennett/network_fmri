from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from network_fmri.config import (
    BehaviorSource, BehaviorSources, ContainerConfig, ParticipantsSource, SlurmConfig,
    VerifiedContainerConfig, WorkflowConfig, WorkflowPaths,
)
from network_fmri.stages import StageError
from network_fmri.stages.behavior import ingest_behavior


class Runner:
    def __init__(self, source_heads: dict[Path, str]) -> None:
        self.heads = {str(path): head for path, head in source_heads.items()}
        self.calls: list[list[str]] = []
        self.status: dict[str, str] = {}

    def __call__(self, args, **kwargs):
        command = [str(value) for value in args]
        self.calls.append(command)
        if command[:2] == ["git", "-C"]:
            repository = command[2]
            if "status" in command:
                return SimpleNamespace(stdout=self.status.get(repository, ""))
            if "ls-files" in command:
                path = command[-1]
                destination = str(Path(repository) / path)
                head = self.heads.get(destination, "")
                return SimpleNamespace(stdout=f"160000 {head} 0\t{path}\n" if head else "")
            return SimpleNamespace(stdout=self.heads.get(repository, "") + "\n")
        if command[:2] == ["datalad", "clone"]:
            source, destination = command[-2:]
            Path(destination).mkdir(parents=True)
            (Path(destination) / ".git").write_text("gitdir\n")
            self.heads[destination] = self.heads[source]
        return SimpleNamespace(stdout="")


def configuration(tmp_path: Path) -> WorkflowConfig:
    paths = WorkflowPaths(
        bids_dir=tmp_path / "bids", parts_dir=tmp_path / "parts",
        work_dir=tmp_path / "work", log_dir=tmp_path / "logs",
        templateflow_dir=tmp_path / "templateflow",
        freesurfer_license=tmp_path / "license.txt",
    )
    paths.bids_dir.mkdir()
    subjects = tuple(f"s{number}" for number in range(1, 47))
    subjects_file = tmp_path / "subjects.txt"
    subjects_file.write_text("\n".join(subjects) + "\n")
    sources = BehaviorSources(
        in_scanner=BehaviorSource(tmp_path / "in-scanner", "a" * 40),
        out_of_scanner=BehaviorSource(tmp_path / "out-of-scanner", "b" * 40),
    )
    for source in (sources.in_scanner.source, sources.out_of_scanner.source):
        source.mkdir()
        (source / ".git").mkdir()
    return WorkflowConfig(
        paths=paths, subjects_file=subjects_file, subjects=subjects,
        flywheel_project="russpold/r01network", behavior=sources,
        participants=ParticipantsSource(tmp_path / "demographics", "c" * 40),
        mriqc=ContainerConfig(tmp_path / "mriqc.sif", "24.0.2"),
        fmriprep=ContainerConfig(tmp_path / "fmriprep.sif", "25.2.5"),
        pydeface=VerifiedContainerConfig(tmp_path / "pydeface.sif", "2.1.0", "a" * 64),
        slurm=SlurmConfig("normal", 8, 32, 720, 4),
    )


def runner_for(config: WorkflowConfig) -> Runner:
    return Runner({
        config.behavior.in_scanner.source: "a" * 40,
        config.behavior.out_of_scanner.source: "b" * 40,
    })


def test_ingest_installs_both_pinned_subdatasets_and_audits_in_scanner(tmp_path):
    config = configuration(tmp_path)
    runner = runner_for(config)

    result = ingest_behavior(config, runner)

    root = config.paths.bids_dir / "sourcedata" / "behavioral"
    in_scanner, out_of_scanner = root / "in_scanner", root / "out_of_scanner"
    clone_calls = [call for call in runner.calls if call[:2] == ["datalad", "clone"]]
    assert clone_calls == [
        ["datalad", "clone", str(config.behavior.in_scanner.source), str(in_scanner)],
        ["datalad", "clone", str(config.behavior.out_of_scanner.source), str(out_of_scanner)],
    ]
    assert ["datalad", "get", "-d", str(in_scanner), str(in_scanner)] in runner.calls
    assert ["datalad", "get", "-d", str(out_of_scanner), str(out_of_scanner)] not in runner.calls
    assert ["network-events", "audit", "--bids-dir", str(config.paths.bids_dir),
            "--behavioral-dir", str(in_scanner)] in runner.calls
    assert result.outputs == (in_scanner, out_of_scanner)
    assert result.details == {
        "in_scanner_source": str(config.behavior.in_scanner.source),
        "in_scanner_commit": "a" * 40,
        "out_of_scanner_source": str(config.behavior.out_of_scanner.source),
        "out_of_scanner_commit": "b" * 40,
    }


def test_ingest_preflights_both_sources_before_installing_either(tmp_path):
    config = configuration(tmp_path)
    runner = runner_for(config)
    runner.heads[str(config.behavior.out_of_scanner.source)] = "f" * 40

    with pytest.raises(StageError, match="canonical behavior commit"):
        ingest_behavior(config, runner)

    assert not any(call[:2] == ["datalad", "clone"] for call in runner.calls)


def test_ingest_matching_rerun_is_a_safe_noop(tmp_path):
    config = configuration(tmp_path)
    root = config.paths.bids_dir / "sourcedata" / "behavioral"
    destinations = (root / "in_scanner", root / "out_of_scanner")
    runner = runner_for(config)
    for destination, head in zip(destinations, ("a" * 40, "b" * 40), strict=True):
        destination.mkdir(parents=True)
        (destination / ".git").write_text("gitdir\n")
        runner.heads[str(destination)] = head

    ingest_behavior(config, runner)

    assert not any(call[:2] == ["datalad", "clone"] for call in runner.calls)


def test_ingest_rejects_conflicting_existing_destination(tmp_path):
    config = configuration(tmp_path)
    destination = config.paths.bids_dir / "sourcedata" / "behavioral" / "in_scanner"
    destination.mkdir(parents=True)
    (destination / "unrelated.txt").write_text("keep\n")

    with pytest.raises(StageError, match="conflicting behavioral destination"):
        ingest_behavior(config, runner_for(config))

    assert (destination / "unrelated.txt").read_text() == "keep\n"


@pytest.mark.parametrize("source_name", ["in_scanner", "out_of_scanner"])
def test_ingest_rejects_dirty_finalized_source(tmp_path, source_name):
    config = configuration(tmp_path)
    runner = runner_for(config)
    source = getattr(config.behavior, source_name).source
    runner.status[str(source)] = "?? unexpected.csv\n"

    with pytest.raises(StageError, match="tracked, untracked, or ignored"):
        ingest_behavior(config, runner)
