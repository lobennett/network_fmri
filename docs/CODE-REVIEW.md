# Pipeline design review — 2026-09-13

The main opportunity is to make the existing boundaries trustworthy and easier to use.
Keep `network_fmri` responsible for planning, Slurm submission, and provenance; keep
events, exclusions, and GLMs in their owning packages. The pinned environment, study
TOML, standalone commands, and lifecycle manifests already provide a useful design.

This review traced the path from Flywheel export and BIDS preparation through campaign
handoffs, first-level fitting, exclusion-driven fixed-effects refresh, and second-level
submission. It examined `network_glm` residual execution and arithmetic in detail.
`network_events` was inspected at its timing/trim handoff, not independently audited
in full. The initial revisions were `network_fmri:6b5a179` and `network_glm:160fba7`;
Sherlock's production checkout and installed GLM pin matched them.

## Findings addressed

| Priority | Finding | Implemented behavior |
|---|---|---|
| High | Skipping surface QC plots also skipped residual output. | Only plotting is conditional. Both hemispheres still return contrast results and write requested residuals. |
| High | Residual, contrast, and fixed-effects failures could be hidden behind success. | Requested-output failures propagate; failed run identities are excluded from fixed effects even when older maps exist. |
| High | Refits and exclusion refreshes could leave old contrast or fixed-effects maps discoverable downstream. | Preserve superseded maps under non-discoverable suffixes before replacement, including below-minimum and zero-eligible-run transitions. |
| High | GLM reuse depended on residual filenames, ignoring changed scientific inputs/settings. | Per-run completion records require matching code, dependency versions, settings, and input/output contents. Legacy files are refitted once. |
| High | Volume residuals used a pinned Nilearn accessor that mixes original Y and a whitened prediction. | Reconstruct `Y - X beta` explicitly. The surface implementation's definition was already correct. |
| High | The locked Nilearn 0.14.0 release was yanked for truncating cleaned integer-image signals. | Pin 0.14.1 and test smoothed GLM estimates against equivalent floating-point input. |
| High | `plan_with_resume_guard` accepted failed receipts and even `{}`. | Require successful status, matching installed package version/revision, command, predecessor, effect, and existing paths. Verification receipts must match cohort and external paths. |
| High | Rerunning verification could leave an older successful receipt after failure. | Mark the receipt running before work; record failures so an earlier success cannot authorize resume. |
| High | Model submissions reused `lev1_units.txt` / `lev2_contrasts.txt`. A later submission could change queued array tasks. | Each real submission gets a unique, persistent roster file. |
| Moderate | Model shell commands rebuilt argv using unquoted string joins. | Shell-quote the executable, paths, and passed-through argument tokens. A test executes the generated shell with spaces and literal shell metacharacters. |
| Moderate | Model `--print` created directories and roster files. | Preview commands have no filesystem side effects. |
| Moderate | Same-slot integrations were ordered only by name. | An optional `after` list expresses dependencies; disabled/missing predecessors, cross-slot dependencies, and cycles fail planning. |
| Moderate | Surface prediction was rounded to float32 before baseline subtraction. | Subtract in float64 and cast at output. |
| Moderate | `--no-residual-filter` was honored only for CIFTI. | The option now has the same meaning in all supported spaces. |

