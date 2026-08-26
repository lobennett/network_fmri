"""Does dropping RT make each task's MAIN contrast less sparse? Paired within subject."""
import sys
from pathlib import Path
import numpy as np, nibabel as nib

RT, NO = Path(sys.argv[1]), Path(sys.argv[2])
MAIN = [("flanker", "incongruent-congruent"), ("stopSignal", "stop_success-go"),
        ("nBack", "twoBack-oneBack"), ("cuedTS", "task_switch_cost"),
        ("spatialTS", "task_switch_cost"), ("goNogo", "nogo_success-go"),
        ("directedForgetting", "neg-con"), ("shapeMatching", "main_vars")]
THR = 2.3

def frac(p):
    d = np.asarray(nib.load(p).dataobj, dtype=np.float32)
    m = np.isfinite(d) & (d != 0)
    return float((np.abs(d[m]) > THR).sum()) / int(m.sum()) if m.sum() else np.nan

def get(root, task, con, arm):
    pat = f"*task-{task}_contrast-{con}_rtmodel-{arm}_stat-fixed-effects-z_score.nii.gz"
    return {p.name.split("_")[0]: p for p in sorted(root.rglob(pat))}

print(f"  fraction of in-mask voxels with |z| > {THR}\n")
print(f"  {'task':20s} {'contrast':40s} {'RTDur':>7s} {'noRT':>7s} {'Δ':>8s}  n up")
for task, con in MAIN:
    a, b = get(RT, task, con, "RTDur"), get(NO, task, con, "noRT")
    subs = sorted(set(a) & set(b))
    if not subs:
        print(f"  {task:20s} {con:40s}  -- no paired maps --"); continue
    d = np.array([frac(b[s]) - frac(a[s]) for s in subs])
    ra = np.mean([frac(a[s]) for s in subs]); rb = np.mean([frac(b[s]) for s in subs])
    print(f"  {task:20s} {con:40s} {ra*100:6.2f}% {rb*100:6.2f}% "
          f"{d.mean()*100:+7.2f}pp  {int((d>0).sum())}/{len(d)}")
