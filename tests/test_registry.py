import pytest

from network_fmri.registry import (
    ArtifactSpec,
    PipelineContext,
    RegistryError,
    SlurmResources,
    StageRegistry,
    StageSpec,
    TRIMMED,
    builtin_stages,
    load_stage_extensions,
    resolve_command,
)


@pytest.fixture
def context(tmp_path):
    return PipelineContext(
        cohort="discovery",
        staging=str(tmp_path / "staging"),
        network_fmri_bin="/venv/bin/network_fmri",
        events_bin="/venv/bin/network-events",
        project="r01network",
        partition="russpold,normal",
        throttle=3,
        live=False,
    )


def test_builtin_plan_preserves_stage_order_and_resources(context):
    plan = StageRegistry(builtin_stages()).plan(context)
    assert [stage.name for stage in plan] == [
        "export",
        "merge",
        "fix-sidecars",
        "validate-pre",
        "gs-pre",
        "trim",
        "b0link",
        "gs-post",
        "ingest-beh",
        "events",
        "validate-post",
        "check",
    ]
    assert plan[5].resources == SlurmResources(16, "32G", "08:00:00")
    assert plan[9].working_directory == str(context.bids_dir)
    assert plan[9].command[:2] == (
        "/venv/bin/network-events",
        "create",
    )


def test_resume_drops_only_dependencies_outside_selected_plan(context):
    plan = StageRegistry(builtin_stages()).plan(context, start="trim")
    assert plan[0].name == "trim"
    assert plan[0].dependencies == ()
    assert plan[1].dependencies == ("trim",)
    assert plan[-1].name == "check"


def test_artifact_paths_and_state_are_machine_readable(context):
    plan = StageRegistry(builtin_stages()).plan(context)
    trim = next(stage for stage in plan if stage.name == "trim")
    assert trim.inputs[0].name == "validated-pre"
    assert trim.outputs[0].name == "trimmed-bids"
    assert trim.inputs[0].location == trim.outputs[0].location
    assert trim.as_dict()["resources"]["memory"] == "32G"


class FakeEntryPoint:
    def __init__(self, name, value):
        self.name = name
        self._value = value

    def load(self):
        return self._value


class BrokenEntryPoint(FakeEntryPoint):
    def load(self):
        raise RuntimeError("package import failed")


def extension_stage():
    report = ArtifactSpec(
        "package-qc-report",
        "{bids_dir}/derivatives/package_qc/report.json",
        "external package QC",
    )
    return StageSpec(
        name="package-qc",
        description="run an externally packaged QC stage",
        resources=SlurmResources(2, "4G", "01:00:00"),
        command=("package-qc", "--bids-dir", "{bids_dir}"),
        inputs=(TRIMMED,),
        outputs=(report,),
        after=("trim",),
        before=("b0link",),
    )


def test_extension_can_gate_a_precise_point_in_the_chain(context):
    registry = StageRegistry(builtin_stages())
    load_stage_extensions(
        registry,
        discovered=[FakeEntryPoint("package_qc", extension_stage)],
    )
    plan = registry.plan(context)
    names = [stage.name for stage in plan]
    assert names[names.index("trim") : names.index("b0link") + 1] == [
        "trim",
        "package-qc",
        "b0link",
    ]
    added = next(stage for stage in plan if stage.name == "package-qc")
    assert added.provider == "entry-point:package_qc"
    b0link = next(stage for stage in plan if stage.name == "b0link")
    assert b0link.dependencies == ("trim", "package-qc")


def test_extension_can_declare_a_sequenced_in_place_transform(context):
    stage = StageSpec(
        name="package-prepare",
        description="modify the trimmed BIDS state in place",
        resources=SlurmResources(2, "4G", "01:00:00"),
        command=("package-prepare", "{bids_dir}"),
        inputs=(TRIMMED,),
        outputs=(TRIMMED,),
        after=("trim",),
        before=("b0link",),
    )
    registry = StageRegistry(builtin_stages())
    registry.register(stage, provider="test")
    plan = registry.plan(context)
    added = next(item for item in plan if item.name == "package-prepare")
    assert added.inputs[0] == added.outputs[0]
    assert next(item for item in plan if item.name == "b0link").dependencies == (
        "trim",
        "package-prepare",
    )


