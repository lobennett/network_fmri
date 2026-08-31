import argparse
import json
import sys
from pathlib import Path

import pytest

from network_fmri.integrations import (
    Effect,
    IntegrationCategory,
    IntegrationResources,
    IntegrationSpec,
    LifecycleSlot,
)
from network_fmri.integrations.cli import run_integration, verify_inputs
from network_fmri.integrations.manifests import (
    ManifestError,
    load_manifest,
    resolve_integrations,
)
from network_fmri.integrations.planner import (
    IntegrationContext,
    PipelineProfile,
    build_registry,
    plan_with_resume_guard,
)
from network_fmri.registry import RegistryError, resolve_command


def _manifest(directory: Path, text: str) -> Path:
    path = directory / "package-qc.toml"
    path.write_text(text)
    return path


def _context(tmp_path: Path, profile: str = "bids") -> IntegrationContext:
    return IntegrationContext(
        cohort="discovery",
        staging=str(tmp_path / "staging"),
        network_fmri_bin="/venv/bin/network_fmri",
        events_bin="/venv/bin/network-events",
        project="r01network",
        partition="normal",
        throttle=3,
        live=False,
        profile=profile,
    )


def test_manifest_is_strict_and_explicitly_activated(tmp_path):
    _manifest(
        tmp_path,
        """
api_version = 1
name = "package-qc"
package = "pytest"
description = "external QC"
category = "quality-control"
slot = "pre-trim"
effect = "read-only"
enabled = false
command = ["package-qc", "--bids-dir", "{bids_dir}"]
requires = ["{bids_dir}/dataset_description.json"]

[resources]
cpus = 2
memory = "8G"
time_limit = "02:00:00"

[[outputs]]
name = "package-qc-report"
location = "{bids_dir}/derivatives/package_qc/report.json"
description = "package QC report"
""",
    )
    spec = load_manifest(tmp_path / "package-qc.toml")
    assert spec.slot == LifecycleSlot.PRE_TRIM
    assert spec.resources == IntegrationResources(2, "8G", "02:00:00")
    assert resolve_integrations(extra_directories=[tmp_path]) == ()
    assert resolve_integrations(
        extra_directories=[tmp_path], enable=["package-qc"]
    ) == (spec,)

    bad = tmp_path / "bad.toml"
    bad.write_text((tmp_path / "package-qc.toml").read_text() + "typo = true\n")
    with pytest.raises(ManifestError, match="unknown keys"):
        load_manifest(bad)


def test_installed_entry_point_is_not_imported_until_enabled():
    loaded = []
    spec = IntegrationSpec(
        name="package-qc",
        package="pytest",
        description="external QC",
        category=IntegrationCategory.QUALITY_CONTROL,
        slot=LifecycleSlot.PRE_TRIM,
        effect=Effect.READ_ONLY,
        command=("package-qc",),
    )

    class EntryPoint:
        name = "package-qc"

        def load(self):
            loaded.append(True)
            return spec

    assert resolve_integrations(discovered=[EntryPoint()]) == ()
    assert loaded == []
    assert resolve_integrations(enable=["package-qc"], discovered=[EntryPoint()]) == (
        spec,
    )
    assert loaded == [True]


def test_pretrim_integration_gates_trim_and_renders_contract(tmp_path):
    _manifest(
        tmp_path,
        """
api_version = 1
name = "package-qc"
package = "pytest"
description = "external QC"
category = "quality-control"
slot = "pre-trim"
effect = "derivative"
enabled = true
command = ["package-qc", "{bids_dir}"]

[[outputs]]
name = "package-qc-report"
location = "{bids_dir}/derivatives/package_qc/report.json"
description = "package QC report"
""",
    )
    registry, integrations = build_registry(
        PipelineProfile.BIDS,
        integration_directories=[tmp_path],
        include_legacy_extensions=False,
    )
    assert [spec.name for spec in integrations] == ["package-qc"]
    plan = registry.plan(_context(tmp_path))
    names = [stage.name for stage in plan]
    assert names[names.index("gs-pre") : names.index("trim") + 1] == [
        "gs-pre",
        "package-qc",
        "trim",
    ]
    stage = next(item for item in plan if item.name == "package-qc")
    assert stage.provider == "integration:pytest"
    assert stage.command[-2:] == (
        "package-qc",
        str(tmp_path / "staging" / "discovery" / "bids"),
    )
    assert "--receipt" in stage.command
    assert next(item for item in plan if item.name == "trim").dependencies == (
        "gs-pre",
        "package-qc",
    )


def test_resume_cannot_silently_bypass_enabled_integration(tmp_path):
    _manifest(
        tmp_path,
        """
api_version = 1
name = "package-qc"
package = "pytest"
description = "external QC"
category = "quality-control"
slot = "pre-trim"
effect = "read-only"
enabled = true
command = ["package-qc"]
""",
    )
    registry, _ = build_registry(
        "bids",
        integration_directories=[tmp_path],
        include_legacy_extensions=False,
    )
    context = _context(tmp_path)
    with pytest.raises(RegistryError, match="without receipts"):
        plan_with_resume_guard(registry, context, start="trim")
    receipt = (
        tmp_path / "staging" / "logs" / "discovery" / "integrations" / "package-qc.json"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}\n")
    assert plan_with_resume_guard(registry, context, start="trim")[0].name == "trim"


