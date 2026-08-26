"""RT included vs excluded, per task's main contrast.

Dumbbell (before->after per item) for the means, plus per-subject deltas so the
consistency behind each mean is visible -- a +0.2pp mean from 4/5 subjects and one from
2/5 mean different things.
"""
import sys, csv
from pathlib import Path
import numpy as np, nibabel as nib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

RT_DIR, NO_DIR, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
THR = 2.3
MAIN = [("flanker", "incongruent-congruent"), ("stopSignal", "stop_success-go"),
        ("nBack", "twoBack-oneBack"), ("cuedTS", "task_switch_cost"),
        ("spatialTS", "task_switch_cost"), ("goNogo", "nogo_success-go"),
        ("directedForgetting", "neg-con"), ("shapeMatching", "main_vars")]

THEMES = {  # values straight from the skill's reference palette
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", grid="#e6e5e2",
                  rt="#2a78d6", nort="#eb6834"),
    "dark":  dict(surface="#1a1a19", ink="#ffffff", ink2="#c3c2b7", grid="#333331",
                  rt="#3987e5", nort="#d95926"),
}


def frac(p):
    d = np.asarray(nib.load(p).dataobj, dtype=np.float32)
    m = np.isfinite(d) & (d != 0)
    return float((np.abs(d[m]) > THR).sum()) / int(m.sum()) if m.sum() else np.nan


def series(root, task, con, arm):
    pat = f"*task-{task}_contrast-{con}_rtmodel-{arm}_stat-fixed-effects-z_score.nii.gz"
    return {p.name.split("_")[0]: p for p in sorted(root.rglob(pat))}


# ---- gather -----------------------------------------------------------------
rows = []
for task, con in MAIN:
    a, b = series(RT_DIR, task, con, "RTDur"), series(NO_DIR, task, con, "noRT")
    subs = sorted(set(a) & set(b))
    if not subs:
        continue
    fa = np.array([frac(a[s]) for s in subs]) * 100
    fb = np.array([frac(b[s]) for s in subs]) * 100
    rows.append(dict(task=task, contrast=con, subs=subs, rt=fa, nort=fb,
                     d=fb - fa, mrt=fa.mean(), mnort=fb.mean()))
rows.sort(key=lambda r: r["d"].mean())          # most negative at the bottom

with (OUT.parent / "rt_arm_sparsity.csv").open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["task", "contrast", "subject", "pct_suprathreshold_RT_included",
                "pct_suprathreshold_RT_excluded", "delta_pp"])
    for r in rows:
        for i, s in enumerate(r["subs"]):
            w.writerow([r["task"], r["contrast"], s, f"{r['rt'][i]:.3f}",
                        f"{r['nort'][i]:.3f}", f"{r['d'][i]:+.3f}"])


def draw(mode):
    c = THEMES[mode]
    fig, (axL, axR) = plt.subplots(
        1, 2, figsize=(13.5, 5.6), sharey=True,
        gridspec_kw=dict(width_ratios=[1.55, 1], wspace=0.06))
    fig.patch.set_facecolor(c["surface"])
    y = np.arange(len(rows))

    for ax in (axL, axR):
        ax.set_facecolor(c["surface"])
        for sp in ("top", "right", "left"):
            ax.spines[sp].set_visible(False)
        ax.spines["bottom"].set_color(c["grid"])
        ax.spines["bottom"].set_linewidth(1)
        ax.tick_params(colors=c["ink2"], labelsize=9, length=0, width=0)
        ax.yaxis.set_tick_params(length=0, width=0)
        ax.xaxis.grid(True, color=c["grid"], lw=1, ls="-")
        ax.set_axisbelow(True)

    # ---- left: dumbbell of the means ---------------------------------------
    for i, r in enumerate(rows):
        axL.plot([r["mrt"], r["mnort"]], [i, i], color=c["grid"], lw=2,
                 solid_capstyle="round", zorder=1)
    axL.scatter([r["mrt"] for r in rows], y, s=95, color=c["rt"], zorder=3,
                edgecolors=c["surface"], linewidths=2, label="RT included (RTDur)")
    axL.scatter([r["mnort"] for r in rows], y, s=95, color=c["nort"], zorder=3,
                edgecolors=c["surface"], linewidths=2, label="RT excluded (noRT)")
    axL.set_xlabel(f"% of in-mask voxels with |z| > {THR}   (mean of 5 subjects)",
                   color=c["ink2"], fontsize=9.5)
    axL.set_xlim(0, max(r["mnort"] for r in rows) * 1.18)

    labels = [f"{r['task']}\n{r['contrast']}" for r in rows]
    axL.set_yticks(y); axL.set_yticklabels(labels, fontsize=9.5, color=c["ink"])
    for t, r in zip(axL.get_yticklabels(), rows):
        t.set_linespacing(1.45)
    axL.set_ylim(-0.7, len(rows) - 0.3)

    leg = axL.legend(loc="lower left", bbox_to_anchor=(0.0, 1.005), ncol=2,
                     frameon=False, fontsize=9.5, labelcolor=c["ink2"],
                     handletextpad=0.4, borderpad=0.0, columnspacing=1.6)
    for h in leg.legend_handles:
        h.set_sizes([70])

    # ---- right: per-subject deltas -----------------------------------------
    axR.axvline(0, color=c["ink2"], lw=1, zorder=2)
    for i, r in enumerate(rows):
        col = c["nort"] if r["d"].mean() > 0 else c["rt"]
        jit = np.linspace(-0.17, 0.17, len(r["d"]))
        axR.scatter(r["d"], i + jit, s=34, color=col, alpha=0.75,
                    edgecolors=c["surface"], linewidths=1.2, zorder=3)
        axR.plot([r["d"].mean()] * 2, [i - 0.27, i + 0.27], color=col, lw=2.4,
                 solid_capstyle="butt", zorder=4)
        n_up = int((r["d"] > 0).sum())
        axR.annotate(f"{r['d'].mean():+.2f} pp   {n_up}/{len(r['d'])}",
                     xy=(1.0, i), xycoords=("axes fraction", "data"),
                     xytext=(6, 0), textcoords="offset points",
                     va="center", ha="left", fontsize=9, color=c["ink2"])
    axR.set_xlabel("change when RT is removed  (percentage points)",
                   color=c["ink2"], fontsize=9.5)
    pad = max(abs(np.concatenate([r["d"] for r in rows]))) * 1.15
    axR.set_xlim(-pad, pad)

    fig.suptitle("Removing the response-time regressor barely changes most main contrasts",
                 x=0.008, y=0.978, ha="left", fontsize=13.5, color=c["ink"], weight="medium")
    fig.text(0.008, 0.925,
             "First-level fixed-effects z maps, discovery cohort (n=5). Right panel: one dot "
             "per subject, tick = mean, count = subjects moving up.",
             ha="left", fontsize=9.5, color=c["ink2"])
    fig.subplots_adjust(left=0.155, right=0.875, top=0.815, bottom=0.115)
    p = OUT.with_name(f"{OUT.stem}_{mode}{OUT.suffix}")
    fig.savefig(p, dpi=200, facecolor=c["surface"])
    plt.close(fig)
    print(f"  wrote {p}")


for m in ("light", "dark"):
    draw(m)
print(f"  wrote {OUT.parent / 'rt_arm_sparsity.csv'}")
