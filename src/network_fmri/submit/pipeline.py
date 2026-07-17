"""Submit the full Flywheel->BIDS->events->datalad DAG for one cohort.

Chains the per-stage submit modules with ``sbatch --dependency=afterok:<prev>``
so each stage only starts after the previous finishes successfully:

    curate -> export(array) -> merge -> trim(array) -> events -> datalad

``events`` is skipped for the ``excluded`` cohort (no reconciliation manifest).
Each stage is rendered by its own submit module (same resources/template as
``fw2bids submit <stage>``); this wrapper only adds the dependency wiring.
"""

from __future__ import annotations

import argparse
import sys

from network_fmri.submit import _common, curate, datalad, events, export, merge, select, trim
from network_fmri.submit._slurm import parse_job_id, submit_sbatch

# Full ordered DAG. Each entry is (stage-name, submit-module, is-array).
# ``select`` is the terminal stage: it renders the data-selection channels into
# the DataLad-tracked tree, so it must run AFTER ``datalad``.
_STAGES = [
    ("curate", curate, True),
    ("export", export, True),
    ("merge", merge, False),
    ("trim", trim, True),
    ("events", events, False),
    ("datalad", datalad, False),
    ("select", select, False),
]

# Cohort-gated stages: dropped when the cohort isn't in the stage's cohort set.
_COHORT_GATED = {
    "events": _common.EVENTS_COHORTS,
    "select": _common.SELECT_COHORTS,
}


def stages_for(cohort: str):
    """The DAG stages for a cohort (drops ``events``/``select`` for ``excluded``)."""
    return [
        (name, mod, is_array)
        for name, mod, is_array in _STAGES
        if name not in _COHORT_GATED or cohort in _COHORT_GATED[name]
    ]


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Submit the full network_fmri Flywheel->BIDS pipeline DAG for a cohort"
    )
    parser.add_argument("--cohort", required=True, choices=list(_common.COHORTS))
    parser.add_argument("--staging", default=_common.DEFAULT_STAGING)
    parser.add_argument("--parts", default=None)
    parser.add_argument("--partition", default="normal")
    parser.add_argument("--mail-user", default=None)
    parser.add_argument(
        "--container",
        nargs="?",
        const=_common.DEFAULT_CONTAINER_IMAGE,
        default=None,
    )
    parser.add_argument(
        "--start-stage",
        choices=[name for name, _, _ in _STAGES],
        default=None,
        help="Resume the DAG from this stage (skip earlier ones whose outputs "
             "already exist, e.g. resume from 'merge' when parts/ are present). "
             "The start stage runs with no dependency.",
    )
    parser.add_argument("--throttle", type=int, default=5)
    parser.add_argument("--subjects", nargs="+", default=None)
    parser.add_argument("--behavioral-dir", default=events.DEFAULT_BEHAVIORAL_DIR)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--dry-run", action="store_true", default=False)
    return parser


def _stage_argv(args: argparse.Namespace, *, is_array: bool, is_events: bool) -> list[str]:
    """Build the CLI argv a stage's own parser expects, from pipeline args."""
    argv = ["--cohort", args.cohort, "--staging", args.staging, "--partition", args.partition]
    if args.parts:
        argv += ["--parts", args.parts]
    if args.mail_user:
        argv += ["--mail-user", args.mail_user]
    if args.container:
        argv += ["--container", args.container]
    if is_array:
        argv += ["--throttle", str(args.throttle)]
        if args.subjects:
            argv += ["--subjects", *args.subjects]
    if is_events:
        argv += ["--behavioral-dir", args.behavioral_dir]
        if args.manifest:
            argv += ["--manifest", args.manifest]
    return argv


def main(argv: list[str] | None = None) -> int:
    args = get_parser().parse_args(argv)

    stages = stages_for(args.cohort)
    if args.start_stage:
        names = [n for n, _, _ in stages]
        if args.start_stage not in names:
            raise SystemExit(
                f"--start-stage {args.start_stage!r} not a stage for cohort "
                f"{args.cohort!r}; choices: {names}"
            )
        stages = stages[names.index(args.start_stage):]

    job_ids: dict[str, str] = {}
    prev: str | None = None
    for name, mod, is_array in stages:
        stage_args = mod.get_parser().parse_args(
            _stage_argv(args, is_array=is_array, is_events=(name == "events"))
        )
        script = mod.render(stage_args)
        dep = f"afterok:{prev}" if prev else "(none)"
        if args.dry_run:
            print(f"\n===== stage: {name}  (dependency: {dep}) =====")
            print(script)
            prev = f"<{name}-jobid>"
            job_ids[name] = prev
        else:
            out = submit_sbatch(script, dependency=prev)
            jid = parse_job_id(out)
            job_ids[name] = jid
            prev = jid

    print("\n=== submitted DAG ===")
    for name, _, _ in stages:
        print(f"{name}: {job_ids[name]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
