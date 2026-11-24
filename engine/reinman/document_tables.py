"""Map labeled entry tables to canonical race cards."""
from collections import defaultdict
from datetime import datetime
import re

from .sources.tracks import canonical_track_code, track_info
from .util import norm_name, parse_distance_yards, parse_odds, parse_post_time, surface_code, to_float, to_int

_ALIASES = {
    "horse": ("horse", "horsename", "runner", "runnername"),
    "program": ("program", "programnumber", "prog", "p#", "cloth", "#"),
    "post": ("postposition", "postpos", "barrier", "pp"),
    "track": ("track", "trackid", "trackcode", "venue", "racecourse"),
    "date": ("date", "racedate", "meetingdate"),
    "race": ("race", "raceno", "racenumber", "race#"),
    "time": ("posttime", "scheduledpost", "starttime"),
    "odds": ("ml", "morningline", "mlodds", "morninglineodds"),
    "distance": ("distance", "racedistance"),
    "surface": ("surface", "racingsurface"),
    "condition": ("trackcondition",),
    "type": ("raceclass", "racetype", "class"),
    "purse": ("purse", "prize"),
    "jockey": ("jockey", "jockeyname", "rider"),
    "trainer": ("trainer", "trainername"),
    "weight": ("weight", "wgt", "carriedweight"),
    "sire": ("sire",), "dam": ("dam",), "dam_sire": ("damsire",),
    "equipment": ("equipment",), "medication": ("medication", "med"),
}
_COLUMNS = {alias: field for field, aliases in _ALIASES.items() for alias in aliases}


def _race_date(value: str) -> str | None:
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def table_cards(tables: list[list], hints: dict) -> list[dict]:
    grouped = defaultdict(list)
    for table in tables:
        columns = {}
        for row in table:
            header = {_COLUMNS.get(re.sub(r"[^a-z0-9#]", "", str(cell).lower())): i for i, cell in enumerate(row)}
            if {"horse", "program", "race"} <= header.keys():
                columns = {key: i for key, i in header.items() if key}
                continue
            if not columns:
                continue
            values = {key: str(row[i]).strip() if i < len(row) else "" for key, i in columns.items()}
            track_name = values.get("track") or hints.get("track_id") or ""
            track = canonical_track_code(track_name, track_name)
            day = _race_date(values.get("date") or hints.get("race_date") or "")
            number = re.fullmatch(r"(?:Race\s*)?(\d{1,2})", values.get("race", ""), re.I)
            program = values.get("program", "").upper().lstrip("#")
            name = values.get("horse", "")
            if track and day and number and int(number[1]) and name and re.fullmatch(r"\d{1,2}[A-Z]?", program):
                grouped[(track, day, int(number[1]))].append((program, name, values))

    cards = []
    for (track, day, number), entries in grouped.items():
        unique = {}
        conflicting = False
        for program, name, values in entries:
            if program in unique and unique[program][0] != name:
                conflicting = True
            unique[program] = (name, values)
        if conflicting or len(unique) < 2:
            continue
        info = track_info(track)
        race = {"race_number": number}
        for key, target, parse in (
            ("time", "post_time", lambda v: parse_post_time(day, v, track_tz=info.get("tz"))),
            ("distance", "distance_yards", parse_distance_yards), ("surface", "surface", surface_code),
            ("type", "race_class", str), ("condition", "track_condition", str), ("purse", "purse", to_int),
        ):
            values = {row[key] for _, row in unique.values() if row.get(key)}
            if len(values) == 1:
                try:
                    raw = values.pop()
                    if target == "post_time" and re.match(r"\d{4}-\d{2}-\d{2}", raw) and not raw.startswith(day):
                        continue
                    race[target] = parse(raw)
                except (ValueError, OverflowError):
                    continue
        race.setdefault("post_time", None)
        race["runners"] = [
            {"program_number": program, "horse_name": name, "horse_id": f"name:{norm_name(name)}",
             "post_position": to_int(row.get("post")), "weight": to_float(row.get("weight")),
             "jockey_id": f"jn:{norm_name(row['jockey'])}" if row.get("jockey") else None,
             "trainer_id": f"tn:{norm_name(row['trainer'])}" if row.get("trainer") else None,
             "ml_odds": parse_odds(row.get("odds")),
             **{field: row.get(key) or None for key, field in (
                 ("jockey", "jockey_name"), ("trainer", "trainer_name"), ("sire", "sire"),
                 ("dam", "dam"), ("dam_sire", "dam_sire"), ("equipment", "equipment"), ("medication", "medication"),
             )}}
            for program, (name, row) in unique.items()
        ]
        cards.append({"meet": {"track_id": track, "track_name": info.get("name", track), "race_date": day,
                               "meet_id": f"document:{track}:{day}"}, "races": [race]})
    return cards
