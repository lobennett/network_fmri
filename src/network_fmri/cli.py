"""The intentionally small public command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from network_fmri import pipeline
from network_fmri.curation import apply_curation
from network_fmri.stages.decisions import generate_decisions, validate_decisions
from network_fmri.config import WorkflowConfig
from network_fmri.qa.freesurfer import validate_surface_review
from network_fmri.processing import ProcessingManager
from network_fmri.study import StudyManager


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="network-fmri")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("pipeline", help="plan, submit, or inspect the fixed workflow")
    study = commands.add_parser("study", help="create or verify the MechaBABS input study")
    study_commands = study.add_subparsers(dest="study_command", required=True)
    study_init = study_commands.add_parser("init")
    study_init.add_argument("config", type=Path)
    study_init.add_argument("--pilot-subject")
    processing = commands.add_parser("processing", help="plan, advance, or inspect MechaBABS")
    processing_commands = processing.add_subparsers(dest="processing_command", required=True)
    processing_plan = processing_commands.add_parser("plan")
    processing_plan.add_argument("config", type=Path)
    processing_plan.add_argument("--pilot-subject")
    processing_status = processing_commands.add_parser("status")
    processing_status.add_argument("config", type=Path)
    processing_advance = processing_commands.add_parser("advance")
    processing_advance.add_argument("config", type=Path)
    processing_advance.add_argument(
        "--stage", required=True, choices=("mriqc", "anatomical", "fmriprep")
    )
    decisions = commands.add_parser("decisions", help="validate reviewed scan decisions")
    decision_commands = decisions.add_subparsers(dest="decision_command", required=True)
    for name in ("generate", "validate"):
        decision = decision_commands.add_parser(name)
        decision.add_argument("bids_dir", type=Path)
    surfaces = commands.add_parser("surfaces", help="validate reviewed FreeSurfer surfaces")
    surface_commands = surfaces.add_subparsers(dest="surface_command", required=True)
    surface_validate = surface_commands.add_parser("validate")
    surface_validate.add_argument("config", type=Path)
    surface_validate.add_argument("--pilot-subject")
    curate = commands.add_parser("curate", help="apply approved drop decisions")
    curate.add_argument("bids_dir", type=Path)
    curate.add_argument("--validator-image", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["-h"], ["--help"]):
        get_parser().print_help()
        return 0
    if args and args[0] == "pipeline":
        return pipeline.main(args[1:])
    if args and args[0] == "_stage":
        return pipeline.stage_main(args[1:])
    parsed = get_parser().parse_args(args)
    if parsed.command == "study":
        config = WorkflowConfig.load(parsed.config)
        if parsed.pilot_subject:
            config = pipeline.pilot_config(config, parsed.pilot_subject)
        if config.mechababs is None:
            raise ValueError("workflow configuration is missing [mechababs]")
        result = StudyManager(config.mechababs, config.paths.bids_dir).initialize(
            subjects=config.subjects
        )
        for key in (
            "study_id", "raw_dataset_id", "raw_commit", "study_dir", "campaign_dir", "created",
        ):
            print(f"{key}\t{getattr(result, key)}")
        return 0
    if parsed.command == "processing":
        config = WorkflowConfig.load(parsed.config)
        if getattr(parsed, "pilot_subject", None):
            config = pipeline.pilot_config(config, parsed.pilot_subject)
        manager = ProcessingManager(config)
        if parsed.processing_command == "plan":
            for stage in manager.plan():
                print(f"{stage.stage}\t{stage.state}\t{stage.application}")
        elif parsed.processing_command == "status":
            status = manager.status()
            for stage in status.stages:
                print(f"{stage.stage}\t{stage.state}\t{stage.application}")
            for job in status.jobs:
                print("job\t" + json.dumps(job, sort_keys=True))
        else:
            result = manager.advance(parsed.stage)
            action = "advanced" if result.advanced else "complete"
            print(f"{result.stage}\t{action}\t{result.previous_state}")
        return 0
    if parsed.command == "decisions":
        result = (
            generate_decisions(parsed.bids_dir)
            if parsed.decision_command == "generate"
            else validate_decisions(parsed.bids_dir)
        )
        pipeline.save_stage_result(parsed.bids_dir, result)
        return 0
    if parsed.command == "surfaces":
        config = WorkflowConfig.load(parsed.config)
        if parsed.pilot_subject:
            config = pipeline.pilot_config(config, parsed.pilot_subject)
        result = validate_surface_review(config)
        review_dataset = (
            config.mechababs.study_dir
            if getattr(config, "mechababs", None) is not None
            else config.paths.bids_dir
        )
        pipeline.save_stage_result(review_dataset, result, config=config)
        return 0
    manifest = parsed.bids_dir / "code" / "network_fmri" / "scan_decisions.tsv"
    result = apply_curation(parsed.bids_dir, manifest, parsed.validator_image)
    pipeline.save_stage_result(parsed.bids_dir, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
