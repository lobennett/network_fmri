"""Plan and submit the cohort pipeline as dependent Slurm jobs.

Slurm is the sole execution engine. Stage order, resources, commands, and artifact
contracts come from the typed registry; scientific work remains in each stage module.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from network_fmri import provenance
from network_fmri.cohorts import COHORTS, DEFAULT_STAGING, roster
from network_fmri.integrations.planner import (
    DEFAULT_START,
    IntegrationContext,
    PipelineProfile,
    build_registry,
    plan_with_resume_guard,
)
from network_fmri.registry import PlannedStage, RegistryError, SubmissionKind

NF = str(Path(sys.executable).parent / "network_fmri")
NE = str(Path(sys.executable).parent / "network-events")
PLAN_SCHEMA_VERSION = 2


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="network_fmri pipeline")
    parser.add_argument("--cohort", required=True, choices=list(COHORTS))
    parser.add_argument(
        "--profile",
        choices=[profile.value for profile in PipelineProfile],
        default=PipelineProfile.BIDS.value,
        help="lifecycle slice to submit",
    )
    parser.add_argument("--staging", default=DEFAULT_STAGING)
    parser.add_argument(
        "--live",
        action="store_true",
        help="tag Flywheel and export for real (otherwise export is a dry run)",
    )
    parser.add_argument("--partition", default="russpold,normal")
    parser.add_argument(
        "--throttle",
        type=int,
        default=3,
        help="concurrent export tasks; Flywheel returns HTTP 500s above about 8",
    )
    parser.add_argument("--project", default="r01network")
    parser.add_argument("--fmriprep-dir", help="external fMRIPrep derivative root")
    parser.add_argument("--exclusions-file", help="compiled exclusion lockfile")
    parser.add_argument("--analysis-dir", help="root exposed as {analysis_dir}")
    parser.add_argument(
        "--from",
        dest="start",
        help="resume at this stage (defaults to the profile's first stage)",
    )
    parser.add_argument(
        "--integration-dir",
        action="append",
        type=Path,
        default=[],
        help="additional directory of versioned integration TOML manifests",
    )
    parser.add_argument(
        "--enable-integration",
        action="append",
        default=[],
        help="explicitly enable a manifest or installed v1 entry point",
    )
    parser.add_argument(
        "--disable-integration",
        action="append",
        default=[],
        help="disable a catalog manifest that has enabled=true",
    )
    parser.add_argument(
        "--assume-complete",
        action="store_true",
        help="allow --from to bypass an enabled integration without a receipt",
    )
    parser.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="print the plan without submitting or creating log directories",
    )
    parser.add_argument(
        "--plan-json",
        type=Path,
        help="write the machine-readable plan here (automatic on submission)",
    )
    parser.add_argument(
        "--no-extensions",
        action="store_true",
        help="ignore legacy network_fmri.pipeline_stages entry points",
    )
    return parser


def _context(args: argparse.Namespace) -> IntegrationContext:
    return IntegrationContext(
        cohort=args.cohort,
        staging=args.staging,
        network_fmri_bin=NF,
        events_bin=NE,
        project=args.project,
        partition=args.partition,
        throttle=args.throttle,
        live=args.live,
        profile=args.profile,
        fmriprep_root=args.fmriprep_dir,
        exclusions_path=args.exclusions_file,
        analysis_root=args.analysis_dir,
    )


def _record(
    args: argparse.Namespace,
    context: IntegrationContext,
    plan: tuple[PlannedStage, ...],
    *,
    created_at: str,
    status: str,
    jobs: dict[str, str],
    submission_commands: dict[str, list[str]],
    error: str | None = None,
) -> dict[str, Any]:
    stages = []
    for stage in plan:
        item = stage.as_dict()
        item["job_id"] = jobs.get(stage.name)
        item["dependency_job_ids"] = [
            jobs[name] for name in stage.dependencies if name in jobs
        ]
        item["submission_command"] = submission_commands.get(stage.name)
        stages.append(item)
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "created_at": created_at,
        "status": status,
        "error": error,
        "code": {
            "revision": provenance.code_revision(),
            "dirty": provenance.code_is_dirty(),
            "python": sys.version.split()[0],
        },
        "cohort": args.cohort,
        "subjects": roster(args.cohort),
        "profile": args.profile,
        "staging": args.staging,
        "bids_dir": context.bids_dir,
        "fmriprep_dir": context.fmriprep_dir,
        "exclusions_file": context.exclusions_file,
        "analysis_dir": context.analysis_dir,
        "parameters": {
            "assume_complete": args.assume_complete,
            "disable_integration": args.disable_integration,
            "enable_integration": args.enable_integration,
            "integration_directories": [str(path) for path in args.integration_dir],
            "live": args.live,
            "partition": args.partition,
            "project": args.project,
            "start": args.start,
            "throttle": args.throttle,
            "legacy_extensions_enabled": not args.no_extensions,
        },
        "integrations": args.resolved_integrations,
        "stages": stages,
    }


def _write_record(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _default_record_path(log_dir: Path, created: datetime) -> Path:
    stamp = created.strftime("%Y%m%dT%H%M%S.%fZ")
    return log_dir / f"pipeline-plan-{stamp}.json"


def _print_plan(args: argparse.Namespace, plan: tuple[PlannedStage, ...]) -> None:
    print(
        f"cohort {args.cohort} ({len(roster(args.cohort))} subjects), "
        f"profile {args.profile}, {len(plan)} stages, staging {args.staging}"
    )
    for stage in plan:
        dependency = (
            f" after {','.join(stage.dependencies)}" if stage.dependencies else ""
        )
        provider = f" [{stage.provider}]" if stage.provider != "network_fmri" else ""
        stage_command = (
            _export_submission_command(stage)
            if stage.submission == SubmissionKind.EXPORT_ARRAY
            else list(stage.command)
        )
        command = shlex.join(stage_command)
        if stage.working_directory:
            command = f"cd {shlex.quote(stage.working_directory)} && {command}"
        print(
            f"  {stage.name:14s} c{stage.resources.cpus:<3} "
            f"{stage.resources.memory:>5} {stage.resources.time_limit}  "
            f"{command}{dependency}{provider}"
        )


def _command_sbatch(
    stage: PlannedStage,
    args: argparse.Namespace,
    log_dir: Path,
    dependency_jobs: list[str],
) -> list[str]:
    resources = stage.resources
    command = [
        "sbatch",
        "-J",
        f"nf-{stage.name}-{args.cohort}",
        "-p",
        args.partition,
        "-c",
        str(resources.cpus),
        f"--mem={resources.memory}",
        "-t",
        resources.time_limit,
        "-o",
        f"{log_dir}/{stage.name}-%j.out",
        "-e",
        f"{log_dir}/{stage.name}-%j.err",
    ]
    if dependency_jobs:
        command.append(f"--dependency=afterok:{':'.join(dependency_jobs)}")
    wrapped = shlex.join(stage.command)
    if stage.working_directory:
        wrapped = f"cd {shlex.quote(stage.working_directory)} && {wrapped}"
    return [*command, "--wrap", wrapped]


def _export_submission_command(stage: PlannedStage) -> list[str]:
    """The exact launcher argv for the one custom array submission path."""
    memory = stage.resources.memory
    if not memory.endswith("G"):
        raise RegistryError("the export array requires memory declared in whole GB")
    return [
        *stage.command,
        "--cpus",
        str(stage.resources.cpus),
        "--mem-gb",
        memory.removesuffix("G"),
        "--time",
        stage.resources.time_limit,
    ]


def _submit_export(stage: PlannedStage) -> str:
    from network_fmri.fw2bids.jobs import get_parser as array_parser
    from network_fmri.fw2bids.jobs import sbatch_array

    command = _export_submission_command(stage)
    # Strip the executable and the two-token CLI route.
    return sbatch_array(array_parser().parse_args(command[3:]))


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    profile = PipelineProfile(args.profile)
    args.start = args.start or DEFAULT_START[profile]
    context = _context(args)
    try:
        registry, integrations = build_registry(
            profile,
            integration_directories=args.integration_dir,
            enable=args.enable_integration,
            disable=args.disable_integration,
            include_legacy_extensions=not args.no_extensions,
        )
        args.resolved_integrations = [
            {
                "name": spec.name,
                "package": spec.package,
                "slot": spec.slot.value,
                "effect": spec.effect.value,
                "source": spec.source,
            }
            for spec in integrations
        ]
        plan = plan_with_resume_guard(
            registry,
            context,
            start=args.start,
            assume_complete=args.assume_complete,
        )
    except RegistryError as error:
        raise SystemExit(str(error)) from error

    created = datetime.now(UTC)
    created_at = created.isoformat()
    jobs: dict[str, str] = {}
    submission_commands: dict[str, list[str]] = {}
    for stage in plan:
        if stage.submission == SubmissionKind.EXPORT_ARRAY:
            submission_commands[stage.name] = _export_submission_command(stage)
    log_dir = Path(args.staging) / "logs" / args.cohort
    if profile != PipelineProfile.BIDS:
        log_dir /= profile.value

    if args.print_only:
        _print_plan(args, plan)
        if args.plan_json:
            _write_record(
                args.plan_json,
                _record(
                    args,
                    context,
                    plan,
                    created_at=created_at,
                    status="dry-run",
                    jobs=jobs,
                    submission_commands=submission_commands,
                ),
            )
        return 0

    log_dir.mkdir(parents=True, exist_ok=True)
    record_path = args.plan_json or _default_record_path(log_dir, created)
    _write_record(
        record_path,
        _record(
            args,
            context,
            plan,
            created_at=created_at,
            status="submitting",
            jobs=jobs,
            submission_commands=submission_commands,
        ),
    )

    try:
        for stage in plan:
            dependency_jobs = [jobs[name] for name in stage.dependencies]
            if stage.submission == SubmissionKind.EXPORT_ARRAY:
                if dependency_jobs:
                    raise RegistryError(
                        "the export array cannot depend on an extension stage"
                    )
                job = _submit_export(stage)
            else:
                command = _command_sbatch(stage, args, log_dir, dependency_jobs)
                submission_commands[stage.name] = command
                output = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                job = output.stdout.strip().split()[-1]
            jobs[stage.name] = job
            predecessors = (
                f" after {','.join(dependency_jobs)}" if dependency_jobs else ""
            )
            print(f"  {stage.name:14s} {job}{predecessors}")
            _write_record(
                record_path,
                _record(
                    args,
                    context,
                    plan,
                    created_at=created_at,
                    status="submitting",
                    jobs=jobs,
                    submission_commands=submission_commands,
                ),
            )
    except Exception as error:
        _write_record(
            record_path,
            _record(
                args,
                context,
                plan,
                created_at=created_at,
                status="failed",
                jobs=jobs,
                submission_commands=submission_commands,
                error=f"{type(error).__name__}: {error}",
            ),
        )
        raise

    _write_record(
        record_path,
        _record(
            args,
            context,
            plan,
            created_at=created_at,
            status="submitted",
            jobs=jobs,
            submission_commands=submission_commands,
        ),
    )
    print(
        f"\n{len(plan)} stages queued for {args.cohort}. "
        "Watch: squeue --me | grep nf-"
    )
    print(f"execution record: {record_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
