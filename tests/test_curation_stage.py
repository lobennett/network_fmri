import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import network_fmri.curation as curation
from network_fmri.curation import apply_curation
from network_fmri.qa.validate import ValidationError
from network_fmri.stages import StageError


class Runner:
    def __init__(self, validator_returncode=0):
        self.calls = []
        self.validator_returncode = validator_returncode

    def __call__(self, args, **kwargs):
        command = [str(value) for value in args]
        self.calls.append(command)
        if command[:2] == ["apptainer", "exec"] and "/src/bids-validator.js" in command:
            output_root = next(
                Path(value.removesuffix(":/out")) for value in command if value.endswith(":/out")
            )
            outfile = output_root / Path(command[command.index("--outfile") + 1]).name
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
    path.with_suffix(".meta.json").write_text("{}\n")
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

    result = apply_curation(tmp_path, manifest, tmp_path / "validator.sif", runner)

    assert result.name == "bids-curated-validated"
    assert not list(func.glob(f"{stem}*"))
    assert not event_qc.exists()
    assert behavior.is_file()
    assert runner.calls[0][:3] == ["network-qa", "decisions", "validate"]
    assert runner.calls[-1][:2] == ["apptainer", "exec"]


def test_curation_rejects_missing_bundle_before_any_mutation(tmp_path):
    func, stem, behavior, _ = _dataset(tmp_path)
    manifest = _manifest(tmp_path / "code" / "network_fmri" / "scan_decisions.tsv", task="nback")

    with pytest.raises(StageError, match="matched no BIDS images"):
        apply_curation(tmp_path, manifest, tmp_path / "validator.sif", Runner())

    assert list(func.glob(f"{stem}*"))
    assert behavior.is_file()


def test_curation_rebuilds_b0_links_for_the_remaining_acquisitions(tmp_path):
    func, _, _, _ = _dataset(tmp_path)
    kept = "sub-s01_ses-01_task-keep_run-1_echo-2_bold"
    _write(func / f"{kept}.nii.gz", "nifti")
    kept_sidecar = _write(func / f"{kept}.json", "{}")
    manifest = _manifest(tmp_path / "code" / "network_fmri" / "scan_decisions.tsv")

    result = apply_curation(tmp_path, manifest, tmp_path / "validator.sif", Runner())

    fmap_sidecar = tmp_path / "sub-s01" / "ses-01" / "fmap" / "sub-s01_ses-01_fieldmap.json"
    assert result.details["b0"]["bold"] == 1
    assert json.loads(kept_sidecar.read_text())["B0FieldSource"] == "s01_ses-01"
    assert json.loads(fmap_sidecar.read_text())["B0FieldIdentifier"] == "s01_ses-01"


def test_curation_restores_bundle_and_b0_metadata_when_validation_fails(tmp_path):
    func, stem, _, _ = _dataset(tmp_path)
    kept = "sub-s01_ses-01_task-keep_run-1_echo-2_bold"
    _write(func / f"{kept}.nii.gz", "nifti")
    kept_sidecar = _write(func / f"{kept}.json", "{}")
    manifest = _manifest(tmp_path / "code" / "network_fmri" / "scan_decisions.tsv")

    with pytest.raises(ValidationError):
        apply_curation(
            tmp_path, manifest, tmp_path / "validator.sif", Runner(validator_returncode=1),
        )

    assert len(list(func.glob(f"{stem}*bold.nii.gz"))) == 3
    assert len(list(func.glob(f"{stem}*bold.json"))) == 3
    assert json.loads(kept_sidecar.read_text()) == {}
    fmap_sidecar = tmp_path / "sub-s01" / "ses-01" / "fmap" / "sub-s01_ses-01_fieldmap.json"
    assert json.loads(fmap_sidecar.read_text()) == {}


