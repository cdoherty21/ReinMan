"""Wager-type vocabulary, leg counts and default takeout assumptions.

Takeout varies by state and track. These defaults are typical U.S. rates used only
when no track-specific value is configured (``TAKEOUT_OVERRIDES``); every EV shown in
the app is labeled as an estimate for that reason.
"""
from __future__ import annotations

import re

WAGER_TYPES = ["WIN", "PLACE", "SHOW", "EXACTA", "TRIFECTA", "SUPERFECTA",
               "DAILY_DOUBLE", "PICK3", "PICK4", "PICK5", "PICK6"]

POSITIONS = {"WIN": 1, "PLACE": 2, "SHOW": 3, "EXACTA": 2, "TRIFECTA": 3, "SUPERFECTA": 4}
LEGS = {"DAILY_DOUBLE": 2, "PICK3": 3, "PICK4": 4, "PICK5": 5, "PICK6": 6}

DEFAULT_TAKEOUT = {
    "WIN": 0.17, "PLACE": 0.17, "SHOW": 0.17,
    "EXACTA": 0.21, "DAILY_DOUBLE": 0.21,
    "TRIFECTA": 0.25, "SUPERFECTA": 0.25,
    "PICK3": 0.24, "PICK4": 0.20, "PICK5": 0.15, "PICK6": 0.25,
}
# e.g. {"KEE": {"PICK5": 0.15}} — fill from the track's published takeout schedule.
TAKEOUT_OVERRIDES: dict[str, dict[str, float]] = {}

MIN_BASE = {"WIN": 2.0, "PLACE": 2.0, "SHOW": 2.0, "EXACTA": 1.0, "TRIFECTA": 0.5,
            "SUPERFECTA": 0.1, "DAILY_DOUBLE": 1.0, "PICK3": 0.5, "PICK4": 0.5,
            "PICK5": 0.5, "PICK6": 0.2}


def takeout(track_id: str | None, wager_type: str) -> float:
    return TAKEOUT_OVERRIDES.get(track_id or "", {}).get(wager_type, DEFAULT_TAKEOUT.get(wager_type, 0.20))


def normalize_wager_type(*names: str | None) -> str:
    s = " ".join(n for n in names if n).upper()
    s = s.replace("-", " ").replace("_", " ")
    if re.search(r"\bSUPER\s*(FECTA)?\b|\bSUPERFECTA\b", s) and "HIGH 5" not in s:
        return "SUPERFECTA"
    if "TRIFECTA" in s or re.search(r"\bTRI\b", s):
        return "TRIFECTA"
    if "EXACTA" in s or "PERFECTA" in s or re.search(r"\bEX\b|\bEXA\b", s):
        return "EXACTA"
    if "DOUBLE" in s or re.search(r"\bDBL\b|\bDD\b", s):
        return "DAILY_DOUBLE"
    m = re.search(r"PICK\s*(\d)|\bP(\d)\b", s)
    if m:
        n = int(m.group(1) or m.group(2))
        if 3 <= n <= 6:
            return f"PICK{n}"
    if re.search(r"\bWIN\b|\bWN\b", s):
        return "WIN"
    if re.search(r"\bPLACE\b|\bPL\b", s):
        return "PLACE"
    if re.search(r"\bSHOW\b|\bSH\b", s):
        return "SHOW"
    return "OTHER"
