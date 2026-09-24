"""Invoke upstream MechaBABS in its selected, locked campaign environment."""

from __future__ import annotations

import os
import re
import subprocess

from network_fmri.config import MechaBABSConfig


class Campaign:
    def __init__(self, config: MechaBABSConfig, *, runner=subprocess.run):
        self.config = config
        self.runner = runner

    def run(self, *arguments: str):
        bindir = self.config.campaign_dir / ".venv" / "bin"
        environment = dict(os.environ)
        environment.update(
            MECHABABS_CAMPAIGN=self.config.campaign,
            VIRTUAL_ENV=str(bindir.parent),
            PATH=str(bindir) + os.pathsep + environment.get("PATH", ""),
        )
        return self.runner(
            (str(bindir / "mechababs"), *arguments),
            cwd=str(self.config.study_dir), env=environment,
            check=True, capture_output=True, text=True,
        )


def read_table(text: str) -> tuple[dict[str, str], ...]:
    """Read upstream's fixed-width status/jobs table, preserving empty columns."""
    lines = text.splitlines()
    if not lines:
        raise RuntimeError("MechaBABS returned no table header")
    columns = list(re.finditer(r"\S+", lines[0]))
    if not columns or columns[0].group() != "source_dataset":
        raise RuntimeError("unexpected MechaBABS table schema")
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        rows.append({
            column.group(): line[column.start():columns[i + 1].start() if i + 1 < len(columns) else None].strip()
            for i, column in enumerate(columns)
        })
    return tuple(rows)
