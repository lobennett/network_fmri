"""Whole-brain group result for the RTepoch arm: does anything survive anywhere?

The ROI test could only see my a-priori coordinates. This asks the unrestricted question,
with task-baseline as the positive control. n=5 gives 2^5 = 32 sign-flips, so the smallest
attainable corrected p is 1/32 = 0.031.
"""
import os, glob
from pathlib import Path
import numpy as np, nibabel as nib

S = os.environ["SCRATCH"]
L2 = Path(S) / "network_fmri/discovery/rtarms/RTepoch_lev2"
for con in ("task-baseline", "incongruent-congruent"):
    d = L2 / f"task-flanker_contrast-{con}"
    c = sorted(d.glob("*corrp*.nii.gz"))
    u = sorted(d.glob("uncorrected_tstat*.nii.gz"))
    tag = "POSITIVE CONTROL" if con == "task-baseline" else "THE TEST"
    print(f"\n### flanker {con}   ({tag})")
    if not c:
        print(f"   no corrected map in {d}"); continue
    a = np.asarray(nib.load(c[0]).dataobj, np.float32); m = a > 0
    print(f"   {c[0].name}")
    for thr, lab in ((0.969, "p<0.031 (floor)"), (0.95, "p<0.05"), (0.90, "p<0.10")):
        n = int((a > thr).sum())
        print(f"     {lab:16s} {n:8d} voxels  ({100*n/max(m.sum(),1):.3f}%)")
    print(f"     max(1-p) = {a.max():.4f}")
    if u:
        t = np.asarray(nib.load(u[0]).dataobj, np.float32)
        tm = np.isfinite(t) & (t != 0)
        print(f"     uncorrected t: max {t[tm].max():+.2f}  min {t[tm].min():+.2f}  "
              f"|t|>3.5 in {100*np.mean(np.abs(t[tm])>3.5):.2f}% of voxels")
