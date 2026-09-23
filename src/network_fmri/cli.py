"""The intentionally small public command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from network_fmri import pipeline
from network_fmri.curation import apply_curation
from network_fmri.stages.decisions import validate_decisions
from network_fmri.config import WorkflowConfig
from network_fmri.qa.freesurfer import validate_surface_review


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="network-fmri")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("pipeline", help="plan, submit, or inspect the fixed workflow")
    decisions = commands.add_parser("decisions", help="validate reviewed scan decisions")
    decision_commands = decisions.add_subparsers(dest="decision_command", required=True)
    validate = decision_commands.add_parser("validate")
    validate.add_argument("bids_dir", type=Path)
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
    if parsed.command == "decisions":
        result = validate_decisions(parsed.bids_dir)
        pipeline.save_stage_result(parsed.bids_dir, result)
        return 0
    if parsed.command == "surfaces":
        config = WorkflowConfig.load(parsed.config)
        if parsed.pilot_subject:
            config = pipeline.pilot_config(config, parsed.pilot_subject)
        result = validate_surface_review(config)
        pipeline.save_stage_result(config.paths.bids_dir, result, config=config)
        return 0
    manifest = parsed.bids_dir / "code" / "network_fmri" / "scan_decisions.tsv"
    apply_curation(parsed.bids_dir, manifest, parsed.validator_image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
