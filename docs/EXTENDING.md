# Adding a package to the pipeline

Most external contributors should add one dependency and one TOML manifest. They should
not edit `pipeline.py`, import the internal stage registry, write an `sbatch` wrapper, or
reimplement provenance. `network_fmri` validates the manifest, places the command at a
stable lifecycle boundary, submits it through Slurm, and records what ran.

This is intentionally a bounded integration system rather than a general workflow
engine. An integration is one cohort-level Slurm job. A package may fan out internally,
but it must return nonzero if any required work fails and must create its declared
outputs.

## Choose a lifecycle profile and slot

Profiles are independently submitted pipeline slices. This reflects the real campaign
handoff: fMRIPrep is run by mechababs/BABS, not inside the BIDS-preparation DAG.

| Profile | Slot | Runs after | Primary input |
|---|---|---|---|
| `bids` | `pre-trim` | pre-trim validation and global-signal QC | validated BIDS tree |
| `bids` | `pre-fmriprep` | final BIDS validation and study checks | checked BIDS tree |
| `post-fmriprep` | `post-fmriprep` | verification of an external fMRIPrep derivative | verification receipt |
| `analysis` | `analysis` | verification of fMRIPrep plus the motion/behavior exclusion lockfile | verification receipt |

Use `preprocessing` for commands that prepare data, `quality-control` for reports or
gates, and `analysis` for downstream computation. The category documents scientific
intent; the slot controls execution order.

## Add a manifest

Put project-owned manifests in
`src/network_fmri/integrations/catalog/<integration>.toml`. A site can also supply one or
more directories with `--integration-dir` without changing this repository.

```toml
api_version = 1
name = "package-qc"
package = "package-qc"
description = "run package QC before dummy-volume trimming"
category = "quality-control"
slot = "pre-trim"
effect = "derivative"
enabled = false
command = ["package-qc", "--bids-dir", "{bids_dir}", "--out", "{bids_dir}/derivatives/package_qc/report.json"]
requires = ["{bids_dir}/dataset_description.json"]

[resources]
cpus = 4
memory = "16G"
time_limit = "02:00:00"

[[outputs]]
name = "package-qc-report"
location = "{bids_dir}/derivatives/package_qc/report.json"
description = "machine-readable package QC report"
```

Use an argv array for `command`, never a shell fragment. Unknown keys and placeholders
fail validation. A `derivative` integration must declare at least one output.

The `effect` makes data ownership explicit:

| Effect | Meaning |
|---|---|
| `read-only` | Inspect the primary input without changing it; reports may be declared as outputs. |
| `in-place` | Deliberately modify the primary input; the input is checked again afterward. |
| `derivative` | Leave the primary input intact and create one or more declared derivatives. |

Additional paths in `requires` and all declared outputs are checked on the compute node.
The command is considered successful only when it exits zero and every promised output
exists.

## Install and activate it

Add the package to `[project.dependencies]` and, for an immutable Git dependency, pin its
revision under `[tool.uv.sources]`. Refresh `uv.lock` and sync the frozen Sherlock
environment. Installing a package does **not** activate it.

```bash
uv run --frozen network_fmri integration validate --check-installed
uv run --frozen network_fmri integration list

uv run --frozen network_fmri pipeline --cohort discovery \
    --enable-integration package-qc --print
```

`enabled = true` is available for a project-wide default; operators can still use
`--disable-integration`. Unknown enable and disable names fail planning so configuration
typos cannot silently change the DAG. Prefer `enabled = false` until the package and its
scientific
parameters have been reviewed. The machine-readable plan records the manifest source,
package, slot, effect, rendered argv, resources, paths, and provider.

For a site manifest:

```bash
uv run --frozen network_fmri integration validate \
    --integration-dir /oak/group/network_fmri_integrations --check-installed
uv run --frozen network_fmri pipeline --cohort discovery \
    --integration-dir /oak/group/network_fmri_integrations \
    --enable-integration package-qc --print
```

## Post-fMRIPrep and analysis examples

The external inputs are verified in a small Slurm job before the package runs. The
verifier requires an fMRIPrep `GeneratedBy` record and the exact selected cohort roster.
For the analysis profile it also requires a `network_qa`-style lockfile whose metadata
names the same cohort. This prevents a typo, partial derivative, or wrong exclusion file
from launching an analysis.

