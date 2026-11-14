"""Replay and measure N4 weighting maps without changing fMRIPrep derivatives.

The replay command runs inside the campaign's exact fMRIPrep container on a
compute node. It uses archived HMC references and recorded program seeds, never
an old Nipype work tree. An independent HMC automask and the archived final brain
mask are comparators, never inputs to the registration being checked.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib
import zipfile

import nibabel as nib
import numpy as np

VERSION = "25.2.7.dev5+g7cf086b9b"
ARCHIVED_RELATIVE_MEDIAN_MIN = 0.68
SUFFIXES = {
    "hmc": "_desc-hmc_boldref.nii.gz",
    "coreg": "_desc-coreg_boldref.nii.gz",
    "mask": "_desc-brain_mask.nii.gz",
    "t1w": "_space-T1w_boldref.nii.gz",
    "t1w_mask": "_space-T1w_desc-brain_mask.nii.gz",
}


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _same_grid(first, second):
    if len(first.shape) != 3 or first.shape != second.shape or not np.allclose(
        first.affine, second.affine, atol=1e-4, rtol=0
    ):
        raise ValueError("images must share a three-dimensional grid")


def weight_metrics(weight_image, brain_image):
    """Coverage uses weights > .5; retain continuous weights and physical offset."""
    _same_grid(weight_image, brain_image)
    weights = weight_image.get_fdata()
    brain_values = brain_image.get_fdata()
    if not np.isfinite(weights).all() or not np.isfinite(brain_values).all():
        raise ValueError("weight and brain images must be finite")
    if np.any(weights < -1e-5) or np.any(weights > 1.00001):
        raise ValueError("weights must be probabilities between zero and one")
    brain = brain_values > 0
    mapped = weights > 0.5
    nbrain, nmapped = int(brain.sum()), int(mapped.sum())
    if not nbrain:
        raise ValueError("empty brain mask")
    overlap = int((brain & mapped).sum())
    distance = None
    if nmapped:
        centers = np.stack([np.argwhere(brain).mean(0), np.argwhere(mapped).mean(0)])
        centers = nib.affines.apply_affine(brain_image.affine, centers)
        distance = float(np.linalg.norm(centers[0] - centers[1]))
    return {
        "brain_voxels": nbrain,
        "weight_voxels": nmapped,
        "brain_coverage": overlap / nbrain,
        "weight_dice": 2 * overlap / (nbrain + nmapped),
        "mean_brain_weight": float(weights[brain].mean()),
        "weight_fraction_inside_brain": float(weights[brain].sum() / weights.sum()) if weights.sum() else 0.0,
        "centroid_distance_mm": distance,
    }


def classify(metrics, *, fail_below=0.25, pass_at=0.75):
    """Provisional overlap thresholds; calibration and visual review are required."""
    if not 0 <= fail_below < pass_at <= 1:
        raise ValueError("invalid coverage thresholds")
    coverage = metrics["brain_coverage"]
    if not np.isfinite(coverage) or not 0 <= coverage <= 1:
        raise ValueError("invalid coverage")
    return "flagged" if coverage < fail_below else "borderline" if coverage < pass_at else "pass"


def intensity_metrics(image, mask):
    _same_grid(image, mask)
    values = image.get_fdata()[mask.get_fdata() > 0]
    if not len(values) or not np.isfinite(values).all():
        raise ValueError("empty or nonfinite brain intensities")
    lo, median, hi = np.percentile(values, [2, 50, 98])
    return {"p2": float(lo), "p50": float(median), "p98": float(hi),
            "relative_median": float((median - lo) / (hi - lo)) if hi > lo else None}


def review_reasons(row):
    """Screen archived intensity as well as the imperfect registration replay.

    Campaign calibration separates damaged archived references (<= .655)
    from usable ones (>= .704). The conservative .68 threshold
    and .05 replay disagreement trigger inspection, never automatic repair.
    """
    if row['status'] not in {'pass', 'flagged', 'borderline'}:
        return []
    reasons = [row['status']] if row['status'] != 'pass' else []
    values = [row.get('intensities', {}).get(label, {}).get('relative_median')
              for label in ('coreg', 'corrected')]
    if any(value is None or not np.isfinite(value) or not 0 <= value <= 1 for value in values):
        return reasons + ['invalid-intensity']
    archived, replayed = values
    if archived < ARCHIVED_RELATIVE_MEDIAN_MIN:
        reasons.append('archived-intensity')
    if abs(archived - replayed) > 0.05:
        reasons.append('replay-disagreement')
    return reasons


def _run_key(path):
    return "_".join(
        item for item in Path(path).name.removesuffix("_bold.nii.gz").split("_")
        if not item.startswith("echo-")
    )


def inventory(raw, archives, subjects_file):
    """Account for each raw acquisition, even when its output archive is missing."""
    subjects = [line.strip().removeprefix("sub-") for line in subjects_file.read_text().splitlines() if line.strip()]
    if len(subjects) != 46 or len(set(subjects)) != 46 or not all(re.fullmatch(r"s[0-9]+", s) for s in subjects):
        raise ValueError("expected exactly 46 distinct reviewed subjects")
    commit = subprocess.check_output(["git", "-C", str(archives), "rev-parse", "HEAD"], text=True).strip()
    raw_commit = subprocess.check_output(["git", "-C", str(raw), "rev-parse", "HEAD"], text=True).strip()
    rows = []
    for subject in subjects:
        expected = sorted({_run_key(p) for p in (raw / f"sub-{subject}").glob("ses-*/func/*_bold.nii.gz")})
        if not expected:
            raise ValueError(f"no input BOLD runs for sub-{subject}")
        # HMC is the enhancement input only when no SBRef replaces it.
        if list((raw / f"sub-{subject}").glob("ses-*/func/*_sbref.nii*")):
            raise ValueError(f"SBRef replay is not supported: sub-{subject}")
        matches = list(archives.glob(f"sub-{subject}_fMRIPrep-*.zip"))
        if len(matches) > 1:
            raise ValueError(f"ambiguous archive for sub-{subject}")
        source = matches[0] if matches else None
        base = {"subject": subject, "archive": str(source) if source else None,
                "annex_key": Path(os.readlink(source)).name if source and source.is_symlink() else None}
        if source is None or not source.is_file():
            rows.extend(dict(base, run=key, status="missing-archive" if source is None else "unavailable-archive") for key in expected)
            continue
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate archive members: {source}")
            configs = [n for n in names if f"/sub-{subject}/log/" in n and n.endswith("/fmriprep.toml")]
            if len(configs) != 1:
                raise ValueError(f"expected one archived configuration: {subject}")
            config_data = archive.read(configs[0])
            config = tomllib.loads(config_data.decode())
            if config["environment"]["version"] != VERSION:
                raise ValueError(f"unexpected fMRIPrep version: {subject}")
            base.update(seeds=config["seeds"], omp_nthreads=config["nipype"]["omp_nthreads"],
                        config_member=configs[0], config_sha256=hashlib.sha256(config_data).hexdigest())
            hmc_keys = {Path(n).name.removesuffix(SUFFIXES["hmc"]) for n in names if n.endswith(SUFFIXES["hmc"])}
            if hmc_keys - set(expected):
                raise ValueError(f"unexpected archived runs: {sorted(hmc_keys - set(expected))}")
            for key in expected:
                members = {}
                for label, suffix in SUFFIXES.items():
                    found = [n for n in names if Path(n).name == key + suffix]
                    if len(found) > 1:
                        raise ValueError(f"ambiguous {label}: {key}")
                    if found:
                        members[label] = found[0]
                rows.append(dict(base, run=key, members=members,
                                 status="ready" if len(members) == len(SUFFIXES) else "incomplete-output"))
    return {"schema_version": 1, "archive_commit": commit, "raw_commit": raw_commit,
            "raw_root": str(raw.resolve()), "archive_root": str(archives.resolve()),
            "subjects": subjects, "subjects_sha256": sha256(subjects_file), "runs": rows}


def replay(hmc, work, seeds, threads):
    """Use the installed workflow unmodified through N4, discarding later nodes."""
    import fmriprep
    import networkx as nx
    import niworkflows
    from niworkflows.func.util import init_enhance_and_skullstrip_bold_wf
    from nipype import config

    if fmriprep.__version__ != VERSION or niworkflows.__version__ != "1.15.3":
        raise ValueError("replay requires the exact campaign image")
    os.environ["ANTS_RANDOM_SEED"] = str(seeds["ants"])
    np.random.seed(seeds["numpy"])
    config.update_config({"execution": {"remove_unnecessary_outputs": False, "stop_on_first_crash": True}})
    wf = init_enhance_and_skullstrip_bold_wf(omp_nthreads=threads)
    if tuple(wf.get_node("init_aff").inputs.transform) != ("Rigid", 0.1):
        raise ValueError("unexpected initializer")
    wf.base_dir = str(work)
    wf.inputs.inputnode.in_file = str(hmc)
    final = wf.get_node("n4_correct")
    keep = nx.ancestors(wf._graph, final) | {final}
    wf.remove_nodes([n for n in list(wf._graph) if n not in keep])
    graph = wf.run(plugin="Linear")
    nodes = {n.name: n for n in graph.nodes()}
    result = nodes["n4_correct"].result
    return {
        "weight": result.inputs["weight_image"], "corrected": result.outputs.output_image,
        "commands": {name: nodes[name].result.runtime.cmdline for name in ("init_aff", "norm", "map_brainmask", "n4_correct")},
        "versions": {"fmriprep": fmriprep.__version__, "niworkflows": niworkflows.__version__},
    }


def montage(paths, weight, target, title, *, n4_label='Replayed N4', coreg_label='Archived coreg'):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    images = [nib.as_closest_canonical(nib.load(paths[k])).get_fdata() for k in ("hmc", "corrected", "coreg")]
    weights = nib.as_closest_canonical(nib.load(weight)).get_fdata()
    brain = nib.as_closest_canonical(nib.load(paths["mask"])).get_fdata() > 0
    center = np.median(np.argwhere(brain), axis=0).astype(int)
    fig, axes = plt.subplots(3, 5, figsize=(15, 9))
    for row, (data, label) in enumerate(zip(images, ('HMC input', n4_label, coreg_label))):
        lo, hi = np.percentile(data[brain & np.isfinite(data)], [2, 98])
        for col, offset in enumerate((-12, -6, 0, 6, 12)):
            z = int(np.clip(center[2] + offset, 0, data.shape[2] - 1))
            ax = axes[row, col]
            ax.imshow(data[:, :, z].T, origin="lower", cmap="gray", vmin=lo, vmax=hi)
            ax.contour(brain[:, :, z].T, levels=[0.5], colors="cyan", linewidths=0.5)
            if row < 2 and np.any(weights[:, :, z] > 0.5):
                ax.contour(weights[:, :, z].T, levels=[0.5], colors="orange", linewidths=0.7)
            ax.set_title(f"{label}, z={z}")
            ax.axis("off")
    fig.suptitle(title + "\ncyan: independent HMC automask; orange: N4 weights; per-row 2–98% scale")
    fig.tight_layout()
    fig.savefig(target, dpi=120)
    plt.close(fig)


def audit_run(row, output, work_root, *, force_review=False):
    if row["status"] != "ready":
        return row
    target = output / row["run"]
    target.mkdir(parents=True, exist_ok=False)
    if row["annex_key"] and (not Path(row["archive"]).is_symlink() or Path(os.readlink(row["archive"])).name != row["annex_key"]):
        raise ValueError("source archive annex key changed")
    with tempfile.TemporaryDirectory(prefix=row["run"] + "-", dir=work_root) as directory:
        work = Path(directory)
        paths, hashes = {}, {}
        with zipfile.ZipFile(row["archive"]) as archive:
            if hashlib.sha256(archive.read(row["config_member"])).hexdigest() != row["config_sha256"]:
                raise ValueError("source configuration changed")
            for label, member in row["members"].items():
                dest = work / (label + ".nii.gz")
                with archive.open(member) as src, dest.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
                paths[label] = dest
                hashes[label] = sha256(dest)
        replayed = replay(paths["hmc"], work / "replay", row["seeds"], row["omp_nthreads"])
        from nipype.interfaces import afni
        independent = afni.Automask(in_file=str(paths["hmc"]), out_file=str(work / "automask.nii.gz"),
                                   outputtype="NIFTI_GZ", dilate=1, num_threads=1).run(cwd=str(work))
        paths["archived_mask"] = paths["mask"]
        paths["mask"] = Path(independent.outputs.out_file)
        metrics = weight_metrics(nib.load(replayed["weight"]), nib.load(paths["mask"]))
        archived_metrics = weight_metrics(nib.load(replayed["weight"]), nib.load(paths["archived_mask"]))
        paths["corrected"] = Path(replayed["corrected"])
        intensities = {label: intensity_metrics(nib.load(paths[label]), nib.load(paths["mask"])) for label in ("hmc", "corrected", "coreg")}
        intensities["t1w"] = intensity_metrics(nib.load(paths["t1w"]), nib.load(paths["t1w_mask"]))
        result = dict(row, status=classify(metrics), metrics=metrics, intensities=intensities,
                      input_sha256=hashes, commands=replayed["commands"], versions=replayed["versions"],
                      slurm_job_id=os.environ.get("SLURM_JOB_ID"), archived_mask_metrics=archived_metrics,
                      comparator="AFNI 3dAutomask on archived HMC reference, dilate=1")
        result["detector_sha256"] = sha256(__file__)
        result["automask_command"] = independent.runtime.cmdline
        shutil.copyfile(replayed["weight"], target / "weight.nii.gz")
        result["weight_sha256"] = sha256(target / "weight.nii.gz")
        shutil.copyfile(paths["mask"], target / "automask.nii.gz")
        result["automask_sha256"] = sha256(target / "automask.nii.gz")
        result['review_reasons'] = review_reasons(result)
        if result['review_reasons'] or force_review:
            montage(paths, replayed["weight"], target / "review.png", f'{row["run"]}: coverage {metrics["brain_coverage"]:.3f}, Dice {metrics["weight_dice"]:.3f}')
        (target / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def summarize(manifest, results):
    """Missing runs never disappear from the table or count as passing evidence."""
    by_run = {}
    expected = {row["run"]: row for row in manifest["runs"]}
    for result in results:
        key = result["run"]
        if key in by_run or key not in expected:
            raise ValueError(f"duplicate or unexpected audit result: {key}")
        for field in ("archive", "annex_key", "config_sha256", "seeds", "members"):
            if result.get(field) != expected[key].get(field):
                raise ValueError(f"audit source mismatch: {key}: {field}")
        if result["status"] not in {"pass", "flagged", "borderline"} or expected[key]["status"] != "ready":
            raise ValueError(f"invalid audit status: {key}")
        by_run[key] = result
    if len({row.get("detector_sha256") for row in by_run.values()}) > 1:
        raise ValueError("cannot combine different detector versions")
    rows = []
    for key, source in expected.items():
        rows.append(by_run.get(key, dict(source, status="not-audited" if source["status"] == "ready" else source["status"])))
    counts = {status: sum(row["status"] == status for row in rows) for status in sorted({row["status"] for row in rows})}
    assessed = sum(counts.get(status, 0) for status in ("pass", "flagged", "borderline"))
    flagged = sum(row['status'] == 'flagged' or 'archived-intensity' in review_reasons(row)
                  for row in rows)
    return {"schema_version": 1, "archive_commit": manifest["archive_commit"],
            "raw_commit": manifest["raw_commit"], "counts": counts, "runs": rows,
            "assessed": assessed, "screen_flagged": flagged,
            "flagged_fraction": flagged / assessed if assessed else None,
            "audit_complete": not counts.get("not-audited", 0),
            "stop_before_reruns": not assessed or bool(counts.get("not-audited", 0)) or flagged > 0.1 * assessed}


def write_summary(summary, output):
    output.mkdir(parents=True, exist_ok=False)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    columns = ["run", "subject", "status", "brain_coverage", "weight_dice", "mean_brain_weight",
               "centroid_distance_mm", "hmc_p50", "coreg_p50", "coreg_relative_median", "t1w_p50",
               "corrected_relative_median", "review_reasons",
               "master_seed", "ants_seed", "numpy_seed", "archive", "annex_key"]
    with (output / "runs.tsv").open("w") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in summary["runs"]:
            values = {**row, **row.get("metrics", {})}
            for label in ("hmc", "coreg", "t1w"):
                values[label + "_p50"] = row.get("intensities", {}).get(label, {}).get("p50")
            values["coreg_relative_median"] = row.get("intensities", {}).get("coreg", {}).get("relative_median")
            values['corrected_relative_median'] = row.get('intensities', {}).get('corrected', {}).get('relative_median')
            values['review_reasons'] = ';'.join(review_reasons(row))
            values.update({key + "_seed": value for key, value in row.get("seeds", {}).items()})
            writer.writerow(values)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inv = commands.add_parser("inventory")
    for name in ("raw", "archives", "subjects-file", "output"):
        inv.add_argument("--" + name, required=True, type=Path)
    audit = commands.add_parser("audit")
    for name in ("manifest", "output", "work-root"):
        audit.add_argument("--" + name, required=True, type=Path)
    audit.add_argument("--subject", required=True)
    audit.add_argument("--run", action="append")
    summary_parser = commands.add_parser("summarize")
    for name in ("manifest", "results", "output"):
        summary_parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "inventory":
        result = inventory(args.raw, args.archives, args.subjects_file)
        with args.output.open("x") as stream:
            json.dump(result, stream, indent=2)
        return
    if args.command == "summarize":
        summary = summarize(json.loads(args.manifest.read_text()),
                            [json.loads(p.read_text()) for p in sorted(args.results.glob("**/result.json"))])
        write_summary(summary, args.output)
        print(json.dumps({k: v for k, v in summary.items() if k != "runs"}))
        return
    if not os.environ.get("SLURM_JOB_ID"):
        raise RuntimeError("audit must run in a Slurm allocation")
    manifest = json.loads(args.manifest.read_text())
    rows = [r for r in manifest["runs"] if r["subject"] == args.subject and (not args.run or r["run"] in args.run)]
    if not rows or (args.run and set(args.run) != {r["run"] for r in rows}):
        raise ValueError("requested run selection does not match inventory")
    for root in (manifest["raw_root"], manifest["archive_root"]):
        if args.output.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError("audit output must be outside the source datasets")
    args.output.mkdir(parents=True, exist_ok=True)
    args.work_root.mkdir(parents=True, exist_ok=True)
    for row in rows:
        result = audit_run(row, args.output, args.work_root)
        print(json.dumps({"run": row["run"], "status": result["status"], "metrics": result.get("metrics")}), flush=True)


if __name__ == "__main__":
    main()
