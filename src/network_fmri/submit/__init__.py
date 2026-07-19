"""network_fmri submit — dispatch to the per-stage SLURM submit handlers.

``fw2bids submit <curate|export|merge|trim|events|fmap_link|datalad|select> ...``
renders and submits a single stage; ``fw2bids pipeline --cohort <c> ...`` submits
the whole chained DAG. Wired into the ``fw2bids`` CLI in :mod:`network_fmri.run`.
"""

from __future__ import annotations

import sys

_ROUTE_NAMES = ("curate", "export", "merge", "trim", "events", "fmap_link", "datalad", "select")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in _ROUTE_NAMES:
        sys.stderr.write(f"usage: fw2bids submit {{{'|'.join(_ROUTE_NAMES)}}} ...\n")
        raise SystemExit(2)
    from importlib import import_module

    mod = import_module(f"network_fmri.submit.{argv[0]}")
    return mod.main(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
