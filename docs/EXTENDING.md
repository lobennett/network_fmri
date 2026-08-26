# Extending the cohort pipeline

Use this interface when an installed package needs one cohort-level job at a precise point
in the Flywheel-to-BIDS chain. The boundary is intentionally narrow:

- Slurm is the only backend;
- an extension contributes typed stages, not a new executor;
- each stage is one cohort-level job;
- the built-in Flywheel export is the only custom array stage.

MRIQC, fMRIPrep, XCP-D, and packages with their own DAG remain external campaign
consumers. They should expose a small handoff stage only if the cohort pipeline must gate
on them.

## Stage contract

A `StageSpec` declares:

| Field | Purpose |
|---|---|
| `name`, `description` | Stable identity and readable intent |
| `resources` | CPU, memory, and wall time for one Slurm job |
| `command` | Argument vector; do not provide a shell fragment |
| `inputs`, `outputs` | Logical artifacts and resolved locations |
| `after`, `before` | Placement in the dependency graph |
| `working_directory` | Optional execution directory |

Every extension must declare `after` or `before`. Use both to occupy an exact boundary.
The planner rejects duplicate names, missing stages or producers, inconsistent artifacts,
cycles, and unsequenced in-place writes before the first `sbatch` call.

Example: transform the trimmed BIDS tree after trimming and before field-map linking.

```python
from network_fmri.registry import SlurmResources, StageSpec, TRIMMED


def network_fmri_stages():
    return (
        StageSpec(
            name="package-prepare",
            description="run package-specific preparation",
            resources=SlurmResources(
                cpus=4,
                memory="16G",
                time_limit="02:00:00",
            ),
            command=(
                "package-prepare",
                "--bids-dir",
                "{bids_dir}",
            ),
            inputs=(TRIMMED,),
            outputs=(TRIMMED,),
            after=("trim",),
            before=("b0link",),
        ),
    )
```

Using the same artifact as input and output explicitly declares a sequenced in-place
transformation. A read-only QC stage should instead produce a separate `ArtifactSpec`
report.

## Template fields

Commands, artifact locations, and working directories may use:

| Template | Value |
|---|---|
| `{cohort}` | Cohort name |
| `{staging}` | Staging root |
| `{bids_dir}` | Merged cohort BIDS directory |
| `{network_fmri}` | Launcher in the active environment |
| `{events_bin}` | `network-events` launcher |
| `{project}` | Flywheel project |
| `{partition}` | Slurm partition expression |
| `{throttle}` | Export-array throttle |
| `{live_flag}` | `--live` for live export, otherwise omitted |

Unknown template fields fail planning.

## Register the provider

Declare the provider in the package's `pyproject.toml`:

```toml
[project.entry-points."network_fmri.pipeline_stages"]
package_prepare = "package_name.network_fmri:network_fmri_stages"
```

The entry point may return one `StageSpec`, a `StageExtension`, an iterable of
`StageSpec`, or a zero-argument function returning one of those. Providers load in
entry-point-name order; dependency constraints determine stage order.

Install the package at an immutable revision in the same locked environment, then inspect
the resolved plan:

```bash
uv run --frozen network_fmri pipeline --cohort discovery --print
uv run --frozen network_fmri pipeline --cohort discovery --print --no-extensions
```

The first command validates installed extensions; the second isolates the built-in chain
for diagnosis. Add focused tests for stage placement, artifacts, rendered arguments,
resources, and failure modes.

## Provenance

A submission writes an atomic `pipeline-plan-*.json` under the cohort log directory.
It records the code revision and dirty state, subjects, parameters, providers, resources,
commands, dependencies, artifacts, and Slurm job IDs. Partial submission failures remain
recorded.

To inspect the same schema without submitting:

```bash
uv run --frozen network_fmri pipeline --cohort discovery --print \
    --plan-json /tmp/discovery-plan.json
```
