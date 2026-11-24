"""Data source registry."""
from __future__ import annotations


from .base import ALL_CAPABILITIES, SourceAdapter
from .racingapi import RacingApiNA


def configured_sources() -> list[SourceAdapter]:
    """Live sources enabled through environment variables."""
    out: list[SourceAdapter] = []
    ra = RacingApiNA()
    if ra.configured:
        out.append(ra)
    return out


def get_source(name: str) -> SourceAdapter:
    if name in ("theracingapi_na", "racingapi"):
        return RacingApiNA()
    if name.startswith("file:"):
        from .canonical import CanonicalFile
        return CanonicalFile(name[5:])
    raise KeyError(f"Unknown source {name!r}")


__all__ = ["ALL_CAPABILITIES", "SourceAdapter", "RacingApiNA", "configured_sources", "get_source"]
