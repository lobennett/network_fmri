# Extending the cohort pipeline

The extension boundary is intentionally narrow: network_fmri owns orchestration,
Slurm is the only execution backend, and an extension contributes one or more typed
cohort stages. A package does not patch the CLI, submission loop, or scientific code
in this repository.

Use an extension when a package needs to run at a defined point in the Flywheel to
BIDS chain. MRIQC, fMRIPrep, and XCP-D remain campaign consumers outside this chain;
the registry is not intended to replace mechababs or become a general workflow
engine.

## Stage contract

A stage declares:

- an immutable name and description;
- one argv-style command, not a shell fragment;
- CPU, memory, and wall-time requests for one Slurm job;
- logical input and output artifacts, with locations;
- after and before ordering constraints;
- an optional working directory.

The planner validates unique names, references, artifact producers, ordering, and
cycles before any sbatch call. The before constraint makes a new stage gate the named
downstream stage; use it with after to occupy an exact boundary. Every extension must
declare at least one of those anchors, so an unconstrained job cannot enter a plan.

For example, a package that modifies the trimmed BIDS tree before field-map linking
can expose:

~~~python
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
~~~

Using the same artifact as input and output explicitly declares an in-place
transformation. It is accepted only when the earlier producer is an ancestor of the
new stage; undeclared or parallel overwrites fail validation. A read-only QC stage
should instead declare its report as a new ArtifactSpec output.

The command and artifact templates may use:

| Field | Meaning |
|---|---|
| {cohort} | cohort name |
| {staging} | staging root |
| {bids_dir} | merged cohort BIDS directory |
| {network_fmri} | absolute path to this environment's launcher |
| {events_bin} | absolute path to network-events |
| {project} | Flywheel project |
| {partition} | selected Slurm partition expression |
| {throttle} | export array throttle |
| {live_flag} | --live during live export, otherwise omitted |

An unknown template field is a planning error.

## Package registration

Register the provider function in the package's pyproject.toml:

~~~toml
[project.entry-points."network_fmri.pipeline_stages"]
package_prepare = "package_name.network_fmri:network_fmri_stages"
~~~

The entry point may provide one StageSpec, a StageExtension, an iterable of
StageSpec, or a zero-argument function returning one of those. Providers load in
entry-point-name order, but stage order comes only from dependency constraints.
Collisions and invalid providers stop planning with an actionable error.

The package must be installed in the same pinned environment. For a production
integration, pin its immutable revision under [tool.uv.sources], regenerate uv.lock,
and add contract tests here or in a small integration adapter. Run:

~~~bash
network_fmri pipeline --cohort discovery --print
~~~

to validate and inspect all installed extensions without submitting. Use
--no-extensions to reproduce the built-in chain while diagnosing an extension.

Extension stages are single cohort-level Slurm jobs. The built-in Flywheel export is
the only registry stage with custom array submission; packages needing a larger DAG
should remain separately orchestrated and expose a bounded handoff stage rather than
expanding this API.

## Execution records

Every live pipeline submission writes an atomic JSON record named
pipeline-plan-UTC.json under the cohort log directory. It captures:

- full code revision and dirty state;
- subjects and operator parameters;
- resolved commands, working directories, and Slurm resources;
- providers, dependencies, and assigned job IDs;
- logical input/output artifact contracts;
- resolved stage commands, standard sbatch argv, and the export-array launcher argv;
- partial progress and the exception if submission fails.

Passing --print --plan-json PATH writes the same schema with dry-run status. Printing
without --plan-json has no filesystem side effects.
