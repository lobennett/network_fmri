from pathlib import Path

import json

from network_fmri.models import StageResult
from network_fmri.reviews import ReviewMigrator


HEADER = (
    "record_type\tsubject\tsession\tdatatype\tsuffix\ttask\tacquisition\t"
    "direction\trun\tmean_fd\tsource_path\tdecision\treviewer\treviewed_at\tnotes\n"
)


def write_scan(path: Path, row: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + row + "\n")
    path.with_suffix(".meta.json").write_text('{"approved": false}\n')
    return path


def test_scan_migration_matches_normalized_bids_key_and_keeps_new_paths(tmp_path):
    source = write_scan(
        tmp_path / "old.tsv",
        "acquisition\tsub-s03\tses-07\tfunc\tbold\tgoNogo\t\t\t01\t0.25\t/old/raw/file.nii.gz\tkeep\tLB\t2026-09-23T12:00:00Z\thigh motion",
    )
    destination = tmp_path / "study/code/network_fmri/scan_decisions.tsv"
    calls = []

    def regenerate(raw, evidence):
        calls.append((raw, evidence))
        write_scan(
            destination,
            "acquisition\ts03\t07\tfunc\tbold\tgoNogo\t\t\t1\t0.25\t/new/raw/file.nii.gz\treview\t\t\t",
        )
        return StageResult("scan-decisions-generated", (destination, destination.with_suffix(".meta.json")))

    approvals = []
    result = ReviewMigrator().migrate_scan(
        source,
        installed_raw=tmp_path / "study/sourcedata/raw",
        mriqc_derivative=tmp_path / "campaign/derivatives/MRIQC-24.0.2",
        regenerate=regenerate,
        validate_source=lambda: approvals.append("source"),
        approve=lambda: approvals.append("destination") or StageResult(
            "scan-decisions-approved", (destination,)
        ),
        save_approval=lambda approved: approvals.append(approved.name),
    )

    assert result.approved is True
    assert calls == [(tmp_path / "study/sourcedata/raw", tmp_path / "campaign/derivatives/MRIQC-24.0.2")]
    assert approvals == ["source", "destination", "scan-decisions-approved"]
    row = destination.read_text().splitlines()[1].split("\t")
    assert row[10] == "/new/raw/file.nii.gz"
    assert row[11:] == ["keep", "LB", "2026-09-23T12:00:00Z", "high motion"]


def test_changed_evidence_writes_report_and_leaves_generated_review_unsealed(tmp_path):
    source = write_scan(
        tmp_path / "old.tsv",
        "acquisition\tsub-s03\tses-07\tfunc\tbold\tgoNogo\t\t\t1\t0.25\t/old/file\tkeep\tLB\t2026-09-23T12:00:00Z\t",
    )
    destination = tmp_path / "study/code/network_fmri/scan_decisions.tsv"

    def regenerate(_raw, _evidence):
        write_scan(
            destination,
            "acquisition\tsub-s03\tses-07\tfunc\tbold\tgoNogo\t\t\t1\t0.30\t/new/file\treview\t\t\t",
        )
        return StageResult("scan-decisions-generated", (destination,))

    approved = []
    result = ReviewMigrator().migrate_scan(
        source,
        installed_raw=tmp_path / "raw",
        mriqc_derivative=tmp_path / "mriqc",
        regenerate=regenerate,
        validate_source=lambda: None,
        approve=lambda: approved.append(True),
        save_approval=lambda _: approved.append(True),
    )

    assert result.approved is False
    assert approved == []
    assert "\treview\t\t\t" in destination.read_text()
    report = json.loads(result.mismatch_report.read_text())
    assert report["mismatches"][0]["reason"] == "evidence-changed"


def test_missing_and_extra_rows_are_both_reported(tmp_path):
    source = write_scan(
        tmp_path / "old.tsv",
        "acquisition\tsub-s01\tses-01\tfunc\tbold\trest\t\t\t1\t0.10\t/old/one\tkeep\tLB\t2026-09-23T12:00:00Z\t",
    )
    destination = tmp_path / "study/code/network_fmri/scan_decisions.tsv"

    def regenerate(_raw, _evidence):
        write_scan(
            destination,
            "acquisition\tsub-s02\tses-01\tfunc\tbold\trest\t\t\t1\t0.10\t/new/two\treview\t\t\t",
        )
        return StageResult("scan-decisions-generated", (destination,))

    result = ReviewMigrator().migrate_scan(
        source, installed_raw=tmp_path / "raw", mriqc_derivative=tmp_path / "mriqc",
        regenerate=regenerate, validate_source=lambda: None,
        approve=lambda: None, save_approval=lambda _: None,
    )

    reasons = {item["reason"] for item in json.loads(result.mismatch_report.read_text())["mismatches"]}
    assert reasons == {"missing-generated-row", "new-generated-row"}


def test_surface_migration_regenerates_against_anatomical_derivative(tmp_path):
    header = "subject\tsurface_dir\tstatus\tsurface_fingerprint\tapproved\treviewer\treviewed_at\tnotes\n"
    source = tmp_path / "old-surfaces.tsv"
    source.write_text(header + "sub-s01\t/old/s01\tcomplete\tabc\tyes\tLB\t2026-09-23T12:00:00Z\tgood\n")
    destination = tmp_path / "study/code/network_fmri/surface_review.tsv"
    seen = []

    def regenerate(raw, derivative):
        seen.append((raw, derivative))
        destination.parent.mkdir(parents=True)
        destination.write_text(header + "sub-s01\t/new/s01\tcomplete\tabc\tno\t\t\t\n")
        return StageResult("surface-review-generated", (destination,))

    result = ReviewMigrator().migrate_surface(
        source, installed_raw=tmp_path / "study/sourcedata/raw",
        anatomical_derivative=tmp_path / "campaign/derivatives/fMRIPrep-25.2.5+anat",
        regenerate=regenerate, validate_source=lambda: None,
        approve=lambda: StageResult("surface-review-approved", (destination,)),
        save_approval=lambda _: None,
    )

    assert result.approved is True
    assert seen[0][1].name == "fMRIPrep-25.2.5+anat"
    assert "/new/s01\tcomplete\tabc\tyes\tLB\t" in destination.read_text()