def test_curation_restores_bundle_when_b0_relinking_fails(tmp_path, monkeypatch):
    func, stem, _, _ = _dataset(tmp_path)
    kept = "sub-s01_ses-01_task-keep_run-1_echo-2_bold"
    _write(func / f"{kept}.nii.gz", "nifti")
    _write(func / f"{kept}.json", "{}")
    manifest = _manifest(tmp_path / "code" / "network_fmri" / "scan_decisions.tsv")

    def fail_b0(_):
        raise StageError("injected B0 failure")

    monkeypatch.setattr(curation, "link_b0", fail_b0)
    with pytest.raises(StageError, match="injected B0 failure"):
        apply_curation(tmp_path, manifest, tmp_path / "validator.sif", Runner())

    assert len(list(func.glob(f"{stem}*bold.nii.gz"))) == 3
    assert len(list(func.glob(f"{stem}*bold.json"))) == 3


def test_curation_rejects_manifest_changed_after_approval_validation(tmp_path):
    func, stem, _, _ = _dataset(tmp_path)
    manifest = _manifest(tmp_path / "code" / "network_fmri" / "scan_decisions.tsv")

    class MutatingRunner(Runner):
        def __call__(self, args, **kwargs):
            result = super().__call__(args, **kwargs)
            if args[0] == "network-qa":
                manifest.write_text(manifest.read_text() + "\n")
            return result

    with pytest.raises(StageError, match="changed after validation"):
        apply_curation(tmp_path, manifest, tmp_path / "validator.sif", MutatingRunner())

    assert list(func.glob(f"{stem}*"))


def test_anatomical_curation_uses_the_run_entity_to_select_one_image(tmp_path):
    _write(tmp_path / "dataset_description.json", json.dumps({"Name": "test"}))
    anat = tmp_path / "sub-s01" / "ses-01" / "anat"
    one = _write(anat / "sub-s01_ses-01_run-1_T1w.nii.gz", "one")
    _write(anat / "sub-s01_ses-01_run-1_T1w.json", "{}")
    two = _write(anat / "sub-s01_ses-01_run-2_T1w.nii.gz", "two")
    _write(anat / "sub-s01_ses-01_run-2_T1w.json", "{}")
    manifest = tmp_path / "code" / "network_fmri" / "scan_decisions.tsv"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "record_type\tsubject\tsession\tdatatype\tsuffix\ttask\tacquisition\tdirection\trun\tdecision\n"
        "acquisition\tsub-s01\tses-01\tanat\tT1w\t\t\t\t1\tdrop\n"
    )
    manifest.with_suffix(".meta.json").write_text("{}\n")

    apply_curation(tmp_path, manifest, tmp_path / "validator.sif", Runner())

    assert not one.exists()
    assert two.is_file()


def test_curation_removes_acquisition_specific_mriqc_and_derivatives(tmp_path):
    _, stem, behavior, _ = _dataset(tmp_path)
    mriqc = _write(
        tmp_path / "derivatives" / "mriqc" / "sub-s01" / "ses-01" / "func" /
        f"{stem}_echo-2_bold.json", "{}",
    )
    report = _write(tmp_path / "derivatives" / "mriqc" / f"{stem}_bold.html", "report")
    derivative = _write(
        tmp_path / "derivatives" / "fmriprep" / "sub-s01" / "ses-01" / "func" /
        f"{stem}_space-MNI_desc-preproc_bold.nii.gz", "nifti",
    )
    manifest = _manifest(tmp_path / "code" / "network_fmri" / "scan_decisions.tsv")

    apply_curation(tmp_path, manifest, tmp_path / "validator.sif", Runner())

    assert not mriqc.exists()
    assert not report.exists()
    assert not derivative.exists()
    assert behavior.is_file()


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
        apply_curation(tmp_path, manifest, tmp_path / "validator.sif", RejectApproval())

    assert list(func.glob(f"{stem}*"))


def test_curation_accepts_approved_manifest_from_wrapper_study(tmp_path):
    _dataset(tmp_path)
    manifest = _manifest(tmp_path / "wrapper/code/network_fmri/scan_decisions.tsv")

    result = apply_curation(tmp_path, manifest, tmp_path / "validator.sif", Runner())

    assert result.name == "bids-curated-validated"
