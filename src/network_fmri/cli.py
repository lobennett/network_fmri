"""``network_fmri`` — Flywheel -> BIDS with provenance, as Slurm jobs.

Verb dispatch only. Each stage owns its own logic and its ``datalad run`` wrapper:

    fw2bids/   Flywheel -> BIDS (submit, import-subject, merge, curate)
    prepare/   in-place fixes to the exported tree (trim, b0link, fix-sidecars)
    behavior/  raw behavioural -> sourcedata
    qa/        BIDS validation, global-signal traces

The ``*-run`` verbs are the inner commands ``datalad run`` records; the bare verb is the
recorded wrapper around it.
"""

from __future__ import annotations

import sys

_USAGE = """usage:
  network_fmri submit fw-heudiconv [options]   render + sbatch a per-subject array
  network_fmri curate [options]                run one subject here (what a task does)
  network_fmri import-subject [options]        curate+export one subject via datalad run
  network_fmri merge --cohort C                rsync per-subject parts into one tree
  network_fmri fix-sidecars --cohort C         coerce sidecar fields to BIDS types
  network_fmri validate --cohort C [options]   run the BIDS validator on the merged tree
  network_fmri global-signal --cohort C --label L   global-signal QA -> derivatives/
  network_fmri trim --cohort C [options]       trim dummy volumes in place
  network_fmri b0link --cohort C               link field maps to their BOLD runs
  network_fmri behavior-clean --cohort C       materialise the cleaned 1:1 behavioral tree
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv[:2] == ["submit", "fw-heudiconv"]:
        from network_fmri.fw2bids.jobs import submit

        return submit(argv[2:])

    verb, rest = (argv[0], argv[1:]) if argv else ("", [])

    if verb == "curate":
        from network_fmri.fw2bids.curate import main as f

        return f(rest)
    if verb == "import-subject":
        from network_fmri.fw2bids.jobs import import_subject as f

        return f(rest)
    if verb == "merge":
        from network_fmri.fw2bids.jobs import merge as f

        return f(rest)
    if verb == "trim":
        from network_fmri.prepare.trim import record as f

        return f(rest)
    if verb == "trim-bold":
        from network_fmri.prepare.trim import main as f

        return f(rest)
    if verb == "b0link":
        from network_fmri.prepare.b0link import record as f

        return f(rest)
    if verb == "b0link-run":
        from network_fmri.prepare.b0link import main as f

        return f(rest)
    if verb == "fix-sidecars":
        from network_fmri.prepare.sidecars import record as f

        return f(rest)
    if verb == "fix-sidecars-run":
        from network_fmri.prepare.sidecars import main as f

        return f(rest)
    if verb == "behavior-clean":
        from network_fmri.behavior.clean import record as f

        return f(rest)
    if verb == "behavior-clean-run":
        from network_fmri.behavior.clean import clean as f

        return f(rest)
    if verb == "global-signal":
        from network_fmri.qa.globalsignal import record as f

        return f(rest)
    if verb == "validate":
        from network_fmri.qa.validate import main as f

        return f(rest)

    sys.stderr.write(_USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
