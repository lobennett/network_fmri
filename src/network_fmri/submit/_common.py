"""Shared plumbing for the per-stage submit modules.

Holds the *container seam* (the single thing that changes when the
``network_fmri.sif`` container lands), roster resolution, the common CLI
arguments every stage takes, and the base sbatch context (resources, log dir,
mail line, run prefix, env exports).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from network_fmri import curation
from network_fmri.submit._slurm import (
    build_mail_line,
    make_log_dir,
    render_template,
    resolve_resources,
    submit_sbatch,
    write_list_file,
)

TEMPLATE_DIR = Path(__file__).parent / "templates"

COHORTS = ("discovery", "validation", "excluded")

# Cohorts that have a reconciliation manifest → get an events stage. ``excluded``
# has no behavioral reconciliation manifest, so events is skipped for it.
EVENTS_COHORTS = ("discovery", "validation")

DEFAULT_STAGING = "/scratch/users/logben/bids_staging"

# Mirrors network_glm's DEFAULT_CONTAINER_IMAGE. The image need NOT exist yet —
# the default run path is the uv scratch venv (see ``resolve_run_prefix``); this
# is only used when ``--container`` is passed with no explicit path.
DEFAULT_CONTAINER_IMAGE = "/home/groups/russpold/singularity_images/network_fmri.sif"

# Env exports for the "now" (no container) path: the scratch uv venv + cache, the
# editable source on PYTHONPATH, uv on PATH, and HOME pinned (the login profile's
# HOME is fine but we set it explicitly so a bare `sbatch` env is reproducible).
UV_ENV_EXPORTS = "\n".join(
    [
        "export UV_PROJECT_ENVIRONMENT=/scratch/users/logben/network_fmri_venv",
        "export UV_CACHE_DIR=/scratch/users/logben/uv_cache",
        "export PYTHONPATH=/home/users/logben/network_fmri/src",
        "export PATH=/share/software/user/open/uv/0.9.5/bin:$PATH",
        "export HOME=/home/users/logben",
    ]
)

DEFAULT_RUN_PREFIX = "uv run --no-sync"


def resolve_run_prefix(container: str | None) -> dict:
    """Container seam: choose the command prefix + env setup for a stage payload.

    - ``container`` is ``None`` (default, "now"): run via the scratch uv venv,
      exporting the UV_* / PYTHONPATH / PATH / HOME env so a bare ``sbatch``
      shell resolves the editable package.
    - ``container`` is a path ("later"): run via ``apptainer exec <sif>`` — no
      host venv env needed. This is the ONLY change when the container lands.
    """
    if container:
        return {"run_prefix": f"apptainer exec {container}", "env_exports": ""}
    return {"run_prefix": DEFAULT_RUN_PREFIX, "env_exports": UV_ENV_EXPORTS}


def resolve_roster(args) -> list[str]:
    """Subjects to operate on: ``--subjects`` override, else the cohort roster.

    ``curation.roster`` already yields sorted keys when the cohort sample is a
    ``{subject: reason}`` dict (``excluded``), so this works for all cohorts.
    """
    if getattr(args, "subjects", None):
        return list(args.subjects)
    return curation.roster(args.cohort)


def add_common_args(parser: argparse.ArgumentParser, *, array: bool = False) -> None:
    """Add the CLI arguments shared by every stage (and the pipeline wrapper)."""
    parser.add_argument("--cohort", required=True, choices=list(COHORTS))
    parser.add_argument(
        "--staging",
        default=DEFAULT_STAGING,
        help=f"staging BIDS root (default: {DEFAULT_STAGING})",
    )
    parser.add_argument(
        "--parts",
        default=None,
        help="per-subject export parts dir (default: <staging>/parts)",
    )
    parser.add_argument(
        "--partition", default="normal", help="SLURM partition (default: normal)"
    )
    parser.add_argument("--nthreads", type=int, default=None, help="CPUs per task override")
    parser.add_argument("--mem-gb", type=int, default=None, help="Memory in GB override")
    parser.add_argument("--time", default=None, help="SLURM time limit override (D-HH:MM:SS)")
    parser.add_argument("--mail-user", default=None)
    parser.add_argument(
        "--container",
        nargs="?",
        const=DEFAULT_CONTAINER_IMAGE,
        default=None,
        help="run payload via `apptainer exec <sif>` instead of the uv scratch venv "
        f"(bare --container uses {DEFAULT_CONTAINER_IMAGE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="render the sbatch script to stdout without submitting",
    )
    if array:
        parser.add_argument(
            "--subjects",
            nargs="+",
            default=None,
            help="subjects to run (default: whole cohort roster); one array task each",
        )
        parser.add_argument(
            "--throttle",
            type=int,
            default=5,
            help="max concurrent array tasks (the %%K in --array=0-N%%K)",
        )


def parts_dir(args) -> Path:
    """``--parts`` if given, else ``<staging>/parts``."""
    return Path(args.parts) if args.parts else Path(args.staging) / "parts"


def cohort_dir(args) -> Path:
    """The staged BIDS tree for this cohort: ``<staging>/<cohort>``."""
    return Path(args.staging) / args.cohort


def base_context(args, defaults: dict, *, job_name: str, log_dir: Path) -> dict:
    """Resources + log dir + mail + run-prefix/env — the fields every template needs."""
    resources = resolve_resources(args, defaults)
    run = resolve_run_prefix(args.container)
    return {
        "job_name": job_name,
        "nthreads": resources["nthreads"],
        "mem_gb": resources["mem_gb"],
        "time": resources["time"],
        "partition": args.partition,
        "log_dir": str(log_dir),
        "mail_line": build_mail_line(args.mail_user),
        "run_prefix": run["run_prefix"],
        "env_exports": run["env_exports"],
    }


def make_stage_log_dir(args) -> Path:
    """``<staging>/<cohort>/logs`` (created)."""
    return make_log_dir(cohort_dir(args))


def array_context(args, defaults: dict, *, stage: str) -> tuple[dict, list[str]]:
    """Context for a per-subject ``--array`` stage (curate/export/trim).

    Writes the subjects list file the array reads and computes ``n_minus_1``
    (``--array=0-N`` is 0-indexed, so the last index is ``len - 1``).
    """
    subjects = resolve_roster(args)
    log_dir = make_stage_log_dir(args)
    subjects_file = write_list_file(log_dir, f"{stage}_subjects.txt", subjects)
    ctx = base_context(args, defaults, job_name=f"nf-{stage}-{args.cohort}", log_dir=log_dir)
    ctx.update(
        {
            "n_minus_1": len(subjects) - 1,
            "throttle": args.throttle,
            "subjects_file": str(subjects_file),
            "cohort": args.cohort,
            "cohort_dir": str(cohort_dir(args)),
            "parts": str(parts_dir(args)),
        }
    )
    return ctx, subjects


def single_context(args, defaults: dict, *, stage: str) -> dict:
    """Context for a single (non-array) stage (merge/events/datalad)."""
    log_dir = make_stage_log_dir(args)
    ctx = base_context(args, defaults, job_name=f"nf-{stage}-{args.cohort}", log_dir=log_dir)
    ctx.update(
        {
            "cohort": args.cohort,
            "cohort_dir": str(cohort_dir(args)),
            "parts": str(parts_dir(args)),
        }
    )
    return ctx


def render(stage: str, ctx: dict) -> str:
    """Render ``templates/<stage>.sbatch.tmpl`` with ``ctx``."""
    return render_template(TEMPLATE_DIR / f"{stage}.sbatch.tmpl", ctx)


def finish(script: str, *, dry_run: bool, dependency: str | None = None) -> int:
    """Print (dry-run) or submit the rendered script. Returns an exit code."""
    if dry_run:
        print(script)
        return 0
    submit_sbatch(script, dependency=dependency)
    return 0
