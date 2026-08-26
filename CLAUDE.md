# network_fmri — working instructions

## Start here

Read [README.md](README.md) for the pipeline overview, then
[docs/AGENT-ONBOARDING.md](docs/AGENT-ONBOARDING.md) for the Sherlock environment,
canonical paths, operating workflow, and known failure modes.

Use the focused references when relevant:

- [docs/SCAN-NOTES.md](docs/SCAN-NOTES.md) for data exclusions and scientific decisions;
- [docs/GLM-DIAGNOSTICS.md](docs/GLM-DIAGNOSTICS.md) for RT arms, sparsity, and reliability;
- [docs/campaign/README.md](docs/campaign/README.md) for MRIQC, fMRIPrep, and XCP-D;
- [docs/EXTENDING.md](docs/EXTENDING.md) for adding a bounded Slurm stage;
- [CONTRIBUTING.md](CONTRIBUTING.md) for development and verification.

## Non-negotiables

- This host is Sherlock. `/etc/claude-code/CLAUDE.md` is authoritative. Run heavy work
  through Slurm, and verify modules, partitions, and storage rather than guessing.
- The canonical checkout is `~/noslop/network_fmri`; `~/network_fmri` is stale.
  Confirm imports before editing or testing.
- Inspect `git status` first and preserve unrelated changes.
- Use a scratch venv, `uv sync --frozen`, and `uv run --frozen pytest`. Verify installed sibling
  package commits against `pyproject.toml` before trusting a green suite.
- Run `network_fmri campaign -- iterate --dry-run` before every campaign advance. One
  tick may affect cells in every cohort.
- A failed Slurm job may have committed its output. Check the target DataLad history
  before repeating an expensive stage.
- Prefer source fixes over downstream exceptions. Keep code comments about current
  behavior; record history and rationale in commits and the focused reference docs.

## Keep documentation current

Update documentation in the same commit as the behavior it describes:

| Change | Update |
|---|---|
| User-facing command, stage, or output | `README.md` |
| Setup, path, environment, workflow, or diagnosed failure | `docs/AGENT-ONBOARDING.md` |
| Exclusion or scientific preprocessing decision | `docs/SCAN-NOTES.md` |
| Campaign config or vendored patch | `docs/campaign/` |
| Registry extension contract | `docs/EXTENDING.md` |
| Development workflow | `CONTRIBUTING.md` |

Delete stale claims. Live job state belongs in Slurm, campaign status, execution records,
and dataset history—not in a timeless guide.
