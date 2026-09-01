"""Independent re-implementation: flanker incongruent-congruent, no network_glm code.

Deliberately shares nothing with network_glm or nilearn:
  * SPM canonical HRF written here from the double-gamma formula (scipy.stats.gamma),
    not nilearn's compute_regressor;
  * design assembled here; fit by numpy least squares, not nilearn's FirstLevelModel;
  * runs combined here by inverse-variance weighting.
Restricted to a-priori ROI voxels, which is all the comparison needs and makes it fast.
"""
import os, glob, re
import numpy as np, pandas as pd, nibabel as nib
from scipy.stats import gamma
from scipy.stats import ttest_1samp

S = os.environ["SCRATCH"]
B = f"{S}/network_fmri/discovery/bids"
FP = f"{B}/derivatives/fmriprep"
TR, STR_ = 1.49, 0.7010
SUBS = ("sub-s03", "sub-s10", "sub-s19", "sub-s29", "sub-s43")
ROIS = {"dACC / pre-SMA": (0, 20, 44), "L dlPFC": (-44, 20, 34), "R dlPFC": (44, 22, 32),
        "L ant insula": (-32, 20, 4), "L IPS": (-32, -56, 44), "R IPS": (34, -56, 44),
        "L motor (ctrl)": (-38, -22, 56)}

def spm_hrf(t):
    """SPM canonical: gamma(6,1) minus gamma(16,1)/6, peak-normalised."""
    h = gamma.pdf(t, 6.0) - gamma.pdf(t, 16.0) / 6.0
    return h / np.max(np.abs(h))

def convolve(onsets, durations, n_scans):
    dt = 0.05
    hi_n = int(np.ceil((n_scans * TR + 32) / dt))
    box = np.zeros(hi_n)
    for o, d in zip(onsets, durations):
        i0, i1 = int(round(o / dt)), int(round((o + max(d, dt)) / dt))
        box[i0:i1] += 1.0
    h = spm_hrf(np.arange(0, 32, dt))
    conv = np.convolve(box, h)[:hi_n] * dt
    ft = np.arange(n_scans) * TR + STR_
    return np.interp(ft, np.arange(hi_n) * dt, conv)

def sphere(shape, aff, xyz, r=8):
    ijk = np.linalg.inv(aff) @ np.array([*xyz, 1.0])
    g = np.ogrid[:shape[0], :shape[1], :shape[2]]
    vs = np.abs(np.diag(aff)[:3])
    return sum(((g[k] - ijk[k]) * vs[k]) ** 2 for k in range(3)) <= r * r

rows = {roi: {"diff": [], "base": []} for roi in ROIS}
for sub in SUBS:
    acc = {roi: {"diff": [[], []], "base": [[], []]} for roi in ROIS}   # [betas],[vars]
    for ev in sorted(glob.glob(f"{B}/{sub}/ses-*/func/*task-flanker_*_events.tsv")):
        ses = re.search(r"ses-(\d+)", ev).group(1)
        bo = glob.glob(f"{FP}/{sub}/ses-{ses}/func/*task-flanker_*space-MNI152NLin2009cAsym_res-2_desc-preproc_bold.nii.gz")
        cf = glob.glob(f"{FP}/{sub}/ses-{ses}/func/*task-flanker_*desc-confounds_timeseries.tsv")
        if not bo or not cf: continue
        img = nib.load(bo[0]); n = img.shape[3]
        d = pd.read_csv(ev, sep="\t")
        rt = pd.to_numeric(d["response_time"], errors="coerce")
        sel = (d["acc"] == 1) & (rt >= 0.2)
        cols = {}
        for cond in ("congruent", "incongruent"):
            m = sel & (d["trial_type"] == cond)
            cols[cond] = convolve(d.loc[m, "onset"].values,
                                 np.ones(int(m.sum())), n)
        C = pd.read_csv(cf[0], sep="\t")
        nuis = [c for c in C.columns
                if c in ("trans_x","trans_y","trans_z","rot_x","rot_y","rot_z")
                or c.startswith("cosine")]
        X = np.column_stack([cols["congruent"], cols["incongruent"],
                             C[nuis].fillna(0).values, np.ones(n)])
        data = np.asarray(img.dataobj, np.float32)
        for roi, xyz in ROIS.items():
            M = sphere(data.shape[:3], img.affine, xyz)
            Y = data[M].T                                     # time x voxels
            Y = Y - Y.mean(0)
            beta, *_ = np.linalg.lstsq(X, Y, rcond=None)
            resid = Y - X @ beta
            dof = n - X.shape[1]
            s2 = (resid ** 2).sum(0) / dof
            XtXi = np.linalg.pinv(X.T @ X)
            for key, c in (("diff", np.array([-1.0, 1.0])), ("base", np.array([.5, .5]))):
                cv = np.zeros(X.shape[1]); cv[:2] = c
                eff = cv @ beta
                var = s2 * (cv @ XtXi @ cv)
                acc[roi][key][0].append(eff); acc[roi][key][1].append(var)
    for roi in ROIS:
        for key in ("diff", "base"):
            E, V = acc[roi][key]
            if not E: continue
            E = np.array(E); V = np.array(V)
            w = 1.0 / V
            fe = (w * E).sum(0) / w.sum(0)                    # inverse-variance fixed effects
            fe_z = fe / np.sqrt(1.0 / w.sum(0))
            rows[roi][key].append(float(np.nanmean(fe_z)))

print("  Independent implementation (own HRF, own GLM, own run combination)")
print(f"  {'ROI':17s} {'task-baseline':>28s} {'incongruent-congruent':>30s}")
print(f"  {'':17s} {'mean z':>10s}{'n>0':>6s}{'p':>10s} {'mean z':>12s}{'n>0':>6s}{'p':>10s}")
for roi in ROIS:
    out = []
    for key in ("base", "diff"):
        v = rows[roi][key]
        if len(v) >= 3:
            t, p = ttest_1samp(v, 0)
            out.append(f"{np.mean(v):>10.3f}{sum(1 for x in v if x>0):6d}{p:10.3f}")
        else: out.append(f"{'--':>26s}")
    print(f"  {roi:17s} {out[0]} {out[1]}")
