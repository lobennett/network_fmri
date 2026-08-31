"""Could the nuisance regressors be eating the conflict effect?

The confound set is 24 motion + cosines + spikes. If subjects move or drift differentially
with trial type, those columns absorb exactly the contrast of interest. Measure how much of
the difference regressor is explained by the nuisance block (R^2), vs the same for the sum
and for nBack as a control.
"""
import os, glob, re
import numpy as np, pandas as pd

S = os.environ["SCRATCH"]
NUIS = ("cosine", "trans_", "rot_", "motion_outlier", "constant")
CASES = [("flanker", ("incongruent", "congruent")),
         ("cuedTS", ("task_switch_cue_switch", "task_stay_cue_switch")),
         ("nBack", ("mismatch_2back", "mismatch_1back"))]

def r2_on(y, N):
    b, *_ = np.linalg.lstsq(N, y, rcond=None)
    resid = y - N @ b
    return 1 - resid.var() / y.var() if y.var() > 0 else np.nan

print(f"  {'task':9s} {'ses':6s} {'R2(diff|nuis)':>14s} {'R2(sum|nuis)':>13s} "
      f"{'worst single motion r':>22s}")
for task, (a, b) in CASES:
    for f in sorted(glob.glob(f"{S}/network_fmri/discovery/lev1/sub-s03/task-{task}/quality_control/*designMatrix.csv"))[:3]:
        ses = re.search(r"ses-(\d+)", f).group(1)
        dm = pd.read_csv(f)
        if a not in dm or b not in dm: continue
        ncols = [c for c in dm.columns if c.startswith(NUIS)]
        N = dm[ncols].values.astype(float)
        diff = (dm[a] - dm[b]).values
        summ = (dm[a] + dm[b]).values
        mot = [c for c in ncols if c.startswith(("trans_", "rot_"))]
        worst = max(abs(np.corrcoef(diff, dm[c])[0, 1]) for c in mot) if mot else np.nan
        print(f"  {task:9s} ses-{ses:3s} {r2_on(diff, N):14.3f} {r2_on(summ, N):13.3f} "
              f"{worst:22.3f}")
print("\n  R2(diff|nuisance) near 0 means the confounds cannot be absorbing the contrast.")
