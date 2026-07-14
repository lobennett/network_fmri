"""Make a staged BIDS tree a DataLad dataset.

Thin shell-out over the ``datalad`` binary, mirroring run.py's subprocess
pattern (capture output, ``SystemExit`` on nonzero). network_fmri stages BIDS
to ``$SCRATCH`` via ``fw2bids <cohort> --live --out``; this turns that on-disk
tree into a version-controlled DataLad dataset so large NIfTIs are git-annex'd
while text sidecars (``.tsv``/``.json``) stay in plain git (``text2git``).

Idempotent: on an already-created dataset it just ``datalad save``s, picking up
new/changed files.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run(cmd, cwd=None):
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if proc.returncode != 0:
        sys.stderr.write((proc.stdout or "") + (proc.stderr or ""))
        raise SystemExit(f"{' '.join(cmd)} failed (rc={proc.returncode})")
    return (proc.stdout or "") + (proc.stderr or "")


def dataladify(path, message="network_fmri: import BIDS", text2git=True) -> None:
    """DataLad-ify the BIDS tree at ``path`` (idempotent).

    If ``path/.datalad`` already exists it is an existing dataset -> just
    ``datalad save`` (picks up new/changed files). Otherwise ``datalad create
    --force`` (``--force`` because the dir already holds the staged BIDS tree),
    optionally with the ``text2git`` run-procedure so text files stay in git,
    then ``datalad save``.
    """
    path = Path(path)
    if not (path / ".datalad").exists():
        create = ["datalad", "create", "--force"]
        if text2git:
            create += ["-c", "text2git"]
        create.append(str(path))
        _run(create)
    _run(["datalad", "save", "-m", message, "."], cwd=str(path))
