"""Three RT arms, a-priori cognitive-control ROIs, flanker incongruent-congruent.

Predictions: if the conflict effect is time-on-task, RTepoch (RT modelled AS condition
duration) should be strongest and RTDur (RT removed) weakest. If all three are null, it is
a power problem, not a modelling one. task-baseline is the positive control.
"""
import os, glob
from pathlib import Path
import numpy as np, nibabel as nib
from scipy import stats

S = os.environ["SCRATCH"]
ARMS = ("RTDur", "noRT", "RTepoch")
SUBS = ("sub-s03","sub-s10","sub-s19","sub-s29","sub-s43")
ROIS = {"dACC / pre-SMA":(0,20,44), "L dlPFC":(-44,20,34), "R dlPFC":(44,22,32),
        "L ant insula":(-32,20,4), "R ant insula":(34,22,2),
        "L IPS":(-32,-56,44), "R IPS":(34,-56,44), "L motor (ctrl)":(-38,-22,56)}
_cache = {}

def sphere(shape, aff, xyz, r=8):
    key = (shape, xyz)
    if key not in _cache:
        ijk = np.linalg.inv(aff) @ np.array([*xyz, 1.0])
        g = np.ogrid[:shape[0], :shape[1], :shape[2]]
        vs = np.abs(np.diag(aff)[:3])
        _cache[key] = sum(((g[k]-ijk[k])*vs[k])**2 for k in range(3)) <= r*r
    return _cache[key]

def vals(arm, con, roi_xyz):
    out = []
    for sub in SUBS:
        f = glob.glob(f"{S}/network_fmri/discovery/rtarms/{arm}/{sub}/task-flanker/"
                      f"fixed_effects/*contrast-{con}_rtmodel-{arm}_stat-fixed-effects-z_score.nii.gz")
        if not f: continue
        img = nib.load(f[0]); d = np.asarray(img.dataobj, np.float32)
        v = d[sphere(d.shape, img.affine, roi_xyz)]
        v = v[np.isfinite(v) & (v != 0)]
        if v.size: out.append(float(v.mean()))
    return out

for con in ("task-baseline", "incongruent-congruent"):
    tag = "POSITIVE CONTROL" if con == "task-baseline" else "THE TEST"
    print(f"\n########## flanker {con}   ({tag})")
    print(f"  {'ROI':17s}" + "".join(f"{a:>22s}" for a in ARMS))
    print(f"  {'':17s}" + "".join(f"{'mean z':>8s}{'n>0':>5s}{'p':>9s}" for _ in ARMS))
    for roi, xyz in ROIS.items():
        cells = []
        for arm in ARMS:
            v = vals(arm, con, xyz)
            if len(v) >= 3:
                t, p = stats.ttest_1samp(v, 0)
                cells.append(f"{np.mean(v):8.3f}{sum(1 for x in v if x>0):5d}{p:9.3f}")
            else:
                cells.append(f"{'--':>22s}")
        print(f"  {roi:17s}" + "".join(cells))
