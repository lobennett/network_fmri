"""The actual sanity check: does incongruent>congruent appear in the cognitive-control
network at a-priori coordinates?

Whole-brain reliability is dominated by ~270k noise voxels. A targeted ROI test is far more
powerful. Spheres at standard conflict / multiple-demand coordinates, both RT arms, with
task-baseline as a positive control (these regions should respond to task vs baseline).
"""
import os, glob
from pathlib import Path
import numpy as np, nibabel as nib
from scipy import stats

S = os.environ["SCRATCH"]
ARMS = {"RTDur": Path(S)/"network_fmri/discovery/lev1",
        "noRT":  Path(S)/"network_fmri/discovery/lev1_noRT"}
SUBS = ("sub-s03","sub-s10","sub-s19","sub-s29","sub-s43")
R_MM = 8

# Standard conflict / multiple-demand coordinates (MNI).
ROIS = {
    "dACC / pre-SMA":   (  0,  20,  44),
    "L dlPFC":          (-44,  20,  34),
    "R dlPFC":          ( 44,  22,  32),
    "L ant insula":     (-32,  20,   4),
    "R ant insula":     ( 34,  22,   2),
    "L IPS":            (-32, -56,  44),
    "R IPS":            ( 34, -56,  44),
    "L motor (ctrl)":   (-38, -22,  56),
}

def sphere(shape, affine, xyz, r):
    inv = np.linalg.inv(affine)
    ijk = inv @ np.array([*xyz, 1.0])
    g = np.ogrid[:shape[0], :shape[1], :shape[2]]
    vs = np.abs(np.diag(affine)[:3])
    d2 = sum(((g[k] - ijk[k]) * vs[k])**2 for k in range(3))
    return d2 <= r*r

def get(arm_dir, sub, task, con, rtm):
    f = sorted((arm_dir/sub/f"task-{task}"/"fixed_effects").glob(
        f"*contrast-{con}_rtmodel-{rtm}_stat-fixed-effects-z_score.nii.gz"))
    return nib.load(f[0]) if f else None

for con, label in (("task-baseline", "POSITIVE CONTROL"), ("incongruent-congruent", "the test")):
    print(f"\n########## flanker {con}   ({label})")
    print(f"  {'ROI':17s} " + "".join(f"{a:>26s}" for a in ARMS))
    print(f"  {'':17s} " + "".join(f"{'mean z':>9s}{'n>0':>6s}{'t(4)':>7s}{'p':>6s}" for _ in ARMS))
    masks = None
    for roi, xyz in ROIS.items():
        cells = []
        for arm, root in ARMS.items():
            rtm = "RTDur" if arm == "RTDur" else "noRT"
            vals = []
            for sub in SUBS:
                img = get(root, sub, "flanker", con, rtm)
                if img is None: continue
                if masks is None or masks[0] != img.shape[:3]:
                    masks = (img.shape[:3], {})
                if roi not in masks[1]:
                    masks[1][roi] = sphere(img.shape[:3], img.affine, xyz, R_MM)
                d = np.asarray(img.dataobj, np.float32)
                v = d[masks[1][roi]]
                v = v[np.isfinite(v) & (v != 0)]
                if v.size: vals.append(float(v.mean()))
            if len(vals) >= 3:
                t, p = stats.ttest_1samp(vals, 0)
                cells.append(f"{np.mean(vals):9.3f}{sum(1 for x in vals if x>0):6d}"
                             f"{t:7.2f}{p:6.3f}")
            else:
                cells.append(f"{'--':>26s}")
        print(f"  {roi:17s} " + "".join(cells))
