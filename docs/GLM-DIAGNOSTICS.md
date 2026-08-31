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

**Decision: the first level stays unsmoothed.** The reliability gain above is real, but it
is a gain in voxelwise spatial agreement, not evidence of recovered effects — flanker
`incongruent-congruent` stays at zero (`r = −0.003` unsmoothed, `+0.003` at 8 mm), so
smoothing reveals no stable single-subject difference effect. Smoothing, if wanted, belongs
at the group level or in the surface/parcellated analyses, where it does not commit the
first level to a kernel. Revisit only with a prespecified justification, and note that
post-hoc smoothing of Z maps is not a substitute for rerunning the model.

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

The brain-map command writes one eight-task RTDur-versus-noRT page per paired subject in
both light and dark themes, using the output stem pattern
`<output>_bytask_<subject>_<theme>`. It also retains the all-subject flanker comparison.
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

## The response-time regressor is misspecified (defect)

Every task config declares RT as `amplitude: 1, duration: response_time` on the same trials
as the condition regressors. That makes it a near-duplicate boxcar rather than a parametric
modulator: measured `r(response_time, congruent + incongruent) = 0.950–0.964` on every
flanker run.

lev1's own QC already records the damage:

| contrast | VIF (flanker, sub-s03) |
|---|---:|
| `task-baseline` | **21.6** |
| `response_time` | **14.8** |
| `incongruent-congruent` | 1.14 |

`task-baseline` VIF by task: flanker 21.6, cuedTS 19.2, nBack 21.6, goNogo n/a,
stopSignal 14.3, spatialTS 13.7, directedForgetting 33.8, shapeMatching 59.5. All 19 task
YAMLs share the spec, so this is study-wide.

The consequence is a **sign inversion in the affected contrasts**. In a-priori ROIs, flanker
`task-baseline` is negative everywhere with RT in the model — left motor cortex −1.42, 0/5
subjects positive, p = 0.006 — for a button-press task that must be strongly positive. Drop
the RT regressor and left motor becomes **+1.90, 5/5 positive, p = 0.002**. This is also
what the earlier `noRT` result was really showing: removing RT raised `task-baseline`
suprathreshold fraction by +2.9 to +15.2 pp because RT had been suppressing it.

**Scope, precisely.** Difference contrasts are insulated: both conditions are equally
collinear with RT, so it cancels in the subtraction (VIF 1.14). Affected are
`task-baseline`, `response_time`, and any contrast that sums rather than differences
conditions. This therefore does **not** explain the dead flanker conflict contrast — see
below.

A correct RT regressor carries mean-centred RT as *amplitude* on the trial events, so it
models RT-related variance orthogonal to the mean task response.

## Flanker conflict: still unexplained after the RT finding

`incongruent-congruent` remains absent in the cognitive-control network in **both** arms.
a-priori dACC/pre-SMA is −0.628 (0/5 subjects positive) with RT and −0.205 (1/5) without;
the literature requires a positive dACC/pre-SMA response. So the RT defect above is not the
cause, and neither is any of the following, each tested:

- alignment — the per-run event→BOLD lag is *no worse* for flanker than for nBack, which
  works (r at lag 0: flanker +0.281, nBack +0.400; peak-lag sd 3.06 s vs 2.70 s);
- trimming — `NumberOfVolumesDiscardedByUser: 7` is uniform, and `first_onset` is 0.577 s
  in all 25 runs, so onsets are task-program-relative and consistently placed;
- design, labels, tSNR, z calibration — see the sections above.

Both tasks do show a systematic **+1.8 s (≈1.2 TR)** best-fitting lag. It is uniform across
tasks so it cannot be flanker-specific, but it is close enough to one volume to be worth
a separate look; it costs sensitivity everywhere.

## Regressor code review (before any re-run)

Reviewed `task_config/loader.py`, `lev1/processing/{design,events,glm,confounds,contrasts,
quality_control}.py`, and all 19 task YAMLs.

### The RT regressor: intended everywhere except the inhibition tasks

**Correction.** An earlier revision of this document called the RT regressor a study-wide
defect. That was wrong. `amplitude: 1, duration: response_time` is the RTDur specification
from Mumford, Bissett, Jones, Shim, Rios & Poldrack (2023), *Nature Human Behaviour*
8(2):349-360, https://doi.org/10.1038/s41562-023-01760-0 — modelling RT is deliberate,
because ignoring it confounds intensity differences with duration differences. Its high
correlation with the summed conditions is the mechanism by which time-on-task variance is
absorbed, not a bug.

Two consequences follow, and only the second is a defect.

**`task-baseline` is not interpretable under RTDur.** The RT regressor absorbs the mean
task response, so `task-baseline` VIF is 13.7-59.5 and its sign inverts (flanker left motor
is -1.42, 0/5 subjects positive, with RT; +1.90, 5/5, without). Differential contrasts are
unaffected (VIF ~1.1) and remain the interpretable ones. `network_qa`'s
`DEFAULT_VIF_IGNORE = ("task-baseline", "response_time")` is therefore correct — but for
this reason, not the "high by construction" wording recorded earlier.

