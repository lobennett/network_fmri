"""Brain maps: main contrasts, RT included vs excluded, on real first-level maps.

Per subject, never an across-subject mean of z maps -- with n=5 and non-overlapping
activation the mean cancels to near-nothing and would misrepresent every map as empty.
Glass brain (maximum-intensity projection) so suprathreshold extent reads directly.
A dedicated label column keeps row labels aligned; nilearn resizes the axes it draws into.
"""
import sys
from pathlib import Path
import numpy as np, nibabel as nib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.gridspec import GridSpec
from nilearn.plotting import plot_glass_brain

RT_DIR, NO_DIR, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
THR = 2.3
MAIN = [("stopSignal", "stop_success-go"), ("spatialTS", "task_switch_cost"),
        ("shapeMatching", "main_vars"), ("cuedTS", "task_switch_cost"),
        ("directedForgetting", "neg-con"), ("flanker", "incongruent-congruent"),
        ("nBack", "twoBack-oneBack"), ("goNogo", "nogo_success-go")]

THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e",
                  neg="#2a78d6", mid="#f0efec", pos="#e34948"),
    "dark":  dict(surface="#1a1a19", ink="#ffffff", ink2="#c3c2b7",
                  neg="#3987e5", mid="#383835", pos="#e66767"),
}


def cmap_for(c):   # diverging: two poles, neutral midpoint. Never a rainbow.
    return LinearSegmentedColormap.from_list("div", [c["neg"], c["mid"], c["pos"]], N=256)


def find(root, task, con, arm):
    pat = f"*task-{task}_contrast-{con}_rtmodel-{arm}_stat-fixed-effects-z_score.nii.gz"
    return {p.name.split("_")[0]: p for p in sorted(root.rglob(pat))}


def pct(img):
    d = np.asarray(img.dataobj, dtype=np.float32)
    m = np.isfinite(d) & (d != 0)
    return 100.0 * float((np.abs(d[m]) > THR).sum()) / int(m.sum()) if m.sum() else np.nan


def figure(mode, panels, row_label, title, subtitle, suffix):
    """panels: list of (label, img_RT, img_noRT)."""
    c, cm = THEMES[mode], cmap_for(THEMES[mode])
    n = len(panels)
    fig = plt.figure(figsize=(14.6, 1.80 * n + 1.5))
    fig.patch.set_facecolor(c["surface"])
    head = 1.15 / (1.80 * n + 1.5)          # room for title + subtitle + column heads
    gs = GridSpec(n, 3, figure=fig, width_ratios=[0.30, 1, 1],
                  left=0.006, right=0.995, top=1 - head,
                  bottom=0.022, hspace=0.30, wspace=0.015)

    for i, (label, ia, ib) in enumerate(panels):
        lab = fig.add_subplot(gs[i, 0]); lab.axis("off")
        lab.set_facecolor(c["surface"])
        lab.text(0.985, 0.5, label, ha="right", va="center", fontsize=10.5,
                 color=c["ink"], linespacing=1.5, transform=lab.transAxes)
        # Shared colour limit per row: the comparison is arm-vs-arm.
        vmax = max(float(np.nanmax(np.abs(np.asarray(im.dataobj)))) for im in (ia, ib))
        vmax = max(vmax, THR + 0.5)
        for j, im in enumerate((ia, ib)):
            ax = fig.add_subplot(gs[i, j + 1])
            ax.set_facecolor(c["surface"])
            plot_glass_brain(im, threshold=THR, vmax=vmax, colorbar=False, cmap=cm,
                             plot_abs=False, display_mode="lyrz", axes=ax,
                             black_bg=(mode == "dark"), annotate=False)
            ax.text(0.5, -0.015, f"{pct(im):.1f}% of voxels |z| > {THR}",
                    transform=ax.transAxes, ha="center", va="top",
                    fontsize=8.5, color=c["ink2"])
            if i == 0:
                ax.set_title(("RT included", "RT excluded")[j], color=c["ink"],
                             fontsize=13, pad=16, weight="medium")

    H = 1.80 * n + 1.5
    fig.text(0.006, 1 - 0.30 / H, title, ha="left", va="top", fontsize=14.5,
             color=c["ink"], weight="medium")
    fig.text(0.006, 1 - 0.62 / H, subtitle, ha="left", va="top",
             fontsize=9.5, color=c["ink2"])
    p = OUT.with_name(f"{OUT.stem}_{suffix}_{mode}{OUT.suffix}")
    fig.savefig(p, dpi=170, facecolor=c["surface"])
    plt.close(fig)
    print(f"  wrote {p}")


SUBJ = "sub-s03"
tasks_panels = []
for task, con in MAIN:
    a, b = find(RT_DIR, task, con, "RTDur"), find(NO_DIR, task, con, "noRT")
    if SUBJ in a and SUBJ in b:
        tasks_panels.append((f"{task}\n{con}", nib.load(a[SUBJ]), nib.load(b[SUBJ])))

flanker_panels = []
fa = find(RT_DIR, "flanker", "incongruent-congruent", "RTDur")
fb = find(NO_DIR, "flanker", "incongruent-congruent", "noRT")
for s in sorted(set(fa) & set(fb)):
    flanker_panels.append((s, nib.load(fa[s]), nib.load(fb[s])))

for m in ("light", "dark"):
    figure(m, tasks_panels, "task",
           "Main contrast per task, with and without the response-time regressor",
           f"First-level fixed-effects z, {SUBJ} (discovery). Glass-brain projection at "
           f"|z| > {THR}; colour limit shared within a row. Blue negative, red positive.",
           "bytask")
figure("light", flanker_panels, "subject",
       "flanker incongruent-congruent, every discovery subject",
       f"First-level fixed-effects z. Glass-brain projection at |z| > {THR}; colour limit "
       f"shared within a row. Blue negative, red positive.",
       "flanker")
