"""Independent check: are the trial LABELS right? Behaviour must show the known effect.

If events.tsv labels were shuffled or mismapped, the design would still be built correctly
(as verified) but would be semantically wrong -- and the behavioural signature would vanish.
Each task has a textbook effect; test it in the events themselves.
"""
import os, glob
from pathlib import Path
import numpy as np, pandas as pd

B = Path(os.environ["SCRATCH"]) / "network_fmri/discovery/bids"
CHECKS = [
    ("flanker", "trial_type", "incongruent", "congruent", "RT incongruent > congruent"),
    ("cuedTS", "trial_type", None, None, None),
    ("nBack", "trial_type", None, None, None),
    ("goNogo", "trial_type", None, None, None),
]

for task, col, hi, lo, expect in CHECKS:
    fs = sorted(glob.glob(str(B / "sub-s03" / "ses-*" / "func" / f"*task-{task}_*_events.tsv")))
    if not fs:
        print(f"\n### {task}: no events"); continue
    df = pd.concat([pd.read_csv(f, sep="\t") for f in fs], ignore_index=True)
    print(f"\n### {task}  ({len(fs)} runs, {len(df)} rows)")
    if col not in df.columns:
        print(f"  no '{col}' column; have {list(df.columns)[:10]}"); continue
    print(f"  {col} values: {df[col].value_counts().to_dict()}")
    if "response_time" not in df.columns:
        continue
    rt = pd.to_numeric(df["response_time"], errors="coerce")
    ok = df.get("key_press", pd.Series(index=df.index, dtype=object))
    cr = df.get("correct_response", pd.Series(index=df.index, dtype=object))
    correct = (pd.to_numeric(ok, errors="coerce") == pd.to_numeric(cr, errors="coerce"))
    valid = rt.notna() & (rt >= 0.2) & correct
    g = df.loc[valid].assign(rt=rt[valid]).groupby(col)["rt"]
    summ = g.agg(["mean", "count"]).sort_values("mean")
    for k, r in summ.iterrows():
        print(f"    {str(k):34s} mean RT {r['mean']:.3f}s   n={int(r['count'])}")
    if hi and lo and hi in summ.index and lo in summ.index:
        d = summ.loc[hi, "mean"] - summ.loc[lo, "mean"]
        verdict = "PRESENT" if d > 0.005 else ("ABSENT/REVERSED" if d < 0.005 else "flat")
        print(f"    -> {expect}: {d*1000:+.1f} ms   [{verdict}]")