**Defect: the inhibition tasks still carry the RT regressor.** In `goNogo` and `stopSignal`
the RT regressor's subset is `... and trial_type == 'go'` — the *identical event set* as the
`go` regressor, differing only in duration. Measured `r(go, response_time) = 0.965` and
`0.963`. Every main contrast in those tasks is defined against `go`
(`nogo_success-go`, `stop_success-go`, `stop_failure-go`), so unlike the balanced tasks the
damage lands on the contrasts of interest: `nogo_success-go` VIF 11.5. `stopSignalWFlanker`
and `stopSignalWDirectedForgetting` carry it too, over all correct test trials.

**Do not remove RT from the inhibition tasks without reading this.** `goNogo.yaml` and
`stopSignal.yaml` carry an explicit rationale for including it, citing the same Mumford
work and naming this exact contrast:

> This keeps `nogo_success-go` / `stop_success-go` from absorbing RT variance via the go
> regressor (the Mumford paradox) while avoiding RT modeling on trials whose RT is not a
> clean motor-readiness signal.

So the inclusion is deliberate and targets the contrast of interest. The trade-off is real
in both directions: keeping RT deconfounds those contrasts but inflates their standard
errors ~3.4x (VIF 11.5); removing it restores efficiency but reintroduces the confound the
comment was written to avoid. Empirically, `nogo_success-go` is the *most* run-to-run
reliable differential contrast in the battery (r 0.15-0.34), and it moved more than any
other when RT was dropped (-4.79 pp), which is consistent with RT carrying real variance
there. Decide on the methodology, not on the VIF alone. The two stop duals carry the same
regressor with no recorded rationale.

No HRF derivative is used, on the same authors' advice.

### Arms available

| arm | design | notes |
|---|---|---|
| `RTDur` | constant-duration conditions + pooled RT-duration regressor | study default; Mumford et al. 2023 |
| `noRT` | RT regressor dropped | RT variance left in the residual |
| `RTepoch` | no RT regressor; conditions carry `duration = RT` | Grinband variable epoch; **refuses the four inhibition tasks**, whose stop/nogo trials have no response and would otherwise contrast an RT-length epoch against a one-second one |

`lev1` now warns at run time about any contrast at or above VIF 5.

### Defect:  epoch and event regressors averaged together in `task-baseline`

`directedForgetting`, `directedForgettingWFlanker` and `stopSignalWDirectedForgetting`
average `memory_and_cue` (`duration: duration`, a multi-second epoch) with `duration: 1`
condition regressors. Convolved amplitude scales with duration, so the nominal equal
weights do not produce an equal average. directedForgetting has the second-highest
`task-baseline` VIF (33.8).

### Defect: the VIF alarm exists and is silenced

`quality_control.py` computes per-contrast VIFs, writes them to CSV, logs at
**`logger.debug`**, and carries an explicit "Does NOT fail-fast on high VIFs" comment. A VIF
of 59.5 therefore produced no output at default log level for the entire study. Worse, the
signal *did* reach `qa-lev1` via `cohort-outliers`, and `network_qa`'s
`DEFAULT_VIF_IGNORE = ("task-baseline", "response_time")` was then added to suppress it. The
justification recorded at the time — "high by construction" — was wrong: it is high by a
fixable misspecification. Revisit that ignore-list once Defect 1 is fixed.

### Verified NOT broken (do not re-check)

- **Confounds alignment**: `reset_index(drop=True)` after both trim and filter, so
  `pd.concat(axis=1)` cannot misalign task regressors against confounds.
- **NaN handling**: negative RT becomes NaN before `rt_too_fast` is computed, so omissions
  cannot double-count; NaN rows are dropped before `compute_regressor`.
- **Numeric durations**: the `constant_{N}_column` sentinel correctly preserves
  `duration: 10` (a prior bug collapsed all numeric durations to 1.0).
- **Trial coverage**: 0–2% of `test_trial` rows unmodelled across the eight base tasks —
  edge cases, not systematic leakage into the implicit baseline.
- **Nuisance definition**: go/nogo tasks restrict to `trial_type == "go"`, so successful
  withholds are not mis-flagged as omissions.
- **Amplitudes**: only `1` or binary indicators; no un-centred continuous modulator exists.
- **Slice-timing reference** is resolved from the sidecar, not hardcoded to TR/2.

### Choices worth questioning (not defects)

- `hrf_model="spm"` with **no temporal derivative**, against a measured systematic +1.8 s
  (≈1.2 TR) best-fitting lag. A temporal derivative would absorb it; today it costs
  sensitivity in every task.
- A stale comment in `design.py` claims fMRIPrep's `cosine00` is a constant column. It is
  not (235 unique values, sd 0.065). The fallback still adds an intercept correctly, but the
  `has_constant` heuristic would be fooled by any genuinely constant non-zero nuisance
  column.

### A Grinband comparison arm needs no new code

RTDur stays as the primary model. A Grinband-style comparison arm — condition regressors
carrying `duration: response_time` and no separate RT regressor — is expressible in YAML
today and needs no centring capability. Note that no mean-centring or orthogonalisation
exists in the regressor path (`_resolve_column_or_constant` returns raw column values), so a
*parametric* RT modulator would require new code; that is not the direction chosen.

## Remaining questions

- Choose the response-time arm for headline analyses.
- Treat `--combine-runs` with care: XCP-D writes both `task-*_desc-denoised` and
  `task-*_run-*_desc-denoised`. With one run per task per session the combined file
  duplicates the per-run one, ~7.5 GB per subject.
- Treat nBack accuracy cautiously: both checked loads were at ceiling (`1.000`).
