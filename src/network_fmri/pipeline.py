"""The fixed, single-dataset Slurm graph and its minimal command interface."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from dataclasses import asdict, replace
from importlib.metadata import PackageNotFoundError, version
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


class ResumeError(RuntimeError):
    """Scheduler state cannot safely support a resume decision."""


def build_plan(
    config: WorkflowConfig, *, config_path: Path | None = None,
    pilot_subject: str | None = None,
) -> tuple[PlannedJob, ...]:
    """Construct the complete dependency graph for one exact subject roster."""

    if pilot_subject is not None and config.subjects != (pilot_subject,):
        raise ValueError("pilot worker commands require the matching one-subject roster")
    if pilot_subject is None and len(config.subjects) == 1:
        # Keep direct callers safe too: a derived pilot config must never create
        # workers that reload the original full roster from the TOML.
        pilot_subject = config.subjects[0]
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
            *(("--pilot-subject", pilot_subject) if pilot_subject else ()),
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
    if len(config.subjects) not in {1, 46}:
        raise ValueError("pipeline requires 46 configured subjects or one selected pilot subject")
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


def require_committed_approval(config: WorkflowConfig, runner=subprocess.run) -> None:
    """Require both a current approval seal and its committed milestone receipt."""

    require_approved_decisions(config, runner)
    from network_fmri.milestones import receipt_path

    bids_dir = config.paths.bids_dir
    receipt = receipt_path(bids_dir, "scan-decisions-approved")
    manifest = bids_dir / "code" / "network_fmri" / "scan_decisions.tsv"
    metadata = manifest.with_suffix(".meta.json")
    try:
        value = json.loads(_head_file(bids_dir, receipt, runner).decode("utf-8"))
        committed_manifest = _head_file(bids_dir, manifest, runner)
        committed_metadata = _head_file(bids_dir, metadata, runner)
        working_manifest = manifest.read_bytes()
        working_metadata = metadata.read_bytes()
    except (OSError, subprocess.CalledProcessError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "scan decisions are sealed but their approval milestone is not committed"
        ) from error
    if not isinstance(value, dict) or value.get("stage") != "scan-decisions-approved" or value.get("status") != "success":
        raise RuntimeError("committed scan-decision approval milestone is malformed")
    validation = value.get("validation")
    expected = {
        "manifest_sha256": _sha256(committed_manifest),
        "metadata_sha256": _sha256(committed_metadata),
    }
    if not isinstance(validation, dict) or any(validation.get(key) != digest for key, digest in expected.items()):
        raise RuntimeError("committed scan-decision approval hashes do not match HEAD")
    if _sha256(working_manifest) != expected["manifest_sha256"] or _sha256(working_metadata) != expected["metadata_sha256"]:
        raise RuntimeError("working scan decisions differ from the committed approval milestone")


def _head_file(bids_dir: Path, path: Path, runner) -> bytes:
    relative = path.relative_to(bids_dir).as_posix()
    completed = runner(
        ["git", "-C", str(bids_dir), "show", f"HEAD:{relative}"],
        check=True, capture_output=True,
    )
    value = completed.stdout
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):  # Keeps lightweight Runner test doubles usable.
        return value.encode("utf-8")
    raise UnicodeError(f"Git did not return bytes for {relative}")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


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
    pilot_subject = value.get("pilot_subject")
    if pilot_subject is not None and (not isinstance(pilot_subject, str) or not pilot_subject):
        raise RuntimeError(f"pipeline submission record has an invalid pilot subject: {path}")
    return SubmissionRecord(
        jobs={str(name): str(job_id) for name, job_id in jobs.items()},
        commands={str(name): tuple(map(str, command)) for name, command in commands.items()},
        dry_run=bool(value.get("dry_run", False)),
        status=str(value.get("status", "submitted")),
        error=value.get("error"),
        pilot_subject=pilot_subject,
    )


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="network-fmri pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "submit", "status"):
        command = commands.add_parser(name)
        command.add_argument("config", type=Path)
        if name in {"plan", "submit"}:
            command.add_argument("--pilot-subject")
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
    parser.add_argument("--pilot-subject")
    return parser


def _print_plan(plan: tuple[PlannedJob, ...]) -> None:
    for job in plan:
        after = ",".join(job.dependencies) or "none"
        array = " array" if job.array else ""
        print(f"{job.name}{array} after={after}: {' '.join(job.command)}")


def _unsubmitted(
    plan: tuple[PlannedJob, ...], existing_jobs: dict[str, str],
) -> tuple[PlannedJob, ...]:
    return tuple(job for job in plan if job.name not in existing_jobs)


def main(argv: list[str] | None = None, *, runner=None) -> int:
    supplied = list(argv or ())
    if supplied in (["-h"], ["--help"]):
        get_parser().print_help()
        return 0
    args = get_parser().parse_args(supplied)
    command_runner = runner or subprocess.run
    config = WorkflowConfig.load(args.config)
    pilot_subject = getattr(args, "pilot_subject", None)
    if pilot_subject:
        config = pilot_config(config, pilot_subject)
    plan = build_plan(config, config_path=args.config, pilot_subject=pilot_subject)
    if args.command == "plan":
        _print_plan(plan)
        return 0
    if args.command == "status":
        record = read_record(record_path(config))
        print(f"status\t{record.status}")
        if record.pilot_subject:
            print(f"pilot_subject\t{record.pilot_subject}")
        if record.error:
            print(f"error\t{record.error}")
        for name in STAGE_ORDER:
            if job_id := record.jobs.get(name):
                print(f"{name}\t{job_id}")
        return 0

    previous = _existing_record(config)
    if previous is not None and previous.pilot_subject != pilot_subject:
        raise RuntimeError(
            "pipeline submission record belongs to a different pilot selection; "
            "resume with the original --pilot-subject"
        )
    if previous is not None and not args.resume:
        raise RuntimeError(
            "a pipeline submission record already exists; use 'pipeline status' or '--resume'"
        )
    existing: dict[str, str] = previous.jobs if previous is not None else {}
    complete: tuple[str, ...] = ()
    if args.resume:
        active = _active_stages(previous, command_runner)
        if active:
            raise RuntimeError(
                "pipeline has active Slurm stages; wait before resuming: " + ", ".join(active)
            )
        completed = _completed_prefix(config, plan, previous, command_runner)
        first_missing = len(completed)
        approval_index = STAGE_ORDER.index("scan-decisions-approved")
        generated_index = STAGE_ORDER.index("scan-decisions-generated")
        if first_missing <= generated_index:
            selected = initial_submission(plan)[first_missing:]
        else:
            # A queued scan-decisions-generated job is never an approval.  The
            # committed manifest receipt is independently checked before curation.
            if first_missing == approval_index:
                if runner is None:
                    require_committed_approval(config)
                else:
                    require_committed_approval(config, command_runner)
                completed = _completed_prefix(config, plan, previous, command_runner)
                first_missing = len(completed)
            if first_missing <= approval_index:
                raise RuntimeError("scan decisions are not approved and committed")
            if runner is None:
                require_committed_approval(config)
            else:
                require_committed_approval(config, command_runner)
            selected = plan[first_missing:]
        existing = {name: job_id for name, job_id in existing.items() if name in completed}
        complete = tuple(completed)
    else:
        selected = initial_submission(plan)
    if not selected:
        return 0
    if not args.dry_run:
        config.paths.log_dir.mkdir(parents=True, exist_ok=True)
    record = submit_plan(
        selected, dry_run=args.dry_run,
        existing_jobs=existing, externally_completed=complete,
        runner=command_runner,
        on_update=(
            (lambda current: write_record(
                record_path(config), replace(current, pilot_subject=pilot_subject),
            ))
            if not args.dry_run else None
        ),
    )
    record = replace(record, pilot_subject=pilot_subject)
    if not args.dry_run:
        write_record(record_path(config), record)
    for name, command in record.commands.items():
        state = "would submit" if args.dry_run else f"submitted {record.jobs[name]}"
        print(f"{name}: {state}\n  {' '.join(command)}")
    return 0


def _existing_record(config: WorkflowConfig) -> SubmissionRecord | None:
    path = record_path(config)
    return read_record(path) if path.exists() else None


def _completed_prefix(
    config: WorkflowConfig,
    plan: tuple[PlannedJob, ...],
    record: SubmissionRecord | None,
    runner,
) -> tuple[str, ...]:
    """Return only the contiguous stages proved complete by durable evidence.

    Scheduler IDs document an attempted submission, not success.  Serial work is
    trusted only after its milestone receipt; array work also needs its worker
    outputs/receipts and a completed Slurm array.  A failed or dependency-cancelled
    node therefore causes it and every descendant to be submitted again.
    """

    record = record or SubmissionRecord()
    complete: list[str] = []
    for job in plan:
        if not _stage_completed(config, job, record, runner):
            break
        complete.append(job.name)
    return tuple(complete)


def _stage_completed(config: WorkflowConfig, job: PlannedJob, record: SubmissionRecord, runner) -> bool:
    from network_fmri.milestones import receipt_path

    if job.name == "fw2bids-array":
        return _array_job_completed(record.jobs.get(job.name), runner) and _part_roster_exists(config)
    if job.name == "mriqc-array":
        return _array_job_completed(record.jobs.get(job.name), runner) and _worker_receipts_exist(
            config.paths.bids_dir / "derivatives" / "mriqc", "mriqc", config.subjects,
        )
    if job.name == "fmriprep-array":
        return _array_job_completed(record.jobs.get(job.name), runner) and _worker_receipts_exist(
            config.paths.bids_dir / "derivatives" / "fmriprep", "fmriprep", config.subjects,
        )
    if job.name == "bids-curated-validated":
        report = config.paths.bids_dir / "derivatives" / "bids-validator" / "desc-curated_validation.json"
        return (
            _job_completed(record.jobs.get(job.name), runner)
            and _valid_json_object(report)
            and report.with_suffix(".log").is_file()
        )
    if job.name == "scan-decisions-approved":
        try:
            require_committed_approval(config, runner)
        except RuntimeError:
            return False
        return True
    return _successful_milestone(receipt_path(config.paths.bids_dir, job.name), job.name)


def _successful_milestone(path: Path, stage: str) -> bool:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and value.get("stage") == stage and value.get("status") == "success"


def _valid_json_object(path: Path) -> bool:
    try:
        return isinstance(json.loads(path.read_text()), dict)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def _part_roster_exists(config: WorkflowConfig) -> bool:
    try:
        return (
            config.paths.parts_dir.is_dir()
            and {entry.name for entry in config.paths.parts_dir.iterdir()} == set(config.subjects)
            and all((config.paths.parts_dir / subject).is_dir() for subject in config.subjects)
        )
    except OSError:
        return False


def _worker_receipts_exist(root: Path, application: str, subjects: tuple[str, ...]) -> bool:
    from network_fmri.containers import receipt_path

    for subject in subjects:
        try:
            receipt = json.loads(receipt_path(root, application, subject).read_text())
        except (OSError, UnicodeError, json.JSONDecodeError):
            return False
        if not isinstance(receipt, dict) or receipt.get("status") != "success" or receipt.get("subject") != subject:
            return False
    return True


def _array_job_completed(job_id: str | None, runner) -> bool:
    states = _job_states(job_id, runner)
    return bool(states) and all(state == "COMPLETED" for state in states)


def _active_stages(record: SubmissionRecord | None, runner) -> tuple[str, ...]:
    """Return submitted stages which Slurm still owns, never duplicating them."""

    if record is None:
        return ()
    active_states = {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED", "REQUEUED", "RESIZING"}
    return tuple(
        name for name, job_id in record.jobs.items()
        if any(state in active_states for state in _job_states(job_id, runner))
    )


def _job_completed(job_id: str | None, runner) -> bool:
    states = _job_states(job_id, runner)
    return bool(states) and all(state == "COMPLETED" for state in states)


def _job_states(job_id: str | None, runner) -> tuple[str, ...]:
    if not job_id:
        return ()
    try:
        result = runner(
            ["sacct", "--jobs", job_id, "--format=State", "--noheader", "--parsable2"],
            check=True, capture_output=True, text=True,
        )
        states = _state_lines(getattr(result, "stdout", ""))
    except (OSError, subprocess.CalledProcessError, AttributeError, IndexError) as error:
        raise ResumeError(f"cannot verify Slurm state for job {job_id} with sacct") from error
    if states:
        return states
    try:
        result = runner(
            ["squeue", "--jobs", job_id, "--noheader", "--format=%T"],
            check=True, capture_output=True, text=True,
        )
        states = _state_lines(getattr(result, "stdout", ""))
    except (OSError, subprocess.CalledProcessError, AttributeError, IndexError) as error:
        raise ResumeError(f"cannot verify Slurm state for job {job_id} with squeue") from error
    if not states:
        raise ResumeError(f"Slurm returned no state for recorded job {job_id}")
    return states


def _state_lines(output: object) -> tuple[str, ...]:
    return tuple(
        line.split("|", 1)[0].split()[0]
        for line in str(output).splitlines() if line.strip()
    )


def pilot_config(config: WorkflowConfig, subject: str) -> WorkflowConfig:
    """Derive a one-subject disposable pilot from a validated full-sample config.

    Loading still requires the reviewed 46-subject roster.  The CLI may then select
    one member only for a pilot whose runtime paths have been changed in a copied TOML.
    """

    if subject not in config.subjects:
        raise ValueError(f"pilot subject is not in the configured 46-subject roster: {subject}")
    return replace(config, subjects=(subject,))


def stage_main(argv: list[str] | None = None, *, runner=None) -> int:
    """Execute one private Slurm stage and save serial milestones explicitly."""

    args = stage_parser().parse_args(argv)
    config = WorkflowConfig.load(args.config)
    if args.pilot_subject:
        config = pilot_config(config, args.pilot_subject)
    array_stages = {"fw2bids-array", "mriqc-array", "fmriprep-array"}
    if (args.stage in array_stages) != (args.array_index is not None):
        stage_parser().error("--array-index is required only for array stages")
    try:
        result = (
            _run_stage(config, args.stage, args.array_index)
            if runner is None else _run_stage(config, args.stage, args.array_index, runner=runner)
        )
    except _validation_error_type() as error:
        # The validator itself atomically publishes both files.  Save those
        # diagnostics separately from a success milestone before preserving
        # its nonzero stage result.
        from network_fmri.milestones import save_diagnostic

        kwargs = {} if runner is None else {"runner": runner}
        save_diagnostic(
            config.paths.bids_dir, args.stage,
            [error.result.report, error.result.log], **kwargs,
        )
        raise
    if args.stage not in array_stages | {"bids-curated-validated"}:
        kwargs = {} if runner is None else {"runner": runner}
        save_stage_result(config.paths.bids_dir, result, config=config, **kwargs)
    return 0


def _run_stage(
    config: WorkflowConfig, name: str, array_index: int | None, *, runner=subprocess.run,
):
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
        return convert_subject(config, _array_subject(config, array_index), runner)
    if name == "bids-assembled":
        return assemble_dataset(config, runner)
    if name == "behavioral-sourcedata-ingested":
        return ingest_behavior(config, runner)
    if name == "gs-pretrim":
        return run_global_signal(config.paths.bids_dir, "pretrim", runner)
    if name == "dummy-volumes-trimmed":
        return trim_dataset(config.paths.bids_dir, jobs=config.slurm.cpus)
    if name == "bids-events-generated":
        return generate_events(config.paths.bids_dir, runner)
    if name == "gs-posttrim":
        return run_global_signal(config.paths.bids_dir, "posttrim", runner)
    if name == "b0-fieldmaps-linked":
        return link_b0(config.paths.bids_dir)
    if name == "bids-precuration-validated":
        return _validation_result(
            "bids-precuration-validated", validate_bids(config.paths.bids_dir, "precuration", runner),
        )
    if name == "mriqc-array":
        subject = _array_subject(config, array_index)
        runner(mriqc_participant_command(config, subject), check=True)
        commit = current_datalad_commit(config.paths.bids_dir, runner)
        root = config.paths.bids_dir / "derivatives" / "mriqc"
        from network_fmri.containers import receipt_path
        write_subject_receipt(receipt_path(root, "mriqc", subject), mriqc_subject_receipt(config, subject, commit))
        return _array_result(name, subject)
    if name == "mriqc-complete":
        runner(mriqc_group_command(config), check=True)
        commit = current_datalad_commit(config.paths.bids_dir, runner)
        from network_fmri.containers import group_receipt_path
        root = config.paths.bids_dir / "derivatives" / "mriqc"
        write_subject_receipt(group_receipt_path(root, "mriqc"), mriqc_group_receipt(config, commit))
        return verify_mriqc(config, runner)
    if name == "scan-decisions-generated":
        return generate_decisions(config.paths.bids_dir, runner)
    if name == "scan-decisions-approved":
        return validate_decisions(config.paths.bids_dir, runner)
    if name == "mriqc-curated":
        manifest = config.paths.bids_dir / "code" / "network_fmri" / "scan_decisions.tsv"
        return apply_curation(config.paths.bids_dir, manifest, runner)
    if name == "bids-curated-validated":
        return _validation_result(
            "bids-curated-validated", validate_bids(config.paths.bids_dir, "curated", runner),
        )
    if name == "fmriprep-array":
        subject = _array_subject(config, array_index)
        runner(fmriprep_participant_command(config, subject), check=True)
        commit = current_datalad_commit(config.paths.bids_dir, runner)
        root = config.paths.bids_dir / "derivatives" / "fmriprep"
        from network_fmri.containers import receipt_path
        write_subject_receipt(receipt_path(root, "fmriprep", subject), fmriprep_subject_receipt(config, subject, commit))
        return _array_result(name, subject)
    if name == "fmriprep-complete":
        return verify_fmriprep(config, runner)
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


def save_stage_result(
    bids_dir: Path, result, *, config: WorkflowConfig | None = None, runner=None,
) -> None:
    """Create the sole DataLad save for a serial, successful graph node."""

    from network_fmri.milestones import MilestoneReceipt, save_milestone

    versions = _receipt_versions(config)
    input_commit = (
        _input_datalad_commit(bids_dir)
        if runner is None else _input_datalad_commit(bids_dir, runner)
    )
    inputs = {
        "bids_dir": str(bids_dir),
        "input_datalad_commit": input_commit,
    }
    if config is not None:
        inputs.update({
            "behavior_source": str(config.behavior.source),
            "behavior_commit": config.behavior.commit,
        })
    receipt = MilestoneReceipt(
        stage=result.name,
        status="success",
        inputs=inputs,
        outputs={"paths": [str(path) for path in result.outputs]},
        versions=versions,
        jobs={"slurm_job_id": os.environ.get("SLURM_JOB_ID", "")},
        validation=_receipt_validation(result),
    )
    if runner is None:
        save_milestone(bids_dir, receipt)
    else:
        save_milestone(bids_dir, receipt, runner)


def _receipt_validation(result) -> dict[str, object]:
    validation = dict(result.details)
    if result.name != "scan-decisions-approved":
        return validation
    if len(result.outputs) != 2:
        raise ValueError("scan-decision approval must declare manifest and metadata outputs")
    manifest, metadata = result.outputs
    try:
        validation.update({
            "manifest_sha256": _sha256(Path(manifest).read_bytes()),
            "metadata_sha256": _sha256(Path(metadata).read_bytes()),
        })
    except OSError as error:
        raise ValueError("scan-decision approval outputs are unavailable for receipt") from error
    return validation


def _receipt_versions(config: WorkflowConfig | None) -> dict[str, object]:
    """Record package and configured-container identities without credentials."""

    from network_fmri import __version__
    from network_fmri.provenance import code_revision

    versions: dict[str, object] = {
        "network_fmri": __version__,
        "network_fmri_revision": code_revision(),
        "network_fw2bids": _package_version("network-fw2bids"),
        "network_events": _package_version("network-events"),
        "network_qa": _package_version("network-qa"),
    }
    if config is not None:
        versions.update({
            "mriqc": {"image": str(config.mriqc.image), "version": config.mriqc.version},
            "fmriprep": {
                "image": str(config.fmriprep.image), "version": config.fmriprep.version,
            },
        })
    return versions


def _package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "not-installed"


def _input_datalad_commit(bids_dir: Path, runner=subprocess.run) -> str:
    from network_fmri.milestones import git_head

    return git_head(bids_dir, runner)


def _validation_error_type():
    """Import lazily so a plan does not require the validator executable."""

    from network_fmri.qa.validate import ValidationError

    return ValidationError