The detailed scientific reasoning and synthetic audit are in
[network_glm's residual review](https://github.com/lobennett/network_glm/blob/122fa29cd11e724a6cfc8160841bcfc537bb2c82/docs/RESIDUALS-REVIEW.md).

## How to extend the pipeline

The normal contribution remains one package command plus one TOML manifest. For a QC
stage between `gs-pre` and `trim`, use `slot = "pre-trim"`; the planner supplies the
dependency edges, resources, prerequisite/output checks, and execution receipt. For a
second package in that slot, `after = ["first-package"]` states the ordering explicitly.
[EXTENDING.md](EXTENDING.md) contains the copyable manifest and validation commands.

Adding a new built-in boundary should require one lifecycle-slot declaration, one row
identifying its artifact/predecessor/successor, and a dependency test. Scientific code
still belongs in the package that implements the stage. Avoid dynamic task discovery,
another scheduler, or a generic plugin configuration language until a concrete stage
requires one.

## Reproducibility and simplicity

Use one versioned run TOML per scientific configuration and separate output directories
for different arms. Archive that TOML, the rendered JSON plan, immutable package pins,
DataLad dataset revisions, and execution records. A mutable Flywheel project or a
directory path alone is not a data version.

Slurm remains the execution mechanism. The new completion record is a local rule for
reusing one GLM run, not a workflow engine: the record is published after outputs are
written, and reuse requires the same content. Changing exclusions or minimum run count
refreshes fixed effects without changing a surviving run's fit. This also permits
contrast-only finalization to reuse completed work.

Smoothed GIFTI fits conservatively refit because their external FreeSurfer meshes and
executable are not part of the current input contract. Superseded contrast and
fixed-effects maps are retained with a terminal `.superseded-<id>` suffix that excludes
them from level-2 discovery. No automatic deletion is introduced.

Integration receipts intentionally do not recursively hash entire BIDS/fMRIPrep trees;
those trees can be many terabytes. They validate a successful matching execution
contract and path availability. DataLad revisions and verified campaign handoffs remain
responsible for dataset identity. GLM run reuse is stricter and hashes its individual
input/output files, so it incurs file-reading cost even when fitting is skipped.

Some simplification is already included: volume fitting uses one parameter dictionary,
plotting no longer controls unrelated work, shell argument construction uses standard
library quoting, and three filename-only GLM reuse branches become one completion
check. Keep future extraction driven by a repeated responsibility or concrete failure.
Moving every helper into a new module or adding interfaces around single functions would
make contribution harder without improving this pipeline.

## Validation and deployment

The original local GLM suite passed 428 tests despite the defects above. New regression
tests first reproduced the missing outputs, false-success paths, precision loss,
unsafe resume, overwritten rosters, and broken argument boundaries. The reviewed GLM
suite passes 452 tests locally. The numerical audit agrees with independently solved
AR(1) GLS to about `1e-12`.

Sherlock verification uses a separate scratch checkout and venv, a small Slurm CPU job,
`uv sync --frozen`, and explicit checks that installed sibling commits match their pins.
The verification script also tests the coordinated sibling source checkout and reports
the imported path. Final verification uses the published immutable `network_glm`
merge commit pinned in `pyproject.toml` and `uv.lock`; run IDs and counts are recorded
in the review handoff.

A fresh Sherlock allocation exposed an undocumented prerequisite: DataLad cannot import
with the default old Git, which lacks `--show-origin`. The setup instructions now load
the verified `system git/2.45.1` module along with `devel gcc/12.4.0`.

The reviewed GLM changes are merged and pinned by immutable commit.
Production checkouts, Flywheel tags, campaign state, and participant
outputs were not changed. Before a cohort rerun, compare corrected residuals with the
old outputs for one representative run and inspect the actual denoising settings.

## Decisions and follow-up checks

The main scientific decision is who owns FC denoising. Separate tissue/global-signal
regression after task regression can reintroduce task signal; filtering also interacts
with regression. Specify and validate a combined task/nuisance/filtering model before
calling the final residuals task-free. The current estimator and filtering defaults
were preserved during the engineering review.

Additional targeted checks concern required tissue-confound columns, medial-wall and
nonfinite surface vertices, native mesh selection when a subject has multiple
FreeSurfer sessions, approximate fixed-effects degrees of freedom, and propagation of
partial nonfinite effect/variance pairs. These require scientific/data validation and
should be separate from the orchestration changes.

There is also an older provenance-label issue: `network_glm.provenance` still uses
`neuro-workflow` as the dataset generator/package label. Code SHA and actual tool
versions remain recorded, but correcting that label and including timing sidecars and
consumed masks explicitly in the top-level input manifest would improve auditability.
The new per-run reuse signature includes the actual run files and available timing
sidecars; it does not replace the human-readable input manifest.
