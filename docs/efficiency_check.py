"""Absolute design efficiency, and where the contrast's power sits in frequency.

VIF is relative; it cannot see an efficiency loss baked into X itself. For a contrast c,
SE per unit noise = sqrt(c' (X'X)^-1 c). Compare the DIFFERENCE contrast against the SUM
contrast within each task -- if flanker's difference is far worse relative to its own sum
than nBack's is, the design (not the data) is the limit.
"""
import os, glob, re
import numpy as np, pandas as pd

S = os.environ["SCRATCH"]
TR = 1.49
NUIS = ("cosine", "trans_", "rot_", "motion_outlier", "constant")
CASES = [("flanker", {"incongruent": 1, "congruent": -1}, {"incongruent": .5, "congruent": .5}),
         ("nBack", {"mismatch_2back": .5, "match_2back": .5,
                    "mismatch_1back": -.5, "match_1back": -.5},
                   {"mismatch_2back": .25, "match_2back": .25,
                    "mismatch_1back": .25, "match_1back": .25}),
         ("cuedTS", {"task_switch_cue_switch": 1, "task_stay_cue_switch": -1},
                    {"task_switch_cue_switch": .5, "task_stay_cue_switch": .5})]

def se(dm, weights):
    X = dm.values.astype(float)
    cols = list(dm.columns)
    c = np.zeros(len(cols))
    for k, w in weights.items():
        if k not in cols: return None
        c[cols.index(k)] = w
    XtX = X.T @ X
    return float(np.sqrt(c @ np.linalg.pinv(XtX) @ c))

print(f"  {'task':9s} {'ses':6s} {'SE(diff)':>9s} {'SE(sum)':>9s} {'ratio':>7s}  "
      f"{'trials':>7s} {'ISI s':>6s} {'alt%':>6s}")
for task, diff_w, sum_w in CASES:
    for f in sorted(glob.glob(f"{S}/network_fmri/discovery/lev1/sub-s03/task-{task}/quality_control/*designMatrix.csv"))[:3]:
        ses = re.search(r"ses-(\d+)", f).group(1)
        dm = pd.read_csv(f)
        sd, ss = se(dm, diff_w), se(dm, sum_w)
        if sd is None or ss is None:
            print(f"  {task:9s} ses-{ses}: missing a regressor"); continue
        # trial timing + how often the type alternates, from simplified events
        sef = glob.glob(f"{S}/network_fmri/discovery/lev1/sub-s03/task-{task}/simplified_events/*ses-{ses}*.csv")
        isi, alt, ntr = float('nan'), float('nan'), 0
        if sef:
            ev = pd.read_csv(sef[0])
            ev = ev[ev.regressor.isin(diff_w)].sort_values("onset")
            ntr = len(ev)
            if ntr > 2:
                isi = float(np.median(np.diff(ev.onset.values)))
                lab = ev.regressor.values
                alt = 100 * float(np.mean(lab[1:] != lab[:-1]))
        print(f"  {task:9s} ses-{ses:3s} {sd:9.4f} {ss:9.4f} {sd/ss:7.2f} "
              f"{ntr:7d} {isi:6.2f} {alt:6.1f}")
print("\n  ratio = SE(difference) / SE(sum). ~2 is the arithmetic floor for a +1/-1 vs")
print("  .5/.5 contrast on orthogonal regressors; much larger means the design itself")
print("  is fighting the difference. alt% = how often consecutive trials switch type.")
