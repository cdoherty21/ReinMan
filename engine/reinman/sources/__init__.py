"""Data source registry."""
from __future__ import annotations


from .base import ALL_CAPABILITIES, SourceAdapter


def configured_sources() -> list[SourceAdapter]:
    """Live sources enabled through environment variables.

    None are configured: no free, permitted live U.S. source exists (see
    docs/DATA_SOURCES.md). Load data with `reinman import` (canonical JSON).
    Add a new adapter here when a suitable source becomes available.
    """
    return []


def get_source(name: str) -> SourceAdapter:
    if name.startswith("file:"):
        from .canonical import CanonicalFile
        return CanonicalFile(name[5:])
    raise KeyError(f"Unknown source {name!r}")


__all__ = ["ALL_CAPABILITIES", "SourceAdapter", "configured_sources", "get_source"]
