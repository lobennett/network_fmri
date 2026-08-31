"""Per-run event->BOLD lag, done properly.

- ROI = that subject's own task-responsive voxels (top 2% of task-baseline fixed-effects z).
- Both timeseries and shifted regressor are residualised against the run's actual nuisance
  columns (cosines + 24 motion + spikes + constant), i.e. the partial correlation the GLM
  computes -- not a raw correlation on a detrended global mean.
- nBack included as a POSITIVE CONTROL: it has reproducible signal, so its lag must peak
  near 0. If nBack is tight and flanker scatters, the scatter is real.
"""
import os, glob, re
from pathlib import Path
import numpy as np, pandas as pd, nibabel as nib

S = os.environ["SCRATCH"]
B = Path(S) / "network_fmri/discovery/bids"
L1 = Path(S) / "network_fmri/discovery/lev1"
TR = 1.49
LAGS = np.arange(-6, 6.01, 0.745)
NUIS = ("cosine", "trans_", "rot_", "motion_outlier", "constant")
SUBS = ("sub-s03", "sub-s10", "sub-s19", "sub-s29", "sub-s43")

def resid(y, N):
    """Residualise y against nuisance matrix N."""
    beta, *_ = np.linalg.lstsq(N, y, rcond=None)
    return y - N @ beta

def roi_mask(sub, task):
    """That subject's task-responsive voxels: top 2% of task-baseline fixed-effects z."""
    f = sorted((L1/sub/f"task-{task}"/"fixed_effects").glob(
        "*contrast-task-baseline_*stat-fixed-effects-z_score.nii.gz"))
    if not f: return None
    z = np.asarray(nib.load(f[0]).dataobj, np.float32)
    z = np.nan_to_num(z)
    thr = np.percentile(z[z != 0], 98)
    return z > thr

for task, conds in (("nBack", None), ("flanker", ("congruent", "incongruent"))):
    print(f"\n############ {task} {'(POSITIVE CONTROL)' if task=='nBack' else ''}")
    print(f"  {'sub':9s} {'ses':7s} {'peak lag':>9s} {'r@peak':>7s} {'r@0':>7s} {'nROI':>7s}")
    rows = []
    for sub in SUBS:
        M = roi_mask(sub, task)
        if M is None: continue
        qc = L1/sub/f"task-{task}"/"quality_control"
        for dmf in sorted(qc.glob("*desc-designMatrix.csv")):
            ses = re.search(r"ses-(\d+)", dmf.name).group(1)
            dm = pd.read_csv(dmf)
            cols = list(dm.columns)
            tcols = [c for c in cols if not c.startswith(NUIS)]
            if conds and not set(conds) <= set(cols): continue
            base = dm[list(conds)].mean(axis=1).values if conds else \
                   dm[[c for c in tcols if 'back' in c.lower()]].mean(axis=1).values
            N = dm[[c for c in cols if c.startswith(NUIS)]].values
            bo = glob.glob(f"{B}/derivatives/fmriprep/{sub}/ses-{ses}/func/"
                           f"*task-{task}_*space-MNI152NLin2009cAsym_res-2_desc-preproc_bold.nii.gz")
            if not bo: continue
            d = np.asarray(nib.load(bo[0]).dataobj, np.float32)
            if d.shape[3] != len(dm): continue
            ts = d[M].mean(0)
            ts_r = resid(ts, N)
            t = np.arange(len(base)) * TR
            rs = []
            for lag in LAGS:
                sh = np.interp(t, t + lag, base, left=0, right=0)
                if sh.std() == 0: rs.append(0.0); continue
                rs.append(np.corrcoef(ts_r, resid(sh, N))[0,1])
            rs = np.array(rs)
            i = int(np.nanargmax(rs))            # signed: task response is positive
            r0 = rs[int(np.argmin(np.abs(LAGS)))]
            rows.append((sub, ses, LAGS[i], rs[i], r0))
            print(f"  {sub:9s} ses-{ses:4s} {LAGS[i]:+9.2f} {rs[i]:+7.3f} {r0:+7.3f} {int(M.sum()):7d}")
    if rows:
        o = pd.DataFrame(rows, columns=["sub","ses","lag","rp","r0"])
        print(f"  --> peak lag mean {o.lag.mean():+.2f}s  sd {o.lag.std():.2f}s | "
              f"r@peak {o.rp.mean():+.3f}  r@0 {o.r0.mean():+.3f}")
