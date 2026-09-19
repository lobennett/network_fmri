"""Small, ordered operations used by the single-dataset workflow."""

from __future__ import annotations


class StageError(RuntimeError):
    """A pipeline stage could not establish one of its required invariants."""


__all__ = ["StageError"]
