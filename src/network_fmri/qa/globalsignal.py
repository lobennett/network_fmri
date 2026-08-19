"""Global-signal QA traces into derivatives/global_signal/<label>.

Reads echo-2 only — the tool's default, and one echo is enough for a global-signal trace,
so this does not mirror trim's coverage. A pure producer: no thresholds and no exclusion
decisions, which belong to network_qa.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from network_fmri import provenance
from network_fmri.cohorts import COHORTS, DEFAULT_STAGING, cohort_dataset


def record(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="network_fmri global-signal")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    p.add_argument("--label", required=True, help="e.g. pre-trim, post-trim")
    p.add_argument("--tr-marker", type=int, default=None,
                   help="draw a marker at this volume (e.g. 7 to show where trim cuts)")
    args = p.parse_args(argv)

    tree = cohort_dataset(args.staging, args.cohort)
    out = f"derivatives/global_signal/{args.label}"
    cmd = [
        str(Path(sys.executable).parent / "nf-global-signal"),
        "--bids-dir", ".",
        "--out-tsv", f"{out}/gs_metrics.tsv",
        "--out-pdf", f"{out}/gs_traces.pdf",
    ]
    if args.tr_marker is not None:
        cmd += ["--tr-marker", str(args.tr_marker)]

    (tree / out).mkdir(parents=True, exist_ok=True)
    provenance.run_recorded(
        tree, cmd,
        f"network_fmri@{provenance.code_version()}: global signal ({args.label}) {args.cohort}",
        outputs=[out], env=provenance.datalad_env(),
    )
    return 0
