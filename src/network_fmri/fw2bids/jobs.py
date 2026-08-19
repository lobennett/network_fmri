"""Run the Flywheel -> BIDS stage: render the array, import one subject, merge the parts.

`submit` renders template.sbatch and hands it to sbatch. `import_subject` is what each
array task runs: it creates the subject's own dataset and `datalad run`s curate+export
inside it, so 40+ tasks never contend on one git index. `merge` rsyncs the parts into the
cohort dataset.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from network_fmri import provenance
from network_fmri.cohorts import COHORTS, DEFAULT_STAGING, roster
from network_fmri.fw2bids.curate import HEURISTIC

TEMPLATE = Path(__file__).parent / "template.sbatch"
DEFAULT_PROJECT = "r01network"


def get_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="network_fmri submit fw-heudiconv")
    p.add_argument("--project", default=DEFAULT_PROJECT, help="Flywheel project label")
    p.add_argument("--cohort", choices=list(COHORTS), help="submit this cohort's roster")
    p.add_argument("--subject", nargs="+", help="explicit subjects instead of --cohort")
    p.add_argument("--staging", default=DEFAULT_STAGING, help=f"default: {DEFAULT_STAGING}")
    p.add_argument("--heuristic", default=str(HEURISTIC), help=f"default: {HEURISTIC}")
    p.add_argument("--live", action="store_true",
                   help="tag Flywheel and export to <staging>/<cohort>/parts/<subject>")
    p.add_argument("--partition", default="normal")
    p.add_argument("--cpus", type=int, default=2)
    p.add_argument("--mem-gb", type=int, default=8)
    p.add_argument("--time", default="04:00:00")
    # 3 concurrent: Flywheel returns sporadic HTTP 500s at 8.
    p.add_argument("--throttle", type=int, default=3,
                   help="max concurrent array tasks (the %%K in --array=0-N%%K)")
    p.add_argument("--template", default=str(TEMPLATE))
    p.add_argument("--print", dest="print_only", action="store_true",
                   help="print the rendered script instead of submitting")
    return p


def render(args: argparse.Namespace) -> str:
    subjects = args.subject or (roster(args.cohort) if args.cohort else None)
    if not subjects:
        get_parser().error("need --cohort or --subject")
    name = args.cohort or "adhoc"

    # Outside the BIDS tree: sbatch logs inside a dataset trip bids-validator.
    log_dir = Path(args.staging) / "logs" / name
    log_dir.mkdir(parents=True, exist_ok=True)
    subjects_file = log_dir / f"{name}_subjects.txt"
    subjects_file.write_text("\n".join(subjects) + "\n")

    return Path(args.template).read_text().format(
        job_name=f"nf-{name}",
        partition=args.partition,
        cpus=args.cpus,
        mem_gb=args.mem_gb,
        time=args.time,
        log_dir=log_dir,
        last=len(subjects) - 1,
        throttle=args.throttle,
        subjects_file=subjects_file,
        # Absolute path to this venv's console script: no PATH setup in the job.
        network_fmri=Path(sys.executable).parent / "network_fmri",
        project=args.project,
        heuristic=args.heuristic,
        cohort=name,
        staging=args.staging,
        live=" --live" if args.live else "",
    )


def sbatch_array(args: argparse.Namespace) -> str:
    """Render the per-subject array and submit it, returning the Slurm job id.

    Separate from :func:`submit` so ``pipeline`` can chain the rest of the stages onto
    this array with ``--dependency=afterok``.
    """
    script = render(args)
    with tempfile.NamedTemporaryFile("w", suffix=".sbatch", delete=False) as f:
        f.write(script)
    out = subprocess.run(["sbatch", f.name], capture_output=True, text=True, check=True)
    return out.stdout.strip().split()[-1]


def submit(argv: list[str]) -> int:
    args = get_parser().parse_args(argv)
    if args.print_only:
        print(render(args))
        return 0
    print(f"submitted array {sbatch_array(args)}")
    return 0


def import_subject(argv: list[str]) -> int:
    """Curate + export one subject inside its own dataset, via ``datalad run``.

    A dataset per subject keeps 40+ array tasks from contending on one git index,
    while still recording the command and outputs in history.
    """
    p = argparse.ArgumentParser(prog="network_fmri import-subject")
    p.add_argument("--project", default=DEFAULT_PROJECT)
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--subject", required=True)
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--heuristic", default=str(HEURISTIC))
    p.add_argument("--live", action="store_true")
    p.add_argument("--retries", type=int, default=3)
    args = p.parse_args(argv)

    ds = Path(args.staging) / args.cohort / "parts" / args.subject
    env = provenance.datalad_env()
    provenance.ensure_dataset(ds, env)

    payload = [
        str(Path(sys.executable).parent / "network_fmri"), "curate",
        "--project", args.project, "--subject", args.subject,
        "--heuristic", args.heuristic, "--retries", str(args.retries),
    ]
    if args.live:
        # Relative to the dataset root, so the recorded command is portable.
        payload += ["--live", "--out", "bids"]

    provenance.run_recorded(
        ds, payload,
        f"network_fmri@{provenance.code_version()}: import {args.subject} "
        f"({'live' if args.live else 'dry run'})",
        outputs=["bids"] if args.live else [],
        env=env,
    )
    return 0


def merge(argv: list[str]) -> int:
    """rsync per-subject exports into one BIDS tree, recorded with ``datalad run``.

    The parts datasets are outside the cohort dataset, so their commits go in the
    run message rather than being pinned as ``--input``.
    """
    p = argparse.ArgumentParser(prog="network_fmri merge")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    args = p.parse_args(argv)

    parts = Path(args.staging) / args.cohort / "parts"
    dest = Path(args.staging) / args.cohort / "bids"
    sources = sorted(d for d in parts.glob("*/bids") if d.is_dir())
    if not sources:
        raise SystemExit(f"no per-subject exports under {parts}/*/bids")

    env = provenance.datalad_env()
    provenance.ensure_dataset(dest, env)
    provenance_note = " ".join(
        f"{s.parent.name}@{provenance.subject_commit(s.parent)}" for s in sources
    )
    # -L dereferences: the parts are datasets, so their NIfTIs are annex symlinks
    # into a .git/annex this dataset does not have. Without it we commit dangling links.
    script = "; ".join(f"rsync -aL {s}/ ." for s in sources)
    provenance.run_recorded(
        dest, ["bash", "-c", script],
        f"network_fmri@{provenance.code_version()}: merge {args.cohort} "
        f"({len(sources)} subjects) from {provenance_note}",
        outputs=["."],
        env=env,
    )
    print(f"merged {len(sources)} subjects -> {dest}")
    return 0
