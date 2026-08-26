# First-level GLM diagnostics

This guide records the discovery-cohort checks used to understand sparse first-level maps,
response-time modeling, and run-to-run reliability. It is evidence about the current
volumetric GLMs, not a replacement for a prespecified analysis plan.

## Inputs and definitions

The paired discovery trees are:

| Arm | Location | Meaning |
|---|---|---|
| RT included (`RTDur`) | `$SCRATCH/network_fmri/discovery/lev1` | task-specific response-time regressor included |
| RT excluded (`noRT`) | `$SCRATCH/network_fmri/discovery/lev1_noRT` | response-time regressor and dependent contrasts omitted |

The `network_glm --rt-model {RTDur,noRT}` option selects the arm, and the corresponding
`rtmodel-RTDur` or `rtmodel-noRT` filename entity distinguishes outputs. Comparisons
use subject-level fixed-effects Z maps and pair subjects across arms. In the sparsity
diagnostics, the mask is finite, nonzero voxels and “suprathreshold” means the descriptive
fraction with `|z| > 2.3`; it is not multiple-comparison-corrected inference.

## What the checks established

### Response-time modeling

Dropping response time barely changes the tested differential contrast but increases some
task-versus-baseline maps:

| Contrast | Mean change in voxels with `|z| > 2.3` | Subjects increasing |
|---|---:|---:|
| flanker `incongruent-congruent` | +0.22 percentage points | 4/5; direction not stable |
| flanker `task-baseline` | +2.85 percentage points | 4/5 |
| stopSignal `task-baseline` | +3.19 percentage points | 4/5 |
| nBack `task-baseline` | +15.24 percentage points | 5/5 |
| cuedTS `task-baseline` | +13.84 percentage points | 5/5 |

The RT regressor therefore absorbs task-versus-baseline variance much more than the tested
condition difference. This result does not choose the headline arm; that remains a
scientific decision. `sub-s19` reverses direction for flanker and stopSignal and merits
separate inspection.

### Inputs, timing, and design matrices

The sparse appearance was not explained by broken inputs or mismatched designs:

- task-versus-baseline maps reproduce across runs in every checked subject/task
  (pairwise `r = 0.09–0.29`);
- events fall within the trimmed timeline, and neither fMRIPrep nor the GLM removes dummy
  volumes a second time;
- all expected condition columns are present except genuinely empty regressors;
- the central `|z| < 2` distribution has standard deviation 0.91–1.01, close to a unit
  normal null;
- independently rebuilt SPM-HRF regressors match saved design columns at `r = 1.000`;
- each design matches its own session's events better than every other session
  (`r = 1.000` on the diagonal versus 0.08–0.52 off diagonal);
- flanker condition regressors are distinct (`r = −0.23` to `−0.42`), balanced at
  67–74 trials per run, and have contrast VIF 1.08–1.14; and
- behavioral labels retain expected effects: flanker conflict +37.4 ms, cued task
  switching 660 < 714 < 740 ms, and nBack load +87.9 ms.

For go/no-go, half of the `trial_type == "unknown"` rows are `test_fixation`; only 20 real
trials (1.6%) are unlabeled. An earlier apparent missing-column result came from reading
the first CSV column as an index; the tracked check intentionally uses no `index_col`.

### Smoothing and reliability

The current discovery first-level run used no smoothing. Post-hoc smoothing of per-run Z
maps improved pairwise spatial reliability where reproducible signal existed:

| Contrast | Unsmoothed mean `r` | 8 mm mean `r` |
|---|---:|---:|
| flanker `task-baseline` | 0.243 | 0.485 |
| nBack `twoBack-oneBack` | 0.131 | 0.257 |
| cuedTS `task_switch_cost` | 0.032 | 0.167 |

This motivates evaluating a prespecified smoothing kernel; post-hoc smoothing is not a
substitute for rerunning the model. Flanker `incongruent-congruent` remains near zero
(`r = −0.003` unsmoothed, `+0.003` at 8 mm), so smoothing does not reveal a stable
single-subject spatial difference effect.

Under a standard normal null, `|z| > 2.3` occurs in about 2.14% of voxels. Flanker's 3.3%
suprathreshold fraction is therefore mostly a global offset and heavier tails, not by
itself evidence of focal activation. Judge condition-difference reliability separately
from task-versus-baseline reliability.

## Reproducibility scripts

These scripts are narrow discovery audits, not general pipeline entry points:

| Script | Purpose |
|---|---|
| `compare_rt_arms.py` | Paired RT-arm suprathreshold fractions for selected tasks and contrasts |
| `compare_main.py` | RT-arm comparison for each task's primary contrast |
| `plot_rt_arms.py` | Light/dark summary plots and tidy `rt_arm_sparsity.csv` |
| `plot_rt_brains.py` | Subject-level glass-brain comparisons; never averages Z maps across subjects |
| `reliability_check.py` | Pairwise run-map correlations |
| `design_matrix_check.py` | HRF rebuild, session cross-match, trial counts, and regressor correlation |
| `label_check.py` | Independent behavioral-label checks |

Run scripts through the locked environment. Positional paths are required by the arm
comparison and plotting scripts:

```bash
uv run --frozen python docs/compare_main.py <RTDur-dir> <noRT-dir>
uv run --frozen python docs/compare_rt_arms.py \
    <RTDur-dir> <noRT-dir> flanker,stopSignal,nBack,cuedTS
uv run --frozen python docs/plot_rt_arms.py \
    <RTDur-dir> <noRT-dir> <output.png>
uv run --frozen python docs/plot_rt_brains.py \
    <RTDur-dir> <noRT-dir> <output.png>
```

The design, label, and reliability scripts currently encode discovery paths and example
subjects internally. Read their constants before running them.

## Upstream signal quality (checked; not the cause)

tSNR of the preprocessed BOLD is healthy and multi-echo optimal combination is working, so
modest task-baseline reliability is not an upstream data-quality problem:

| | median tSNR |
|---|---|
| optimally combined (flanker / nBack) | **64.0 / 53.0** |
| best single echo (echo-1) | 40.8 / 33.7 |
| echo-2 / echo-3 | 12.5 / 5.5 / 10.9 / 5.1 |

The combination beats the best single echo by ~57%, which is what optimal combination should
do. Measured with `tsnr_check.py`.

## Remaining questions

- Choose the response-time arm for headline analyses.
- Choose and justify `--smoothing-fwhm` before the final cohort run.
- Treat `--combine-runs` with care: XCP-D writes both `task-*_desc-denoised` and
  `task-*_run-*_desc-denoised`. With one run per task per session the combined file
  duplicates the per-run one, ~7.5 GB per subject.
- Treat nBack accuracy cautiously: both checked loads were at ceiling (`1.000`).
