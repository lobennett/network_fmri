import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from network_fmri.curation import apply_curation
from network_fmri.stages import StageError


class Runner:
    def __init__(self, validator_returncode=0):
        self.calls = []
        self.validator_returncode = validator_returncode

    def __call__(self, args, **kwargs):
        command = [str(value) for value in args]
        self.calls.append(command)
        if command[0] == "bids-validator":
            outfile = Path(command[command.index("--outfile") + 1])
            outfile.parent.mkdir(parents=True, exist_ok=True)
            outfile.write_text('{"issues": {}}\n')
            return SimpleNamespace(returncode=self.validator_returncode, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _write(path, contents=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    return path


def _manifest(path, *, decision="drop", task="rest", run="1"):
    columns = [
        "record_type", "subject", "session", "datatype", "suffix", "task",
        "acquisition", "direction", "run", "decision",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        writer.writerow({
            "record_type": "acquisition", "subject": "sub-s01", "session": "ses-01",
            "datatype": "func", "suffix": "bold", "task": task, "acquisition": "",
            "direction": "", "run": run, "decision": decision,
        })
    return path


def _dataset(tmp_path):
    _write(tmp_path / "dataset_description.json", json.dumps({"Name": "test"}))
    func = tmp_path / "sub-s01" / "ses-01" / "func"
    stem = "sub-s01_ses-01_task-rest_run-01"
    for echo in (1, 2, 3):
        _write(func / f"{stem}_echo-{echo}_bold.nii.gz", "nifti")
        _write(func / f"{stem}_echo-{echo}_bold.json", "{}")
    _write(func / f"{stem}_events.tsv", "onset\tduration\n")
    _write(func / f"{stem}_events.json", "{}")
    event_qc = _write(
        tmp_path / "sourcedata" / "events_qc" / "sub-s01" / "ses-01" /
        f"{stem}_desc-truncation.json", "{}",
    )
    fmap = tmp_path / "sub-s01" / "ses-01" / "fmap"
    _write(fmap / "sub-s01_ses-01_fieldmap.nii.gz", "nifti")
    _write(fmap / "sub-s01_ses-01_fieldmap.json", "{}")
    behavior = _write(
        tmp_path / "sourcedata" / "behavioral" / "sub-s01" / "ses-01" / "beh" /
        f"{stem}_beh.csv", "trial,onset\n1,1\n",
    )
    return func, stem, behavior, event_qc


def test_drop_removes_echo_bundle_events_and_sidecars_but_keeps_raw_behavior(tmp_path):
    func, stem, behavior, event_qc = _dataset(tmp_path)
    manifest = _manifest(tmp_path / "code" / "network_fmri" / "scan_decisions.tsv")
    runner = Runner()

    result = apply_curation(tmp_path, manifest, runner)

    assert result.name == "mriqc-curated"
    assert not list(func.glob(f"{stem}*"))
    assert not event_qc.exists()
    assert behavior.is_file()
    assert runner.calls[0][:3] == ["network-qa", "decisions", "validate"]
    assert runner.calls[-1][0] == "bids-validator"


def test_curation_rejects_missing_bundle_before_any_mutation(tmp_path):
    func, stem, behavior, _ = _dataset(tmp_path)
    manifest = _manifest(tmp_path / "code" / "network_fmri" / "scan_decisions.tsv", task="nback")

    with pytest.raises(StageError, match="matched no BIDS images"):
        apply_curation(tmp_path, manifest, Runner())

    assert list(func.glob(f"{stem}*"))
    assert behavior.is_file()


def test_curation_rebuilds_b0_links_for_the_remaining_acquisitions(tmp_path):
    func, _, _, _ = _dataset(tmp_path)
    kept = "sub-s01_ses-01_task-keep_run-1_echo-2_bold"
    _write(func / f"{kept}.nii.gz", "nifti")
    kept_sidecar = _write(func / f"{kept}.json", "{}")
    manifest = _manifest(tmp_path / "code" / "network_fmri" / "scan_decisions.tsv")

    result = apply_curation(tmp_path, manifest, Runner())

    fmap_sidecar = tmp_path / "sub-s01" / "ses-01" / "fmap" / "sub-s01_ses-01_fieldmap.json"
    assert result.details["b0"]["bold"] == 1
    assert json.loads(kept_sidecar.read_text())["B0FieldSource"] == "s01_ses-01"
    assert json.loads(fmap_sidecar.read_text())["B0FieldIdentifier"] == "s01_ses-01"


def test_curation_rejects_an_edited_or_unsealed_manifest_before_mutation(tmp_path):
    func, stem, _, _ = _dataset(tmp_path)
    manifest = _manifest(tmp_path / "code" / "network_fmri" / "scan_decisions.tsv")

    class RejectApproval(Runner):
        def __call__(self, args, **kwargs):
            command = [str(value) for value in args]
            self.calls.append(command)
            if command[0] == "network-qa":
                raise __import__("subprocess").CalledProcessError(1, command)
            return super().__call__(args, **kwargs)

    with pytest.raises(StageError, match="approval"):
        apply_curation(tmp_path, manifest, RejectApproval())

    assert list(func.glob(f"{stem}*"))
