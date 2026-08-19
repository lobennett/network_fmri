"""Cohort rosters and where their BIDS trees live on staging.

An alias (``s19-2``) or a reassigned session never appears here; those resolve in
:mod:`network_fmri.fw2bids.sessions`.
"""

from __future__ import annotations

import os
from pathlib import Path

COHORTS: dict[str, tuple[str, ...]] = {
    "discovery": ("s03", "s10", "s19", "s29", "s43"),
    "validation": (
        "s76", "s180", "s216", "s247", "s286", "s295", "s300", "s320", "s321", "s336",
        "s373", "s394", "s415", "s480", "s599", "s645", "s874", "s956", "s1035", "s1057",
        "s1058", "s1127", "s1134", "s1175", "s1189", "s1258", "s1267", "s1270", "s1273",
        "s1292", "s1314", "s1326", "s1338", "s1351", "s1391", "s1399", "s1402", "s1408",
        "s1445", "s1481", "s1486",
    ),
    # Dropped participants: withdrawn, unreliable, or discontinued.
    "excluded": (
        "s214", "s222", "s250", "s297", "s432", "s823", "s968", "s1165", "s1178",
        "s1266", "s1320",
    ),
}


def roster(cohort: str) -> list[str]:
    try:
        return list(COHORTS[cohort])
    except KeyError:
        raise SystemExit(f"unknown cohort {cohort!r} (have: {', '.join(COHORTS)})") from None


# Not bids_staging: that holds the previous pipeline's output, our baseline.
DEFAULT_STAGING = str(Path(os.environ.get("SCRATCH", Path.home())) / "network_fmri")


def cohort_dataset(staging: str, cohort: str) -> Path:
    """The cohort's merged BIDS tree, which must already be a DataLad dataset."""
    tree = Path(staging) / cohort / "bids"
    if not (tree / ".datalad").is_dir():
        raise SystemExit(f"{tree} is not a DataLad dataset (run `network_fmri merge` first)")
    return tree
