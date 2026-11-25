"""Import winner archives without pretending they contain full starting fields."""
from __future__ import annotations

import bisect
import json
import re
from collections import defaultdict
from datetime import date, datetime

from .db import Database
from .sources.tracks import canonical_track_code
from .util import jockey_id, norm_name, now_iso, surface_code, trainer_id

_REQUIRED = {"racedate", "racenumber", "horsename"}
_WINNER = {"winningpostposition", "winnerstrainer", "winnersjockey", "winnerssire"}
_TABLE = """CREATE TABLE IF NOT EXISTS historical_winners (
    track_id TEXT NOT NULL, race_date TEXT NOT NULL, race_number INTEGER NOT NULL,
    horse_key TEXT NOT NULL, horse_name TEXT NOT NULL, data TEXT NOT NULL,
    document_id INTEGER NOT NULL, imported_at TEXT NOT NULL,
    PRIMARY KEY (track_id, race_date, race_number, horse_key)
)"""


def _header(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def apply(db: Database, tables: list[list], doc: dict) -> dict | None:
    """Identify winner-specific schemas by headings, then validate each observation."""
    records = {}
    rejected = 0
    recognized = False
    for table in tables:
        columns = {}
        for row in table:
            header = {_header(x): i for i, x in enumerate(row)}
            if _REQUIRED <= header.keys() and _WINNER & header.keys():
                columns = header
                recognized = True
                continue
            if not columns or not any(row):
                continue
            values = {key: str(row[i]).strip() for key, i in columns.items() if i < len(row)}
            try:
                raw_date = values["racedate"]
                day = (date.fromisoformat(raw_date) if "-" in raw_date
                       else datetime.strptime(raw_date, "%m/%d/%Y").date())
                number = int(values["racenumber"])
                horse = values["horsename"]
                track = values.get("track") or values.get("trackcode") or values.get("trackid") or doc.get("track_id")
                # This specific original export has no track field.
                if not track and _WINNER <= columns.keys() and "racemeet" in columns:
                    if "keeneland" in doc["filename"].lower():
                        track = "KEE"
                track = canonical_track_code(track, track) if track else None
                if not track or not 1 <= number <= 99 or not norm_name(horse) or day >= date.fromisoformat(now_iso()[:10]):
                    raise ValueError("invalid historical race")
            except (KeyError, ValueError, TypeError):
                rejected += 1
                continue
            key = (track, day.isoformat(), number, norm_name(horse))
            records[key] = dict(zip(("track_id", "race_date", "race_number", "horse_key"), key),
                                horse_name=horse, data=json.dumps(values),
                                document_id=doc["id"], imported_at=now_iso())
    if not recognized:
        return None
    db.execute(_TABLE)
    db.insert_many("historical_winners", records.values(), verb="INSERT OR IGNORE")
    return {"historical_import": True, "winner_records": len(records), "rejected_rows": rejected,
            "message": f"Recognized {len(records):,} historical winner records ({rejected:,} rejected). "
                       "Available as historical evidence and model features."}


class WinnerHistory:
    """Counts of recorded winner observations, indexed by date; never win rates."""

    def __init__(self, rows=()):
        self.dates = defaultdict(list)
        for row in rows:
            values = json.loads(row["data"])
            track = row["track_id"]
            surface = surface_code(values.get("surface"))
            day = row["race_date"]
            for role, identity in (
                ("horse", row["horse_key"]),
                ("trainer", trainer_id(values.get("winnerstrainer"))),
                ("jockey", jockey_id(values.get("winnersjockey"))),
                ("sire", norm_name(values.get("winnerssire"))),
            ):
                if identity:
                    self.dates[(track, role, identity)].append(day)
                    if surface:
                        self.dates[(track, role, identity, surface)].append(day)
        for dates in self.dates.values():
            dates.sort()

    def count(self, track, role, identity, before, surface=None):
        key = (track, role, identity, surface) if surface else (track, role, identity)
        return bisect.bisect_left(self.dates.get(key, []), before)


def load(db: Database) -> WinnerHistory:
    db.execute(_TABLE)
    return WinnerHistory(db.query("SELECT track_id, race_date, horse_key, data FROM historical_winners"))
