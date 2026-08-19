"""Regenerate the mechababs study metadata TSVs from a BIDS tree.

mechababs's ``selection`` rule filters on these columns, so they must match the tree
after any pruning (a stale ``t1w_num`` would queue MRIQC on a scan that no longer
exists). Written as code rather than by hand so a campaign can be re-derived.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

DATATYPES = ("anat", "dwi", "fmap", "func", "perf")


def scan(bids_dir: Path) -> dict:
    """{(subject, session): {datatypes, t1w_num, t2w_num, bold_num}}."""
    out = {}
    for sub in sorted(bids_dir.glob("sub-*")):
        if not sub.is_dir():
            continue
        for ses in sorted(sub.glob("ses-*")):
            present = sorted(d for d in DATATYPES if (ses / d).is_dir())
            anat = ses / "anat"
            out[(sub.name, ses.name)] = dict(
                datatypes=",".join(present),
                t1w_num=len(list(anat.glob("*_T1w.nii.gz"))) if anat.is_dir() else 0,
                t2w_num=len(list(anat.glob("*_T2w.nii.gz"))) if anat.is_dir() else 0,
                # echo-1 only: multi-echo BOLD would otherwise count each echo.
                bold_num=len(list((ses / "func").glob("*echo-1_bold.nii.gz")))
                if (ses / "func").is_dir() else 0,
            )
    return out


def write(bids_dir: Path, out_dir: Path) -> tuple[int, int]:
    rows = scan(bids_dir)
    cols = ("datatypes", "t1w_num", "t2w_num", "bold_num")

    ses_tsv = out_dir / "sourcedata+subjects+sessions.tsv"
    with ses_tsv.open("w") as f:
        f.write("subject_id\tsession_id\t" + "\t".join(cols) + "\n")
        for (sub, ses), r in sorted(rows.items()):
            f.write(f"{sub}\t{ses}\t" + "\t".join(str(r[c]) for c in cols) + "\n")

    # Subject level: union of datatypes, sum of counts.
    agg: dict = collections.defaultdict(lambda: dict(dt=set(), t1w_num=0, t2w_num=0, bold_num=0))
    for (sub, _), r in rows.items():
        a = agg[sub]
        a["dt"].update(x for x in r["datatypes"].split(",") if x)
        for c in ("t1w_num", "t2w_num", "bold_num"):
            a[c] += r[c]

    sub_tsv = out_dir / "sourcedata+subjects.tsv"
    with sub_tsv.open("w") as f:
        f.write("subject_id\t" + "\t".join(cols) + "\n")
        for sub, a in sorted(agg.items()):
            f.write(f"{sub}\t{','.join(sorted(a['dt']))}\t"
                    + "\t".join(str(a[c]) for c in ("t1w_num", "t2w_num", "bold_num")) + "\n")

    return len(agg), len(rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="network_fmri study-meta")
    p.add_argument("--bids-dir", required=True)
    p.add_argument("--out", required=True, help="directory to write both TSVs into")
    args = p.parse_args(argv)
    n_sub, n_ses = write(Path(args.bids_dir), Path(args.out))
    print(f"[study-meta] {n_sub} subjects, {n_ses} sessions", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
