"""Link each session's B0 field map to the BOLD runs it corrects.

fMRIPrep/SDCFlows groups a field map, its magnitude and the BOLD runs it corrects by a
shared ``B0FieldIdentifier``, so without this there is no distortion correction. Every
session here has exactly one Hz field map, which is what makes a per-session identifier
sufficient.

Runs after ``trim`` and before MRIQC.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from network_fmri.prepare.sidecar import path_for, update

log = logging.getLogger(__name__)


def link_tree(bids_dir: Path) -> dict:
    """Stamp ``B0Field*`` across a cohort tree.

    Identifier is ``<subject>_<ses>`` (e.g. ``s1035_ses-01``): it goes on the field map and
    its magnitude as ``B0FieldIdentifier``, and on every BOLD in the session as
    ``B0FieldSource``. A session with BOLD but no field map gets no SDC and is counted; a
    field map with no BOLD is counted and skipped.
    """
    summary = {"sessions": 0, "bold": 0, "fmap": 0, "no_fmap": 0, "orphan_fmap": 0}

    for ses in sorted(bids_dir.glob("sub-*/ses-*")):
        if not ses.is_dir():
            continue
        fmaps = sorted(ses.glob("fmap/*_fieldmap.nii.gz"))
        bolds = sorted(ses.glob("func/*_bold.nii.gz"))

        if len(fmaps) > 1:
            # Asserted never: the fmap template hardcodes run-1, so a second field map
            # would silently overwrite the first.
            raise ValueError(f"{ses}: {len(fmaps)} field maps, expected exactly one")
        if not fmaps:
            if bolds:
                summary["no_fmap"] += 1
                log.warning("%s: BOLD present but no field map — no SDC", ses)
            continue
        if not bolds:
            summary["orphan_fmap"] += 1
            log.warning("%s: field map present but no BOLD — skipped", ses)
            continue

        ident = f"{ses.parent.name.removeprefix('sub-')}_{ses.name}"
        magnitude = fmaps[0].with_name(fmaps[0].name.replace("_fieldmap.", "_magnitude."))
        for nii in (fmaps[0], magnitude):
            if update(path_for(nii), B0FieldIdentifier=ident):
                summary["fmap"] += 1
        for nii in bolds:
            if update(path_for(nii), B0FieldSource=ident):
                summary["bold"] += 1
        summary["sessions"] += 1

    return summary


def get_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="network_fmri b0link-run")
    p.add_argument("--bids-dir", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = get_parser().parse_args(argv)
    print(f"[b0link] {link_tree(Path(args.bids_dir))}", flush=True)
    return 0


def record(argv: list[str] | None = None) -> int:
    """Record a B0 field-map linking pass over the cohort dataset."""
    from network_fmri import provenance
    from network_fmri.cohorts import COHORTS, DEFAULT_STAGING, cohort_dataset

    p = argparse.ArgumentParser(prog="network_fmri b0link")
    p.add_argument("--cohort", required=True, choices=list(COHORTS))
    p.add_argument("--staging", default=DEFAULT_STAGING)
    args = p.parse_args(argv)

    tree = cohort_dataset(args.staging, args.cohort)
    provenance.run_recorded(
        tree,
        [str(Path(sys.executable).parent / "network_fmri"), "b0link-run", "--bids-dir", "."],
        f"network_fmri@{provenance.code_version()}: link B0 field maps in {args.cohort}",
        outputs=[], env=provenance.datalad_env(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
