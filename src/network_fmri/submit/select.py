"""Submit the ``select`` stage: render the pass-1 data-selection channels.

Terminal DAG stage. Shells ``network-qa`` over the DataLad-tracked cohort tree
to produce the fMRIPrep-INDEPENDENT selection channels (same orchestration
pattern as ``events`` shelling ``network-events``):

    network-qa compile --generators short_run behavioral --bids-dir <cohort>
        --out <cohort>/code/exclusions_lock.json
    network-qa render bidsignore  --lockfile … --out <cohort>/.bidsignore
    network-qa render scans-tsv   --lockfile … --bids-dir <cohort>
    network-qa render bids-filter --anat-acquisition … --task … --out
        <cohort>/code/bids-filter_<pipeline>.json      (one per pipeline)

**Two-pass reality.** Pass 1 (this stage) runs only the fMRIPrep-independent
generators — ``short_run`` (dim4 vs per-task mode) + ``behavioral`` (missing /
non-monotonic events). Pass 2 (later, in the network_glm/QA phase) re-compiles
adding ``motion`` + ``lev1_outlier`` (which need fMRIPrep confounds / lev1 QC)
and re-renders. Pass 2 is NOT part of this stage.

Skipped for the ``excluded`` cohort — it has no events / selection layer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from network_fmri.submit import _common

STAGE = "select"
DEFAULT_RESOURCES = {"nthreads": 2, "mem_gb": 8, "time": "01:00:00"}

# Pass-1 generators: fMRIPrep-INDEPENDENT only (no motion / lev1_outlier).
DEFAULT_GENERATORS = ["short_run", "behavioral"]


def _selection_config_path() -> Path:
    """Locate ``config/selection.json`` in both layouts.

    Prefer the package-local copy (the wheel force-includes ``config`` at
    ``network_fmri/config``); fall back to the repo-root ``config/`` for the
    editable/dev checkout.
    """
    here = Path(__file__).resolve()
    pkg = here.parents[1] / "config" / "selection.json"  # network_fmri/config
    if pkg.is_file():
        return pkg
    return here.parents[3] / "config" / "selection.json"  # <repo root>/config


def load_selection_config() -> dict:
    """Return the ``pipelines`` mapping from ``config/selection.json``."""
    cfg = json.loads(_selection_config_path().read_text())
    return cfg["pipelines"]


def _bids_filter_block(args: argparse.Namespace, cohort_dir: str) -> str:
    """Build the shell block that renders one bids-filter file per pipeline.

    ``--anat-acquisition`` / ``--tasks`` CLI overrides (when given) apply to
    every selected pipeline; otherwise each pipeline's values come from
    ``config/selection.json``.
    """
    pipelines = load_selection_config()
    names = args.pipeline or sorted(pipelines)
    lines: list[str] = []
    for name in names:
        if name not in pipelines:
            raise SystemExit(
                f"select: pipeline {name!r} not in selection.json "
                f"(have: {sorted(pipelines)})"
            )
        pcfg = pipelines[name]
        anat = args.anat_acquisition or pcfg["anat_acquisition"]
        tasks = args.tasks or pcfg["tasks"]
        task_args = " ".join(f"--task {t}" for t in tasks)
        lines.append(
            f"{{run_prefix}} network-qa render bids-filter \\\n"
            f"    --anat-acquisition {anat} \\\n"
            f"    {task_args} \\\n"
            f"    --out {cohort_dir}/code/bids-filter_{name}.json"
        )
    return "\n\n".join(lines)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit the network_fmri select SLURM job")
    _common.add_common_args(parser)
    parser.add_argument(
        "--generators",
        nargs="+",
        default=list(DEFAULT_GENERATORS),
        help="network-qa generators to compile (default: pass-1 fMRIPrep-independent "
        f"set {DEFAULT_GENERATORS})",
    )
    parser.add_argument(
        "--pipeline",
        nargs="+",
        default=None,
        help="pipeline(s) to render a bids-filter for (default: all in selection.json)",
    )
    parser.add_argument(
        "--anat-acquisition",
        default=None,
        help="override the canonical anat acquisition (default: from selection.json)",
    )
    parser.add_argument(
        "--tasks",
        nargs="+",
        default=None,
        help="override the bids-filter task list (default: from selection.json)",
    )
    return parser


def render(args: argparse.Namespace) -> str:
    if args.cohort not in _common.SELECT_COHORTS:
        raise SystemExit(
            f"select stage is not defined for cohort {args.cohort!r} "
            f"(no selection layer); valid: {_common.SELECT_COHORTS}"
        )
    ctx = _common.single_context(args, DEFAULT_RESOURCES, stage=STAGE)
    ctx["generators"] = " ".join(args.generators)
    # str.format-based render: the block itself carries `{run_prefix}` markers,
    # so format it against ctx before injecting (it has no other placeholders).
    block = _bids_filter_block(args, ctx["cohort_dir"]).format(**ctx)
    ctx["bids_filter_block"] = block
    return _common.render(STAGE, ctx)


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)
    return _common.finish(render(args), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
