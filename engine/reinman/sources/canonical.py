"""Canonical JSON importer.

Use this to load data you are licensed to use from any provider (for example a
past-performance file you purchased, converted to Rein Man's canonical format, or
the Equibase/TJC research datasets). The format is documented in
``docs/DATA_FORMAT.md`` and in ``sources/base.py``. Optional rich fields (past
performances with speed/pace figures, running positions, trouble comments,
workouts) are used by the feature builder when present and clearly marked as
unavailable when absent.

A file is ``{"cards": [card, ...], "results": [{"meet": meet, "races": [result,...]}, ...]}``.
"""
from __future__ import annotations

import json
from pathlib import Path


class CanonicalFile:
    name = "canonical_file"
    capabilities: dict[str, bool] = {}

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text())
        self.name = self.data.get("source") or "canonical_file"
        self.capabilities = self.data.get("capabilities") or {}

    def meets(self, race_date: str) -> list[dict]:
        return [c["meet"] for c in self.data.get("cards", []) if c["meet"]["race_date"] == race_date]

    def card(self, meet: dict) -> dict:
        for c in self.data.get("cards", []):
            if c["meet"]["meet_id"] == meet["meet_id"]:
                return c
        raise KeyError(meet["meet_id"])

    def results(self, meet: dict) -> list[dict]:
        for r in self.data.get("results", []):
            if r["meet"]["meet_id"] == meet["meet_id"]:
                return r["races"]
        return []

    def all_cards(self) -> list[dict]:
        return list(self.data.get("cards", []))

    def all_results(self) -> list[dict]:
        return list(self.data.get("results", []))
