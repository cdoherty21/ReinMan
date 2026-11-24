"""Data source registry."""
from __future__ import annotations


from .base import ALL_CAPABILITIES, SourceAdapter
from .puntersedge import PuntersEdge


def configured_sources(reserve=None) -> list[SourceAdapter]:
    """Live sources enabled through environment variables (PUNTERSEDGE_API_KEY)."""
    free = PuntersEdge(reserve)
    return [free] if free.configured else []


def get_source(name: str) -> SourceAdapter:
    if name == "puntersedge":
        return PuntersEdge()
    if name.startswith("file:"):
        from .canonical import CanonicalFile
        return CanonicalFile(name[5:])
    raise KeyError(f"Unknown source {name!r}")


__all__ = ["ALL_CAPABILITIES", "SourceAdapter", "configured_sources", "get_source"]
