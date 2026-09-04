"""Single-PDF summary of the flanker incongruent-congruent findings."""
import os
from pathlib import Path
import numpy as np, nibabel as nib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.gridspec import GridSpec
from nilearn.plotting import plot_glass_brain

S = os.environ["SCRATCH"]
V = Path(S) / "network_fmri/v1_check"
OUT = Path(S) / "network_fmri/flanker_findings.pdf"

INK, INK2, SURF, GRID = "#0b0b0b", "#52514e", "#fcfcfb", "#e6e5e2"
ARM_C = {"RTDur": "#2a78d6", "noRT": "#eb6834", "RTepoch": "#1baf7a"}   # slots 1-3
DIV = LinearSegmentedColormap.from_list("d", ["#2a78d6", "#f0efec", "#e34948"], N=256)
ARMS = list(ARM_C)

# measured values (see docs/GLM-DIAGNOSTICS.md)
BEST_P   = {"RTDur": 0.0514, "noRT": 0.1036, "RTepoch": 0.1624}
TB_PCT   = {"RTDur": 59.35, "noRT": 49.73, "RTepoch": 40.11}
DACC_T   = {"RTDur": -0.35, "noRT": 0.97, "RTepoch": 1.37}
MOTOR_T  = {"RTDur": -4.10, "noRT": 2.78, "RTepoch": 4.51}
RULED_OUT = [
    ("Design matrices", "rebuilt independently, r = 1.000; run/session pairing correct"),
    ("Trial labels", "behavioural conflict effect present, +37.4 ms, correctly signed"),
    ("Event/BOLD alignment", "per-run lag scatter no worse than nBack, which works"),
    ("Signal quality", "tSNR 64; multi-echo optimal combination beats best echo by 57%"),
    ("z calibration", "core (|z|<2) sd 0.91-1.01 against a null of 1.0"),
    ("Design efficiency", "SE(difference) 0.58-0.62 vs SE(sum) 1.27-1.45 - the better contrast"),
    ("Confound absorption", "nuisance explains 24-28% of it; nBack's 84-88% and works"),
    ("Spatial smoothing", "r = -0.003 unsmoothed, +0.003 at 8 mm - no recovery"),
    ("RT collinearity", "contrast VIF 1.14; arithmetically insulated"),
    ("Time-on-task", "RTepoch models RT as duration and should maximise it; shows nothing"),
    ("Implementation", "from-scratch GLM (own HRF, own solver) reproduces every sign"),
]

def style(ax):
    ax.set_facecolor(SURF)
    for s in ("top", "right", "left"): ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID); ax.spines["bottom"].set_linewidth(1)
    ax.tick_params(colors=INK2, labelsize=8.5, length=0, width=0)
    ax.xaxis.grid(True, color=GRID, lw=1, ls="-"); ax.set_axisbelow(True)

def tmap(arm, con):
    g = list((V / f"{arm}_lev2" / f"task-flanker_contrast-{con}").glob("uncorrected_tstat*.nii.gz"))
    return nib.load(g[0]) if g else None

def corrp_masked_t(arm, con, thr=0.95):
    d = V / f"{arm}_lev2" / f"task-flanker_contrast-{con}"
    cg = list(d.glob("*corrp*.nii.gz")); tg = list(d.glob("uncorrected_tstat*.nii.gz"))
    if not cg or not tg: return None
    c = nib.load(cg[0]); t = nib.load(tg[0])
    a = np.asarray(t.dataobj, np.float32).copy()
    a[np.asarray(c.dataobj, np.float32) <= thr] = 0
    return nib.Nifti1Image(a, t.affine)