```bash
# Package that needs fMRIPrep but makes its own QC derivative.
uv run --frozen network_fmri pipeline --cohort discovery \
    --profile post-fmriprep \
    --fmriprep-dir /oak/path/to/fmriprep \
    --enable-integration package-qc --print

# Analysis package that also consumes the first-level exclusion lockfile.
uv run --frozen network_fmri pipeline --cohort discovery \
    --profile analysis \
    --fmriprep-dir /oak/path/to/fmriprep \
    --exclusions-file /oak/path/to/discovery_motion_lock.json \
    --analysis-dir /oak/path/to/results \
    --enable-integration package-analysis --print
```

The default fMRIPrep path is `<bids>/derivatives/fmriprep`; the default analysis
exclusion path is `<bids>/derivatives/qa/<cohort>_motion_lock.json`. Pass explicit paths
when campaign outputs or results live elsewhere.

## Template fields

Manifest commands, requirements, and output locations may use:

| Template | Value |
|---|---|
| `{cohort}` | Cohort name |
| `{staging}` | Staging root |
| `{bids_dir}` | Merged cohort BIDS directory |
| `{fmriprep_dir}` | Configured fMRIPrep derivative root |
| `{exclusions_file}` | Configured motion/behavior exclusion lockfile |
| `{analysis_dir}` | Configured analysis output root |
| `{profile}` | Active lifecycle profile |
| `{network_fmri}` | Launcher in the active environment |
| `{events_bin}` | `network-events` launcher |
| `{project}` | Flywheel project |
| `{partition}` | Slurm partition expression |
| `{throttle}` | Export-array throttle |
| `{live_flag}` | `--live` for live export, otherwise omitted |

## Execution receipts and resume safety

Every integration runs through a provenance wrapper and writes
`<staging>/logs/<cohort>/integrations/<name>.json`. The receipt records timestamps,
status, exact argv, inputs, outputs, installed distribution version and direct URL, exit
code, and any failure. Post-fMRIPrep and analysis verification create similar receipts
under `.../artifacts/`.

If `--from` would skip an enabled integration whose receipt is absent, planning stops.
Resume at that integration, or use `--assume-complete` only after independently verifying
the output. This safeguard does not replace DataLad provenance for commands that modify a
dataset; packages remain responsible for using DataLad when appropriate.

## Programmatic package entry point

A separately distributed package may expose the same v1 contract without a TOML file:

```python
from network_fmri.integrations import (
    Effect,
    IntegrationCategory,
    IntegrationOutput,
    IntegrationResources,
    IntegrationSpec,
    LifecycleSlot,
)


def integration():
    return IntegrationSpec(
        name="package-qc",
        package="package-qc",
        description="run package QC after fMRIPrep",
        category=IntegrationCategory.QUALITY_CONTROL,
        slot=LifecycleSlot.POST_FMRIPREP,
        effect=Effect.DERIVATIVE,
        command=("package-qc", "--input", "{fmriprep_dir}"),
        resources=IntegrationResources(cpus=4, memory="16G", time_limit="02:00:00"),
        outputs=(
            IntegrationOutput(
                "package-qc-report",
                "{analysis_dir}/package_qc/report.json",
                "package QC report",
            ),
        ),
    )
```

```toml
[project.entry-points."network_fmri.integrations.v1"]
package-qc = "package_name.network_fmri:integration"
```

Entry-point names must equal `IntegrationSpec.name`. They are not imported or run merely
because the package is installed; `--enable-integration package-qc` is still required.

## Contribution checklist

1. Keep the package's scientific implementation and CLI in its own repository.
2. Add or pin the dependency and add one disabled manifest (or a v1 entry point).
3. Declare realistic Slurm resources, all required paths, and deterministic outputs.
4. Add tests for parsing, placement, rendered argv, missing inputs/outputs, and failure.
5. Run `integration validate --check-installed` and print every affected profile.
6. Update the README if the integration becomes part of the normal operator workflow.

The old `network_fmri.pipeline_stages` entry-point group remains available for existing
packages and can be suppressed with `--no-extensions`. It exposes internal `StageSpec`
objects, auto-loads installed providers, and is retained only for compatibility. New
integrations should use the explicit v1 interface above.
