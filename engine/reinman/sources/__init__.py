"""Data source registry."""
from __future__ import annotations


from .base import ALL_CAPABILITIES, SourceAdapter
from .otb_live import OTBResults
from .puntersedge import PuntersEdge


def configured_sources(reserve=None, db=None) -> list[SourceAdapter]:
    """Live sources enabled through environment variables.

    PUNTERSEDGE_API_KEY: cards, odds, scratches and results. FIRECRAWL_API_KEY (with a
    database): OTB official results, listed after PuntersEdge so it only fills gaps.
    """
    sources: list[SourceAdapter] = []
    free = PuntersEdge(reserve)
    if free.configured:
        sources.append(free)
    otb = OTBResults(db)
    if otb.configured:
        sources.append(otb)
    return sources


def get_source(name: str) -> SourceAdapter:
    if name == "puntersedge":
        return PuntersEdge()
    if name == "otb":
        return OTBResults()
    if name.startswith("file:"):
        from .canonical import CanonicalFile
        return CanonicalFile(name[5:])
    raise KeyError(f"Unknown source {name!r}")


__all__ = ["ALL_CAPABILITIES", "SourceAdapter", "configured_sources", "get_source"]
