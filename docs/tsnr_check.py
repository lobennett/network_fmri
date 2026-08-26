"""tSNR of the preprocessed BOLD, and whether multi-echo combination helped.

Low task-baseline reliability (r=0.24) points upstream of the GLM. tSNR is the direct
measure. Compare the optimally-combined preproc BOLD against a single echo.
"""
import os, glob
from pathlib import Path
import numpy as np, nibabel as nib

B = Path(os.environ["SCRATCH"]) / "network_fmri/discovery/bids"
FP = B / "derivatives/fmriprep"

def tsnr(p, mask=None):
    img = nib.load(p)
    d = np.asarray(img.dataobj, dtype=np.float32)
    mu = d.mean(-1); sd = d.std(-1)
    m = (mu > np.percentile(mu[mu > 0], 40)) & (sd > 0)
    return float(np.median(mu[m] / sd[m])), int(m.sum())

print(f"{'what':46s} {'median tSNR':>12s} {'n vox':>9s}")
for task in ("flanker", "nBack"):
    # optimally combined, MNI space
    fs = sorted(FP.glob(f"sub-s03/ses-*/func/*task-{task}_*space-MNI152NLin2009cAsym_res-2_desc-preproc_bold.nii.gz"))
    if fs:
        t, n = tsnr(fs[0])
        print(f"{task+' preproc (optimally combined, MNI)':46s} {t:12.1f} {n:9,d}")
    # individual echoes, as written by --me-output-echos
    for e in (1, 2, 3):
        es = sorted(FP.glob(f"sub-s03/ses-*/func/*task-{task}_*echo-{e}_desc-preproc_bold.nii.gz"))
        if es:
            t, n = tsnr(es[0])
            print(f"{f'  {task} echo-{e} only':46s} {t:12.1f} {n:9,d}")
    # raw, untouched
    rs = sorted((B/"sub-s03").glob(f"ses-*/func/*task-{task}_*run-1_echo-2_bold.nii.gz"))
    if rs:
        t, n = tsnr(rs[0])
        print(f"{f'  {task} RAW echo-2 (no preproc)':46s} {t:12.1f} {n:9,d}")
    print()