def test_parallel_in_place_writers_fail_closed(context):
    stage = StageSpec(
        name="unsafe-writer",
        description="write without consuming the artifact",
        resources=SlurmResources(1, "1G", "00:01:00"),
        command=("unsafe-writer",),
        inputs=(),
        outputs=(TRIMMED,),
        after=("trim",),
        before=("b0link",),
    )
    registry = StageRegistry(builtin_stages())
    registry.register(stage)
    with pytest.raises(RegistryError, match="unsequenced producers"):
        registry.plan(context)


def test_extension_collision_fails_closed():
    registry = StageRegistry(builtin_stages())
    collision = extension_stage()
    collision = StageSpec(
        name="trim",
        description=collision.description,
        resources=collision.resources,
        command=collision.command,
        inputs=collision.inputs,
        outputs=collision.outputs,
        after=("gs-pre",),
    )
    with pytest.raises(RegistryError, match="conflicts"):
        load_stage_extensions(
            registry,
            discovered=[FakeEntryPoint("collision", collision)],
        )


def test_extension_load_error_names_the_provider():
    registry = StageRegistry(builtin_stages())
    with pytest.raises(RegistryError, match="invalid pipeline entry point 'broken'"):
        load_stage_extensions(
            registry,
            discovered=[BrokenEntryPoint("broken", None)],
        )


def test_extension_requires_an_explicit_pipeline_anchor():
    registry = StageRegistry(builtin_stages())
    stage = extension_stage()
    unanchored = StageSpec(
        name=stage.name,
        description=stage.description,
        resources=stage.resources,
        command=stage.command,
        inputs=stage.inputs,
        outputs=stage.outputs,
    )
    with pytest.raises(RegistryError, match="must declare after or before"):
        registry.register(unanchored, provider="external")


def test_unproduced_artifact_is_rejected(context):
    missing = ArtifactSpec("missing-data", "/missing", "missing")
    output = ArtifactSpec("result", "/result", "result")
    stage = StageSpec(
        "consumer",
        "consume missing data",
        SlurmResources(1, "1G", "00:01:00"),
        ("consumer",),
        (missing,),
        (output,),
    )
    with pytest.raises(RegistryError, match="unproduced"):
        StageRegistry([stage]).plan(context)


def test_unknown_template_field_is_rejected(context):
    source = ArtifactSpec("source", "/source", "source", external=True)
    output = ArtifactSpec("result", "/result", "result")
    stage = StageSpec(
        "bad-template",
        "use an invalid template",
        SlurmResources(1, "1G", "00:01:00"),
        ("tool", "{not_available}"),
        (source,),
        (output,),
    )
    with pytest.raises(RegistryError, match="unknown template"):
        StageRegistry([stage]).plan(context)


def test_dependency_cycle_is_rejected_before_planning(context):
    source = ArtifactSpec("source", "/source", "source", external=True)
    first = StageSpec(
        "first",
        "first cyclic stage",
        SlurmResources(1, "1G", "00:01:00"),
        ("first",),
        (source,),
        (ArtifactSpec("first-result", "/first", "first result"),),
        after=("second",),
    )
    second = StageSpec(
        "second",
        "second cyclic stage",
        SlurmResources(1, "1G", "00:01:00"),
        ("second",),
        (source,),
        (ArtifactSpec("second-result", "/second", "second result"),),
        after=("first",),
    )
    with pytest.raises(RegistryError, match="dependency cycle"):
        StageRegistry([first, second]).plan(context)


def test_two_token_cli_route_wins_and_preserves_arguments():
    command, remaining = resolve_command(
        ["submit", "fw-heudiconv", "--cohort", "discovery"]
    )
    assert command.route == ("submit", "fw-heudiconv")
    assert remaining == ["--cohort", "discovery"]


def test_invalid_resource_contract_fails_at_registration_boundary():
    with pytest.raises(RegistryError, match="resources"):
        SlurmResources(0, "8G", "01:00:00")