def test_analysis_profile_verifies_external_inputs_before_package(tmp_path):
    _manifest(
        tmp_path,
        """
api_version = 1
name = "package-qc"
package = "pytest"
description = "analysis package"
category = "analysis"
slot = "analysis"
effect = "derivative"
enabled = true
command = ["package-analysis", "--input", "{fmriprep_dir}"]

[[outputs]]
name = "package-analysis-result"
location = "{analysis_dir}/package/result.json"
description = "analysis result"
""",
    )
    registry, _ = build_registry(
        "analysis",
        integration_directories=[tmp_path],
        include_legacy_extensions=False,
    )
    context = _context(tmp_path, "analysis")
    plan = registry.plan(context)
    assert [stage.name for stage in plan] == ["analysis-ready", "package-qc"]
    assert plan[0].inputs[0].external is True
    assert plan[0].inputs[1].location.endswith("discovery_motion_lock.json")
    assert plan[1].dependencies == ("analysis-ready",)
    assert context.analysis_dir in plan[1].outputs[1].location

    with pytest.raises(RegistryError, match="cannot run"):
        build_registry(
            "post-fmriprep",
            integration_directories=[tmp_path],
            include_legacy_extensions=False,
        )


def test_runner_records_package_command_and_promised_outputs(tmp_path):
    source = tmp_path / "input"
    output = tmp_path / "output"
    receipt = tmp_path / "receipt.json"
    source.write_text("input")
    args = argparse.Namespace(
        name="package-qc",
        package="pytest",
        effect="derivative",
        receipt=str(receipt),
        input=[str(source)],
        output=[str(output)],
        command=[
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path; Path(r'%s').write_text('ok')" % output,
        ],
    )
    assert run_integration(args) == 0
    record = json.loads(receipt.read_text())
    assert record["status"] == "completed"
    assert record["package"]["name"].lower() == "pytest"
    assert record["outputs"] == [str(output)]


def test_runner_records_failure_when_promised_output_is_missing(tmp_path):
    receipt = tmp_path / "failed.json"
    args = argparse.Namespace(
        name="package-qc",
        package="pytest",
        effect="derivative",
        receipt=str(receipt),
        input=[],
        output=[str(tmp_path / "missing")],
        command=["--", sys.executable, "-c", "pass"],
    )
    with pytest.raises(RuntimeError, match="missing promised outputs"):
        run_integration(args)
    assert json.loads(receipt.read_text())["status"] == "failed"


def test_fmriprep_verifier_records_subjects_and_exclusions(tmp_path):
    derivative = tmp_path / "fmriprep"
    derivative.mkdir()
    (derivative / "dataset_description.json").write_text(
        json.dumps({"GeneratedBy": [{"Name": "fMRIPrep", "Version": "25.2.4"}]})
    )
    for subject in ("sub-s03", "sub-s10"):
        (derivative / subject).mkdir()
    exclusions = tmp_path / "motion_lock.json"
    exclusions.write_text(
        json.dumps(
            {
                "_meta": {
                    "dataset": "discovery",
                    "generators": ["motion", "behavioral"],
                },
                "exclusions": [],
            }
        )
        + "\n"
    )
    receipt = tmp_path / "verified.json"
    args = argparse.Namespace(
        cohort=None,
        fmriprep_dir=str(derivative),
        exclusions_file=str(exclusions),
        receipt=str(receipt),
    )
    assert verify_inputs(args) == 0
    record = json.loads(receipt.read_text())
    assert record["subjects"] == ["sub-s03", "sub-s10"]
    assert record["n_exclusions"] == 0
    assert record["status"] == "verified"

    args.cohort = "discovery"
    with pytest.raises(SystemExit, match="do not match cohort"):
        verify_inputs(args)


def test_activation_typos_fail_and_specs_follow_lifecycle_order():
    with pytest.raises(ManifestError, match="unknown disabled integrations"):
        resolve_integrations(disable=["misspelled"])

    specs = {
        "a-pre-fmriprep": IntegrationSpec(
            name="a-pre-fmriprep",
            package="pytest",
            description="later lifecycle slot",
            category=IntegrationCategory.PREPROCESSING,
            slot=LifecycleSlot.PRE_FMRIPREP,
            effect=Effect.READ_ONLY,
            command=("later",),
        ),
        "z-pre-trim": IntegrationSpec(
            name="z-pre-trim",
            package="pytest",
            description="earlier lifecycle slot",
            category=IntegrationCategory.PREPROCESSING,
            slot=LifecycleSlot.PRE_TRIM,
            effect=Effect.READ_ONLY,
            command=("earlier",),
        ),
    }

    class EntryPoint:
        def __init__(self, name):
            self.name = name

        def load(self):
            return specs[self.name]

    resolved = resolve_integrations(
        enable=specs,
        discovered=[EntryPoint(name) for name in specs],
    )
    assert [spec.name for spec in resolved] == ["z-pre-trim", "a-pre-fmriprep"]


def test_integration_cli_route_is_public():
    command, remaining = resolve_command(["integration", "list"])
    assert command.target == "network_fmri.integrations.cli:main"
    assert remaining == ["list"]
