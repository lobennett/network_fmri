"""The powered test: flanker conflict at n=46, three RT arms, whole brain + a-priori ROIs."""
import os
from pathlib import Path
import numpy as np, nibabel as nib

S = os.environ["SCRATCH"]
ARMS = ("RTDur", "noRT", "RTepoch")
ROIS = {"dACC / pre-SMA": (0, 20, 44), "L dlPFC": (-44, 20, 34), "R dlPFC": (44, 22, 32),
        "L ant insula": (-32, 20, 4), "R ant insula": (34, 22, 2),
        "L IPS": (-32, -56, 44), "R IPS": (34, -56, 44), "L motor (ctrl)": (-38, -22, 56)}
_c = {}
def sphere(shape, aff, xyz, r=8):
    k = (shape, xyz)
    if k not in _c:
        ijk = np.linalg.inv(aff) @ np.array([*xyz, 1.0])
        g = np.ogrid[:shape[0], :shape[1], :shape[2]]
        vs = np.abs(np.diag(aff)[:3])
        _c[k] = sum(((g[i] - ijk[i]) * vs[i]) ** 2 for i in range(3)) <= r * r
    return _c[k]

for con in ("task-baseline", "incongruent-congruent"):
    tag = "POSITIVE CONTROL" if con == "task-baseline" else "THE TEST"
    print(f"\n########## flanker {con}   ({tag})   n = 46")
    print(f"  {'arm':9s} {'max(1-p)':>9s} {'p<0.05':>9s} {'p<0.01':>9s} {'% brain p<.05':>14s}")
    maps = {}
    for arm in ARMS:
        d = Path(S) / f"network_fmri/v1_check/{arm}_lev2/task-flanker_contrast-{con}"
        f = sorted(d.glob("*corrp*.nii.gz"))
        if not f:
            print(f"  {arm:9s} {'-- no map --':>9s}"); continue
        img = nib.load(f[0]); a = np.asarray(img.dataobj, np.float32)
        maps[arm] = (a, img)
        inm = a > 0
        n5 = int((a > 0.95).sum()); n1 = int((a > 0.99).sum())
        print(f"  {arm:9s} {a.max():9.4f} {n5:9d} {n1:9d} "
              f"{100*n5/max(inm.sum(),1):13.2f}%")
    if not maps: continue
    print(f"\n  a-priori ROIs, max(1-p) inside an 8 mm sphere:")
    print(f"  {'ROI':17s}" + "".join(f"{a:>12s}" for a in maps))
    for roi, xyz in ROIS.items():
        cells = []
        for arm, (a, img) in maps.items():
            m = sphere(a.shape, img.affine, xyz) & (a > 0)
            cells.append(f"{a[m].max():12.3f}" if m.any() else f"{'--':>12s}")
        print(f"  {roi:17s}" + "".join(cells))