with PdfPages(OUT) as pdf:
    # ---------------- page 1: the finding ----------------
    fig = plt.figure(figsize=(11, 8.5)); fig.patch.set_facecolor(SURF)
    gs = GridSpec(3, 2, figure=fig, height_ratios=[0.40, 1, 0.85],
                  left=0.075, right=0.965, top=0.845, bottom=0.07, hspace=0.60, wspace=0.22)

    fig.text(0.075, 0.955, "Flanker incongruent − congruent: no conflict effect at n = 46",
             fontsize=17, color=INK, weight="medium", va="top")
    fig.text(0.075, 0.918,
             "Group inference over 46 subjects (5 discovery + 41 validation, 208 runs). The positive control "
             "covers 40–59% of the brain at\ncorrected p < 0.05; the conflict contrast yields zero significant "
             "voxels in all three response-time models.",
             fontsize=9.5, color=INK2, va="top", linespacing=1.5)

    # stat row
    ax = fig.add_subplot(gs[0, :]); ax.axis("off")
    tiles = [("46", "subjects"), ("208", "flanker runs"),
             ("0", "voxels at corrected p < 0.05"), ("0.051", "best corrected p (RTDur)")]
    for i, (v, lab) in enumerate(tiles):
        x = 0.005 + i * 0.253
        ax.text(x, 0.62, v, fontsize=26, color=INK, weight="medium", transform=ax.transAxes)
        ax.text(x, 0.16, lab, fontsize=9, color=INK2, transform=ax.transAxes)

    # effect sizes by arm
    ax = fig.add_subplot(gs[1, 0]); style(ax)
    y = np.arange(len(ARMS))[::-1]
    ax.barh(y + 0.19, [MOTOR_T[a] for a in ARMS], height=0.34,
            color=[ARM_C[a] for a in ARMS], zorder=3)
    ax.barh(y - 0.19, [DACC_T[a] for a in ARMS], height=0.34,
            color=[ARM_C[a] for a in ARMS], alpha=0.45, zorder=3)
    ax.axvline(0, color=INK2, lw=1, zorder=4)
    for i, a in enumerate(ARMS):
        yy = y[i]
        ax.text(MOTOR_T[a] + (0.15 if MOTOR_T[a] > 0 else -0.15), yy + 0.19,
                f"{MOTOR_T[a]:+.2f}", va="center", fontsize=8.5, color=INK,
                ha="left" if MOTOR_T[a] > 0 else "right")
        ax.text(DACC_T[a] + (0.15 if DACC_T[a] > 0 else -0.15), yy - 0.19,
                f"{DACC_T[a]:+.2f}", va="center", fontsize=8.5, color=INK2,
                ha="left" if DACC_T[a] > 0 else "right")
    ax.set_yticks(y); ax.set_yticklabels(ARMS, fontsize=10, color=INK)
    ax.set_xlim(-5.6, 5.9); ax.set_xlabel("uncorrected t in an 8 mm sphere", fontsize=9, color=INK2)
    ax.set_title("solid: task-baseline, left motor (control)\nfaint: conflict, dACC / pre-SMA",
                 fontsize=9.5, color=INK2, loc="left", pad=8, linespacing=1.5)
    ax.annotate("sign inverted —\ncontrol is wrong", xy=(-4.10, y[0] + 0.19), xytext=(-5.3, y[0] - 0.72),
                fontsize=8.5, color="#c1121f", ha="left",
                arrowprops=dict(arrowstyle="-", color="#c1121f", lw=1))

    # numbers table
    ax = fig.add_subplot(gs[1, 1]); ax.axis("off")
    ax.text(0, 1.0, "Per arm, n = 46", fontsize=10.5, color=INK, weight="medium",
            transform=ax.transAxes, va="top")
    hdr = ["arm", "best\ncorr. p", "control\n% brain", "dACC\nconflict t", "control\nsign"]
    xs = [0.0, 0.30, 0.48, 0.68, 0.88]
    for x, h in zip(xs, hdr):
        ax.text(x, 0.845, h, fontsize=8.5, color=INK2, transform=ax.transAxes,
                va="top", linespacing=1.4)
    for i, a in enumerate(ARMS):
        yy = 0.63 - i * 0.15
        ax.plot([0, 1], [yy + 0.085, yy + 0.085], color=GRID, lw=1, transform=ax.transAxes)
        ax.scatter([0.0], [yy + 0.022], s=52, color=ARM_C[a], transform=ax.transAxes,
                   clip_on=False, zorder=3)
        ax.text(0.045, yy, a, fontsize=9.5, color=INK, transform=ax.transAxes)
        ax.text(xs[1], yy, f"{BEST_P[a]:.3f}", fontsize=9.5, color=INK, transform=ax.transAxes)
        ax.text(xs[2], yy, f"{TB_PCT[a]:.1f}%", fontsize=9.5, color=INK, transform=ax.transAxes)
        ax.text(xs[3], yy, f"{DACC_T[a]:+.2f}", fontsize=9.5, color=INK, transform=ax.transAxes)
        ok = MOTOR_T[a] > 0
        ax.text(xs[4], yy, "correct" if ok else "INVERTED", fontsize=9,
                color="#0ca30c" if ok else "#c1121f", transform=ax.transAxes)
    ax.text(0, -0.02,
            "RTepoch is the arm to use: correct control sign, largest\n"
            "control effect and largest dACC conflict t. RTDur's control\n"
            "is inverted, so its nominally best corrected p cannot be\n"
            "used to choose the model.",
            fontsize=8.5, color=INK2, transform=ax.transAxes, va="top", linespacing=1.7)

    # conclusion band
    ax = fig.add_subplot(gs[2, :]); ax.axis("off")
    ax.add_patch(plt.Rectangle((0, 0.06), 1, 0.88, transform=ax.transAxes,
                               facecolor="#f2f1ee", edgecolor=GRID, lw=1))
    ax.text(0.018, 0.80, "Conclusion", fontsize=11, color=INK, weight="medium",
            transform=ax.transAxes, va="top")
    ax.text(0.018, 0.63,
            "Neither a power problem nor a pipeline problem.  With a positive control significant across half the brain, a conflict contrast producing\n"
            "nothing anywhere is a property of the effect.  The effect is directionally correct but very small: in both arms whose control has the right\n"
            "sign, dACC conflict is positive (+1.37, +0.97) — roughly a third of the t needed for even uncorrected significance.\n\n"
            "Caveat  These are v1 (OAK) derivatives: fMRIPrep 25.2.4, the 7 non-steady-state volumes retained, no qa-reject anatomical decisions and no\n"
            "DataLad provenance.  Published numbers must come from v2; the direction and magnitude are not expected to change qualitatively.",
            fontsize=8.6, color=INK2, transform=ax.transAxes, va="top", linespacing=1.65)
    pdf.savefig(fig, facecolor=SURF); plt.close(fig)

    # ---------------- page 2: the maps ----------------
    fig = plt.figure(figsize=(11, 8.5)); fig.patch.set_facecolor(SURF)
    gs = GridSpec(3, 2, figure=fig, width_ratios=[0.26, 1],
                  left=0.01, right=0.985, top=0.845, bottom=0.03, hspace=0.16, wspace=0.01)
    fig.text(0.03, 0.965, "Group maps, RTepoch arm, n = 46",
             fontsize=16, color=INK, weight="medium", va="top")
    fig.text(0.03, 0.928,
             "Glass-brain maximum-intensity projection. Blue negative, red positive. The control shows the expected\n"
             "task-positive and default-mode pattern; the conflict contrast survives nothing.",
             fontsize=9.5, color=INK2, va="top", linespacing=1.6)

    rows = [("task-baseline\ncorrected p < 0.05",
             corrp_masked_t("RTepoch", "task-baseline"), 1e-6),
            ("incongruent - congruent\ncorrected p < 0.05",
             corrp_masked_t("RTepoch", "incongruent-congruent"), 1e-6),
            ("incongruent - congruent\nuncorrected |t| > 2",
             tmap("RTepoch", "incongruent-congruent"), 2.0)]
    for i2, (lab, img, thr) in enumerate(rows):
        la = fig.add_subplot(gs[i2, 0]); la.axis("off"); la.set_facecolor(SURF)
        la.text(0.97, 0.5, lab, transform=la.transAxes, ha="right", va="center",
                fontsize=10, color=INK, linespacing=1.6)
        ax = fig.add_subplot(gs[i2, 1]); ax.set_facecolor(SURF); ax.axis("off")
        if img is None:
            ax.text(0.5, 0.5, "map unavailable", ha="center", va="center",
                    color=INK2, transform=ax.transAxes); continue
        d = np.asarray(img.dataobj, np.float32)
        live = d[np.isfinite(d) & (d != 0)]
        if live.size == 0 or np.max(np.abs(live)) <= thr:
            ax.text(0.5, 0.5, "no voxels survive threshold", ha="center", va="center",
                    fontsize=12, color=INK2, transform=ax.transAxes, style="italic")
            continue
        vmax = float(np.nanpercentile(np.abs(live), 99.5))
        plot_glass_brain(img, threshold=thr, vmax=max(vmax, thr + .5), colorbar=False,
                         cmap=DIV, plot_abs=False, display_mode="lyrz", axes=ax,
                         black_bg=False, annotate=False)
    pdf.savefig(fig, facecolor=SURF); plt.close(fig)

    # ---------------- page 3: what was ruled out ----------------
    fig = plt.figure(figsize=(11, 8.5)); fig.patch.set_facecolor(SURF)
    fig.text(0.06, 0.955, "Ruled out, with the measurement that ruled it out",
             fontsize=16, color=INK, weight="medium", va="top")
    fig.text(0.06, 0.917,
             "Every mechanical explanation for the null was tested and eliminated before concluding the effect is absent.",
             fontsize=9.5, color=INK2, va="top")
    for i3, (what, how) in enumerate(RULED_OUT):
        yy = 0.845 - i3 * 0.058
        fig.text(0.06, yy, what, fontsize=10, color=INK, va="top")
        fig.text(0.30, yy, how, fontsize=10, color=INK2, va="top")
        fig.lines.append(plt.Line2D([0.06, 0.94], [yy - 0.016, yy - 0.016],
                                    transform=fig.transFigure, color=GRID, lw=0.8))
    fig.text(0.06, 0.175, "Why n = 5 could not settle it",
             fontsize=12, color=INK, weight="medium", va="top")
    fig.text(0.06, 0.142,
             "A one-sample sign-flip test on five subjects has only 2^5 = 32 possible permutations, so the smallest attainable\n"
             "corrected p is 1/32 = 0.031 and clearing 0.05 requires being the single most extreme permutation. In discovery the\n"
             "positive control - a large, obvious motor effect - reached only p = 0.0625. Discovery therefore could not produce a\n"
             "corrected group result at any effect size, which is why the question was moved to n = 46.",
             fontsize=9.5, color=INK2, va="top", linespacing=1.7)
    pdf.savefig(fig, facecolor=SURF); plt.close(fig)

print(f"  wrote {OUT}")
