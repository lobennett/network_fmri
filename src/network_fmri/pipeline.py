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
from network_fmri.registry import (
    PipelineContext,
    PlannedStage,
    RegistryError,
    SubmissionKind,
    pipeline_registry,
)

NF = str(Path(sys.executable).parent / "network_fmri")
NE = str(Path(sys.executable).parent / "network-events")
PLAN_SCHEMA_VERSION = 1


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="network_fmri pipeline")
    parser.add_argument("--cohort", required=True, choices=list(COHORTS))
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
    parser.add_argument(
        "--from",
        dest="start",
        default="export",
        help="resume at this registered stage",
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
        help="ignore installed third-party pipeline stage entry points",
    )
    return parser


def _context(args: argparse.Namespace) -> PipelineContext:
    return PipelineContext(
        cohort=args.cohort,
        staging=args.staging,
        network_fmri_bin=NF,
        events_bin=NE,
        project=args.project,
        partition=args.partition,
        throttle=args.throttle,
        live=args.live,
    )


def _record(
    args: argparse.Namespace,
    context: PipelineContext,
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
        "staging": args.staging,
        "bids_dir": context.bids_dir,
        "parameters": {
            "live": args.live,
            "partition": args.partition,
            "project": args.project,
            "start": args.start,
            "throttle": args.throttle,
            "extensions_enabled": not args.no_extensions,
        },
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
        f"{len(plan)} stages, staging {args.staging}"
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
    context = _context(args)
    try:
        plan = pipeline_registry(include_extensions=not args.no_extensions).plan(
            context, start=args.start
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
