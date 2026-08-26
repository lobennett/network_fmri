"""Triple-check the design matrices.

1. Is each regressor really the HRF convolution of THIS run's events? (rebuild and correlate)
2. Is the design paired with the RIGHT run? (cross-match every design against every
   session's events -- the diagonal must win, or the pairing is scrambled)
3. Are the two contrasted conditions actually distinct? (corr, trial counts)
"""
import os, re, glob
from pathlib import Path
import numpy as np, pandas as pd
from nilearn.glm.first_level import compute_regressor

R = Path(os.environ["SCRATCH"]) / "network_fmri/discovery/lev1"
SUB, TASK = "sub-s03", "flanker"
TR, STR_ = 1.49, 0.7010
A, B = "congruent", "incongruent"

qc = R / SUB / f"task-{TASK}" / "quality_control"
se = R / SUB / f"task-{TASK}" / "simplified_events"
dms = sorted(qc.glob("*desc-designMatrix.csv"))
ses_of = lambda p: re.search(r"ses-(\d+)", p.name).group(1)

designs, events = {}, {}
for p in dms:
    designs[ses_of(p)] = pd.read_csv(p)                 # NO index_col: col 0 is a regressor
for p in sorted(se.glob("*desc-simplifiedEvents.csv")):
    events[ses_of(p)] = pd.read_csv(p)

print(f"=== {SUB} task-{TASK}: {len(designs)} designs, {len(events)} event files ===\n")

def rebuild(ev, name, n_scans):
    """HRF-convolve this regressor's events onto the run's frame times."""
    e = ev[ev["regressor"] == name]
    if e.empty:
        return None
    cond = np.vstack([e["onset"].values, e["duration"].values, e["amplitude"].values])
    ft = np.arange(n_scans) * TR + STR_
    sig, _ = compute_regressor(cond, "spm", ft, con_id=name)
    return sig[:, 0]

print("1+2. rebuilt-vs-saved correlation. Diagonal = own session; best should be diagonal.")
print(f"     {'design':>8s} | " + " ".join(f"ev {s:>3s}" for s in sorted(events)))
scrambled = []
for ds in sorted(designs):
    dm = designs[ds]
    if B not in dm.columns:
        continue
    n = len(dm)
    row, vals = [], {}
    for es in sorted(events):
        r = rebuild(events[es], B, n)
        if r is None or r.std() == 0:
            row.append("   n/a"); continue
        c = float(np.corrcoef(dm[B].values, r)[0, 1])
        vals[es] = c
        row.append(f"{c:+6.3f}")
    best = max(vals, key=vals.get) if vals else None
    flag = "" if best == ds else f"   <-- BEST IS ses-{best}, NOT ses-{ds}"
    if best != ds:
        scrambled.append((ds, best))
    print(f"     ses-{ds:>4s} | " + " ".join(row) + flag)

print(f"\n3. are '{A}' and '{B}' distinct within each run?")
print(f"     {'ses':>5s} {'corr(A,B)':>10s} {'n_trials A':>11s} {'n_trials B':>11s}"
      f" {'sd A':>7s} {'sd B':>7s}")
for ds in sorted(designs):
    dm = designs[ds]
    if A not in dm.columns or B not in dm.columns:
        print(f"     {ds:>5s}  MISSING COLUMN: {[c for c in (A,B) if c not in dm.columns]}")
        continue
    ev = events.get(ds)
    na = int((ev["regressor"] == A).sum()) if ev is not None else -1
    nb = int((ev["regressor"] == B).sum()) if ev is not None else -1
    c = float(np.corrcoef(dm[A].values, dm[B].values)[0, 1])
    print(f"     {ds:>5s} {c:+10.3f} {na:11d} {nb:11d} {dm[A].std():7.4f} {dm[B].std():7.4f}")

print("\n   (corr near +1.0 would make the difference meaningless; expect mild negative)")
if scrambled:
    print(f"\n!!! PAIRING SCRAMBLED for {scrambled}")
else:
    print("\n   pairing OK: every design matches its own session's events best")
