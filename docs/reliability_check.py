"""Run-to-run reproducibility: sustained contrast vs differential contrast.
If task-baseline reproduces and the differential does not, the inputs are fine."""
import os, itertools
from pathlib import Path
import numpy as np, pandas as pd, nibabel as nib

R = Path(os.environ["SCRATCH"]) / "network_fmri/discovery/lev1"
T = [("flanker","incongruent-congruent"),("cuedTS","task_switch_cost"),
     ("nBack","twoBack-oneBack"),("goNogo","nogo_success-go")]

def load(p):
    return np.asarray(nib.load(p).dataobj, np.float32)

def mean_pairwise_r(paths):
    arrs = [load(p) for p in paths]
    rs = []
    for a, b in itertools.combinations(arrs, 2):
        m = np.isfinite(a) & np.isfinite(b) & (a != 0) & (b != 0)
        if m.sum() > 1000:
            rs.append(np.corrcoef(a[m], b[m])[0, 1])
    return (float(np.mean(rs)), float(np.min(rs)), float(np.max(rs)), len(rs)) if rs else (np.nan,)*3+(0,)

print(f"{'subject':9s} {'task':11s} {'contrast':24s} {'mean r':>7s} {'min':>7s} {'max':>7s} {'pairs':>6s}")
for sub in ("sub-s03", "sub-s10", "sub-s19"):
    for task, con in T:
        ic = R / sub / f"task-{task}" / "indiv_contrasts"
        if not ic.is_dir(): continue
        for c in ("task-baseline", con):
            ps = sorted(ic.glob(f"*contrast-{c}_*z_score.nii.gz"))
            if len(ps) < 2: continue
            mr, lo, hi, n = mean_pairwise_r(ps)
            print(f"{sub:9s} {task:11s} {c:24s} {mr:+7.3f} {lo:+7.3f} {hi:+7.3f} {n:6d}")
    print()
