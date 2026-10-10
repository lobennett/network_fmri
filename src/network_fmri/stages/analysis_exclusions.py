"""Install reviewed study exclusions without changing preprocessing eligibility."""

import csv
import io
from importlib.resources import files
from pathlib import Path


def install_known_exclusions(bids_dir: Path, *, source: Path | None = None) -> Path:
    source = source or files("network_fmri").joinpath("data/analysis_exclusions.tsv")
    approved = list(csv.DictReader(io.StringIO(source.read_text()), delimiter="\t"))
    target = Path(bids_dir) / "code/network_fmri/analysis_exclusions.tsv"
    columns = list(approved[0])
    rows = []
    if target.exists():
        with target.open() as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            if reader.fieldnames != columns:
                raise ValueError(f"Unexpected exclusion columns: {target}")
            rows = list(reader)
    keys = ("subject", "session", "task", "run", "analysis_scope")
    existing = {tuple(row[key] for key in keys): row for row in rows}
    for row in approved:
        # Do not introduce subjects outside this dataset (including pilot builds).
        if not any((root / row["subject"] / row["session"]).is_dir()
                   for root in (Path(bids_dir), Path(bids_dir) / "sourcedata/raw")):
            continue
        key = tuple(row[name] for name in keys)
        if key in existing:
            if existing[key] != row:
                raise ValueError(f"Conflicting reviewed exclusion: {key}")
        else:
            rows.append(row)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".tsv.tmp")
    with partial.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    partial.replace(target)
    return target
