"""Sparsity of lev1 fixed-effects z maps, RTDur vs noRT, paired within subject.

Sparsity = fraction of in-mask voxels past a |z| threshold. Paired because the two arms
differ only in the design, so subject is the natural block.
"""
import sys
from pathlib import Path

import numpy as np
import nibabel as nib

ARMS = {"RTDur": Path(sys.argv[1]), "noRT": Path(sys.argv[2])}
TASKS = sys.argv[3].split(",")
THRESHOLDS = (2.3, 3.1)


def maps(root: Path, task: str, contrast: str, arm: str) -> dict[str, Path]:
    pat = f"*task-{task}_contrast-{contrast}_rtmodel-{arm}_stat-fixed-effects-z_score.nii.gz"
    return {p.name.split("_")[0]: p for p in sorted(root.rglob(pat))}


def sparsity(path: Path) -> tuple[dict[float, float], float]:
    d = np.asarray(nib.load(path).dataobj, dtype=np.float32)
    inmask = np.isfinite(d) & (d != 0)
    n = int(inmask.sum())
    if not n:
        return {t: float("nan") for t in THRESHOLDS}, float("nan")
    a = np.abs(d[inmask])
    return {t: float((a > t).sum()) / n for t in THRESHOLDS}, float(np.percentile(a, 95))


for task in TASKS:
    for contrast in ("incongruent-congruent", "task-baseline"):
        got = {arm: maps(root, task, contrast, arm) for arm, root in ARMS.items()}
        subs = sorted(set(got["RTDur"]) & set(got["noRT"]))
        if not subs:
            continue
        print(f"\n=== task-{task} / {contrast}  ({len(subs)} subjects paired) ===")
        hdr = "  sub      " + "".join(f"|z|>{t}: RTDur  noRT   Δ      " for t in THRESHOLDS)
        print(hdr)
        agg = {t: [] for t in THRESHOLDS}
        for s in subs:
            r, _ = sparsity(got["RTDur"][s])
            n, _ = sparsity(got["noRT"][s])
            row = f"  {s:8s} "
            for t in THRESHOLDS:
                d = n[t] - r[t]
                agg[t].append(d)
                row += f"{r[t]*100:6.2f}% {n[t]*100:6.2f}% {d*100:+6.2f}pp  "
            print(row)
        print("  " + "-" * 68)
        for t in THRESHOLDS:
            a = np.array(agg[t])
            sign = "MORE" if a.mean() > 0 else "LESS"
            print(f"  |z|>{t}: mean Δ {a.mean()*100:+.2f}pp  "
                  f"({sign} suprathreshold without RT in {int((a > 0).sum())}/{len(a)} subjects)")
