"""The fixed, single-dataset Slurm graph and its minimal command interface."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path

from network_fmri.config import WorkflowConfig
from network_fmri.slurm import PlannedJob, SubmissionRecord, submit_plan


STAGE_ORDER = (
    "fw2bids-array", "bids-assembled", "behavioral-sourcedata-ingested",
    "gs-pretrim", "dummy-volumes-trimmed", "bids-events-generated",
    "gs-posttrim", "b0-fieldmaps-linked", "bids-precuration-validated",
    "mriqc-array", "mriqc-complete", "scan-decisions-generated",
    "scan-decisions-approved", "mriqc-curated", "bids-curated-validated",
    "fmriprep-array", "fmriprep-complete",
)

_FIRST_SUBMISSION_END = "scan-decisions-generated"
_POST_APPROVAL_START = "mriqc-curated"


def build_plan(
    config: WorkflowConfig, *, config_path: Path | None = None,
) -> tuple[PlannedJob, ...]:
    """Construct the complete dependency graph for one exact subject roster."""

    source = str(config_path) if config_path is not None else "<workflow-config>"
    resources = {
        "cpus": config.slurm.cpus,
        "memory_gb": config.slurm.memory_gb,
        "time_minutes": config.slurm.time_minutes,
    }
    jobs: list[PlannedJob] = []
    predecessor: str | None = None
    for name in STAGE_ORDER:
        is_array = name in {"fw2bids-array", "mriqc-array", "fmriprep-array"}
        command = (
            "network-fmri", "_stage", name, source,
            *(("--array-index", "${SLURM_ARRAY_TASK_ID}") if is_array else ()),
        )
        jobs.append(PlannedJob(
            name=name,
            command=command,
            dependencies=(predecessor,) if predecessor else (),
            array=is_array,
            **resources,
            slurm=config.slurm,
            log_dir=config.paths.log_dir,
            subject_count=len(config.subjects),
        ))
        predecessor = name
    _validate_plan(tuple(jobs), config)
    return tuple(jobs)


def _validate_plan(plan: tuple[PlannedJob, ...], config: WorkflowConfig) -> None:
    if tuple(job.name for job in plan) != STAGE_ORDER:
        raise ValueError("pipeline plan does not match the fixed stage order")
    if len(config.subjects) != 46:
        raise ValueError("pipeline requires exactly 46 configured subjects")
    for index, job in enumerate(plan):
        expected = () if index == 0 else (STAGE_ORDER[index - 1],)
        if job.dependencies != expected:
            raise ValueError(f"stage {job.name} has an invalid dependency")
        if job.array and "datalad" in " ".join(job.command).lower():
            raise ValueError(f"array stage {job.name} must never invoke DataLad")


def initial_submission(plan: tuple[PlannedJob, ...]) -> tuple[PlannedJob, ...]:
    """Return jobs through evidence generation, deliberately excluding approval."""

    stop = next(index for index, job in enumerate(plan) if job.name == _FIRST_SUBMISSION_END)
    return plan[: stop + 1]


def post_approval_submission(plan: tuple[PlannedJob, ...]) -> tuple[PlannedJob, ...]:
    """Return jobs that can run only after a sealed decision manifest exists."""

    start = next(index for index, job in enumerate(plan) if job.name == _POST_APPROVAL_START)
    return plan[start:]


def approval_command(config: WorkflowConfig) -> tuple[str, ...]:
    """The read-only gate checked immediately before curation is queued."""

    manifest = config.paths.bids_dir / "code" / "network_fmri" / "scan_decisions.tsv"
    return (
        "network-qa", "decisions", "validate", "--manifest", str(manifest),
        "--metadata", str(manifest.with_suffix(".meta.json")),
        "--bids-dir", str(config.paths.bids_dir),
    )


def require_approved_decisions(config: WorkflowConfig, runner=subprocess.run) -> None:
    """Refuse post-review submission until Network QA accepts the sealed file."""

    try:
        runner(approval_command(config), check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(
            "scan decisions are not approved; run 'network-fmri decisions validate <bids-dir>' first"
        ) from error


def record_path(config: WorkflowConfig) -> Path:
    return config.paths.log_dir / "pipeline-submission.json"


def write_record(path: Path, record: SubmissionRecord) -> None:
    """Persist scheduler state only after a non-dry submission attempt."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(asdict(record), indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def read_record(path: Path) -> SubmissionRecord:
    try:
        value = json.loads(path.read_text())
        jobs = value["jobs"]
        commands = value["commands"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read pipeline submission record: {path}") from error
    if not isinstance(jobs, dict) or not isinstance(commands, dict):
        raise RuntimeError(f"pipeline submission record is malformed: {path}")
    return SubmissionRecord(
        jobs={str(name): str(job_id) for name, job_id in jobs.items()},
        commands={str(name): tuple(map(str, command)) for name, command in commands.items()},
        dry_run=bool(value.get("dry_run", False)),
    )


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="network-fmri pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "submit", "status"):
        command = commands.add_parser(name)
        command.add_argument("config", type=Path)
        if name == "submit":
            command.add_argument("--resume", action="store_true")
            command.add_argument("--dry-run", action="store_true")
    return parser


def stage_parser() -> argparse.ArgumentParser:
    """Parse the private command executed by Slurm jobs in this fixed graph."""

    parser = argparse.ArgumentParser(prog="network-fmri _stage")
    parser.add_argument("stage", choices=STAGE_ORDER)
    parser.add_argument("config", type=Path)
    parser.add_argument("--array-index", type=int)
    return parser


def _print_plan(plan: tuple[PlannedJob, ...]) -> None:
    for job in plan:
        after = ",".join(job.dependencies) or "none"
        array = " array" if job.array else ""
        print(f"{job.name}{array} after={after}: {' '.join(job.command)}")


def main(argv: list[str] | None = None) -> int:
    supplied = list(argv or ())
    if supplied in (["-h"], ["--help"]):
        get_parser().print_help()
        return 0
    args = get_parser().parse_args(supplied)
    config = WorkflowConfig.load(args.config)
    plan = build_plan(config, config_path=args.config)
    if args.command == "plan":
        _print_plan(plan)
        return 0
    if args.command == "status":
        record = read_record(record_path(config))
        for name in STAGE_ORDER:
            if job_id := record.jobs.get(name):
                print(f"{name}\t{job_id}")
        return 0

    existing: dict[str, str] = {}
    complete: tuple[str, ...] = ()
    if args.resume:
        require_approved_decisions(config)
        existing = read_record(record_path(config)).jobs
        selected = post_approval_submission(plan)
        complete = ("scan-decisions-approved",)
    else:
        selected = initial_submission(plan)
    record = submit_plan(
        selected, dry_run=args.dry_run,
        existing_jobs=existing, externally_completed=complete,
    )
    if not args.dry_run:
        write_record(record_path(config), record)
    for name, command in record.commands.items():
        state = "would submit" if args.dry_run else f"submitted {record.jobs[name]}"
        print(f"{name}: {state}\n  {' '.join(command)}")
    return 0


def stage_main(argv: list[str] | None = None) -> int:
    """Execute one private Slurm stage and save serial milestones explicitly."""

    args = stage_parser().parse_args(argv)
    config = WorkflowConfig.load(args.config)
    array_stages = {"fw2bids-array", "mriqc-array", "fmriprep-array"}
    if (args.stage in array_stages) != (args.array_index is not None):
        stage_parser().error("--array-index is required only for array stages")
    try:
        result = _run_stage(config, args.stage, args.array_index)
    except _validation_error_type() as error:
        # The validator itself atomically publishes both files.  Save those
        # diagnostics separately from a success milestone before preserving
        # its nonzero stage result.
        from network_fmri.milestones import save_diagnostic

        save_diagnostic(
            config.paths.bids_dir, args.stage,
            [error.result.report, error.result.log],
        )
        raise
    if args.stage not in array_stages and args.stage != "scan-decisions-approved":
        _save_stage_result(config, result)
    return 0


def _run_stage(config: WorkflowConfig, name: str, array_index: int | None):
    """Dispatch one graph node to the focused stage module that owns its work."""

    from network_fmri.containers import current_datalad_commit, write_subject_receipt
    from network_fmri.curation import apply_curation
    from network_fmri.prepare.b0link import link_b0
    from network_fmri.prepare.trim import trim_dataset
    from network_fmri.qa.fmriprep import (
        fmriprep_participant_command, fmriprep_subject_receipt, verify_fmriprep,
    )
    from network_fmri.qa.mriqc import (
        mriqc_group_command, mriqc_group_receipt, mriqc_participant_command,
        mriqc_subject_receipt, verify_mriqc,
    )
    from network_fmri.qa.validate import validate_bids
    from network_fmri.stages.assembly import assemble_dataset, convert_subject
    from network_fmri.stages.behavior import ingest_behavior
    from network_fmri.stages.decisions import generate_decisions, validate_decisions
    from network_fmri.stages.events import generate_events
    from network_fmri.stages.global_signal import run_global_signal

    if name == "fw2bids-array":
        return convert_subject(config, _array_subject(config, array_index))
    if name == "bids-assembled":
        return assemble_dataset(config)
    if name == "behavioral-sourcedata-ingested":
        return ingest_behavior(config)
    if name == "gs-pretrim":
        return run_global_signal(config.paths.bids_dir, "pretrim")
    if name == "dummy-volumes-trimmed":
        return trim_dataset(config.paths.bids_dir, jobs=config.slurm.cpus)
    if name == "bids-events-generated":
        return generate_events(config.paths.bids_dir)
    if name == "gs-posttrim":
        return run_global_signal(config.paths.bids_dir, "posttrim")
    if name == "b0-fieldmaps-linked":
        return link_b0(config.paths.bids_dir)
    if name == "bids-precuration-validated":
        return _validation_result("bids-precuration-validated", validate_bids(config.paths.bids_dir, "precuration"))
    if name == "mriqc-array":
        subject = _array_subject(config, array_index)
        subprocess.run(mriqc_participant_command(config, subject), check=True)
        commit = current_datalad_commit(config.paths.bids_dir)
        root = config.paths.bids_dir / "derivatives" / "mriqc"
        from network_fmri.containers import receipt_path
        write_subject_receipt(receipt_path(root, "mriqc", subject), mriqc_subject_receipt(config, subject, commit))
        return _array_result(name, subject)
    if name == "mriqc-complete":
        subprocess.run(mriqc_group_command(config), check=True)
        commit = current_datalad_commit(config.paths.bids_dir)
        from network_fmri.containers import group_receipt_path
        root = config.paths.bids_dir / "derivatives" / "mriqc"
        write_subject_receipt(group_receipt_path(root, "mriqc"), mriqc_group_receipt(config, commit))
        return verify_mriqc(config)
    if name == "scan-decisions-generated":
        return generate_decisions(config.paths.bids_dir)
    if name == "scan-decisions-approved":
        return validate_decisions(config.paths.bids_dir)
    if name == "mriqc-curated":
        manifest = config.paths.bids_dir / "code" / "network_fmri" / "scan_decisions.tsv"
        return apply_curation(config.paths.bids_dir, manifest)
    if name == "bids-curated-validated":
        return _validation_result("bids-curated-validated", validate_bids(config.paths.bids_dir, "curated"))
    if name == "fmriprep-array":
        subject = _array_subject(config, array_index)
        subprocess.run(fmriprep_participant_command(config, subject), check=True)
        commit = current_datalad_commit(config.paths.bids_dir)
        root = config.paths.bids_dir / "derivatives" / "fmriprep"
        from network_fmri.containers import receipt_path
        write_subject_receipt(receipt_path(root, "fmriprep", subject), fmriprep_subject_receipt(config, subject, commit))
        return _array_result(name, subject)
    if name == "fmriprep-complete":
        return verify_fmriprep(config)
    raise ValueError(f"unsupported pipeline stage: {name}")


def _array_subject(config: WorkflowConfig, index: int | None) -> str:
    if index is None or not 0 <= index < len(config.subjects):
        raise ValueError(f"array index must select one configured subject, got {index!r}")
    return config.subjects[index]


def _array_result(name: str, subject: str):
    from network_fmri.models import StageResult

    return StageResult(name, (), {"subject": subject})


def _validation_result(name: str, validation):
    from network_fmri.models import StageResult

    return StageResult(name, (validation.report, validation.log), {"label": validation.label})


def _save_stage_result(config: WorkflowConfig, result) -> None:
    """Create the sole DataLad save for a serial, successful graph node."""

    from network_fmri.milestones import MilestoneReceipt, save_milestone

    receipt = MilestoneReceipt(
        stage=result.name,
        status="success",
        inputs={},
        outputs={"paths": [str(path) for path in result.outputs]},
        versions={},
        jobs={"slurm_job_id": os.environ.get("SLURM_JOB_ID", "")},
        validation=result.details,
    )
    save_milestone(config.paths.bids_dir, receipt)


def _validation_error_type():
    """Import lazily so a plan does not require the validator executable."""

    from network_fmri.qa.validate import ValidationError

    return ValidationError
