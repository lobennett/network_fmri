"""Submit network_glm's runners as Slurm arrays.

Owns the fan-out, the resources and the host modules each level needs. Modelling flags
pass through after ``--``, so what they mean stays network_glm's business; ``--space`` is
the exception, since it decides the lev2 fan-out and which modules to load.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from network_fmri.cohorts import COHORTS, DEFAULT_STAGING, roster

GLM = str(Path(sys.executable).parent / "network-glm")
SURFACE_SPACES = ("surface", "fsaverage6", "fsLR")


def _split_passthrough(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split our own flags from everything after ``--``, which is the runner's."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1:]
    return argv, []


def _write_list(log_dir: Path, name: str, lines: list[str]) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / name
    path.write_text("\n".join(lines) + "\n")
    return path


def _sbatch(name: str, body: str, args: argparse.Namespace, log_dir: Path,
            n_tasks: int | None) -> str:
    """Submit one job, or an array of ``n_tasks``. Returns the Slurm job id."""
    cmd = ["sbatch", "-J", name, "-p", args.partition,
           "-c", str(args.cpus), f"--mem={args.mem_gb}G", "-t", args.time,
           "-o", f"{log_dir}/{name}-%A-%a.out", "-e", f"{log_dir}/{name}-%A-%a.err"]
    if n_tasks is not None:
        cmd.append(f"--array=1-{n_tasks}%{args.throttle}")
    if args.dependency:
        cmd.append(f"--dependency=afterok:{args.dependency}")
    cmd += ["--wrap", body]
    if args.print_only:
        print(f"  {' '.join(cmd[:-1])}\n  --wrap:\n{body}")
        return "DRY-RUN"
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return out.stdout.strip().split()[-1]


# Per level: lev1 is memory-hungry and slow, lev2 permutation-bound but small.
DEFAULTS = {
    "lev1": dict(cpus=1, mem_gb=64, time="2-00:00:00"),
    "lev2": dict(cpus=2, mem_gb=4, time="04:00:00"),
    "outliers": dict(cpus=2, mem_gb=16, time="01:00:00"),
}


def _common(p: argparse.ArgumentParser, level: str) -> None:
    d = DEFAULTS[level]
    p.add_argument("--partition", default="russpold,normal")
    p.add_argument("--cpus", type=int, default=d["cpus"])
    p.add_argument("--mem-gb", type=int, default=d["mem_gb"])
    p.add_argument("--time", default=d["time"])
    p.add_argument("--throttle", type=int, default=20)
    p.add_argument("--dependency", default=None, help="Slurm job id to wait on")
    p.add_argument("--log-dir", default=None, help="default: <results-dir>/logs")
    p.add_argument("--print", dest="print_only", action="store_true")


def lev1(argv: list[str] | None = None) -> int:
    """One array task per (subject, task)."""
    from network_glm.task_config.loader import get_all_tasks, get_base_tasks, get_dual_tasks

    own, extra = _split_passthrough(list(sys.argv[1:] if argv is None else argv))
    p = argparse.ArgumentParser(prog="network_fmri glm-lev1",
                                epilog="Flags after -- go to `network-glm lev1`.")
    p.add_argument("--cohort", choices=list(COHORTS))
    p.add_argument("--subjects", nargs="+")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--tasks", nargs="+")
    g.add_argument("--all", dest="taskset", action="store_const", const="all")
    g.add_argument("--base-tasks", dest="taskset", action="store_const", const="base")
    g.add_argument("--dual-tasks", dest="taskset", action="store_const", const="dual")
    p.add_argument("--results-dir", required=True)
    p.add_argument("--space", default="MNI")
    _common(p, "lev1")
    args = p.parse_args(own)

    subjects = args.subjects or (roster(args.cohort) if args.cohort else None)
    if not subjects:
        raise SystemExit("need --cohort or --subjects")
    tasks = {"all": get_all_tasks, "base": get_base_tasks,
             "dual": get_dual_tasks}[args.taskset]() if args.taskset else args.tasks

    # sub- prefixed: the runner interpolates subj_id raw into output filenames.
    pairs = [f"sub-{s.removeprefix('sub-')} {t}" for s in subjects for t in tasks]
    log_dir = Path(args.log_dir or Path(args.results_dir) / "logs")
    listfile = _write_list(log_dir, "lev1_units.txt", pairs)

    # mri_surf2surf is only reached from the surface branch, and only when smoothing.
    modules = ""
    if args.space in SURFACE_SPACES and "--smoothing-fwhm" in extra:
        modules = "module load biology freesurfer/8.1.0\n"

    body = (f'set -euo pipefail\n{modules}'
            f'UNIT="$(sed -n "${{SLURM_ARRAY_TASK_ID}}p" {listfile})"\n'
            f'{GLM} lev1 --subj-id "${{UNIT%% *}}" --task-name "${{UNIT##* }}" '
            f'--results-dir {args.results_dir} --space {args.space} {" ".join(extra)}')
    job = _sbatch("glm-lev1", body, args, log_dir, len(pairs))
    print(f"  glm-lev1 {job}  ({len(subjects)} subjects x {len(tasks)} tasks = {len(pairs)} tasks)")
    return 0


def lev2(argv: list[str] | None = None) -> int:
    """One array task per contrast, discovered from the level-1 outputs."""
    from network_glm.lev2.discover import discover_contrasts_from_lev1_dirs
    from network_glm.task_config.loader import get_base_tasks, get_dual_tasks

    own, extra = _split_passthrough(list(sys.argv[1:] if argv is None else argv))
    p = argparse.ArgumentParser(prog="network_fmri glm-lev2",
                                epilog="Flags after -- go to `network-glm lev2`.")
    p.add_argument("--lev1-dirs", nargs="+", required=True)
    p.add_argument("--results-dir", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--contrasts", nargs="+")
    g.add_argument("--all", dest="taskset", action="store_const", const="all")
    g.add_argument("--base-tasks", dest="taskset", action="store_const", const="base")
    g.add_argument("--dual-tasks", dest="taskset", action="store_const", const="dual")
    p.add_argument("--space", default="volume")
    _common(p, "lev2")
    args = p.parse_args(own)

    if args.contrasts:
        contrasts = args.contrasts
    else:
        task_filter = {"all": None, "base": get_base_tasks(), "dual": get_dual_tasks()}[args.taskset]
        contrasts = discover_contrasts_from_lev1_dirs(
            args.lev1_dirs, task_filter=task_filter, space=args.space)
    if not contrasts:
        raise SystemExit(f"no contrasts found under {' '.join(args.lev1_dirs)}")

    log_dir = Path(args.log_dir or Path(args.results_dir) / "logs")
    listfile = _write_list(log_dir, "lev2_contrasts.txt", contrasts)

    # randomise is FSL; the surface path is self-contained.
    modules = "" if args.space == "surface" else "module load biology fsl\n"

    body = (f'set -euo pipefail\n{modules}'
            f'CONTRAST="$(sed -n "${{SLURM_ARRAY_TASK_ID}}p" {listfile})"\n'
            f'{GLM} lev2 --contrast "$CONTRAST" --output-dir {args.results_dir} '
            f'--space {args.space} {" ".join(extra)}')
    job = _sbatch("glm-lev2", body, args, log_dir, len(contrasts))
    print(f"  glm-lev2 {job}  ({len(contrasts)} contrasts)")
    return 0


def outliers(argv: list[str] | None = None) -> int:
    """A single job: cohort-level outlier detection over the level-1 maps."""
    own, extra = _split_passthrough(list(sys.argv[1:] if argv is None else argv))
    p = argparse.ArgumentParser(prog="network_fmri glm-outliers",
                                epilog="Flags after -- go to `network-glm cohort-outliers`.")
    p.add_argument("--results-dir", required=True)
    _common(p, "outliers")
    args = p.parse_args(own)

    log_dir = Path(args.log_dir or Path(args.results_dir) / "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    body = (f'set -euo pipefail\n'
            f'{GLM} cohort-outliers --output-dir {args.results_dir} {" ".join(extra)}')
    job = _sbatch("glm-outliers", body, args, log_dir, None)
    print(f"  glm-outliers {job}")
    return 0
