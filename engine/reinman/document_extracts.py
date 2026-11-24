"""Parse the track summaries labeled as partial extracts."""
from __future__ import annotations

import csv
import re
from datetime import date

from .sources.tracks import canonical_track_code
from .util import norm_name, parse_distance_yards, parse_odds, parse_post_time, surface_code, to_int

_TITLE = re.compile(r"^([^|\n]+)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)
_PARTIAL = re.compile(r"^STATUS:\s*PARTIAL EXTRACT ONLY\b", re.M | re.I)
_RACE = re.compile(r"^Race\s+(\d{1,2})\s*\|\s*(.+)$", re.I)
_POST = re.compile(r"\b(\d{1,2}:\d{2})\s*(AM|PM)(?:\s*ET)?\s*$", re.I)
_DISTANCE = re.compile(r"\b\d+(?:\.\d+|\s+\d+/\d+)?\s*(?:miles?|furlongs?|yards?|f\b)(?:\s+\d+\s+yards?)?", re.I)
_SCRATCH = re.compile(r"^Reported scratch:\s*Race\s+(\d{1,2})\s+(.+)$", re.I)


def _conditions(race: dict, values: list[str], day: str) -> None:
    for value in values:
        value = value.strip()
        post = _POST.fullmatch(value)
        if post:
            race["post_time"] = parse_post_time(day, f"{post[1]} {post[2]}", tz="ET")
        if re.fullmatch(r"(?:purse\s+)?\$[\d,]+", value, re.I):
            race["purse"] = to_int(value.split("$", 1)[1])
        distance = _DISTANCE.search(value)
        if distance and (yards := parse_distance_yards(distance[0])):
            race.update(distance_desc=distance[0], distance_yards=yards)
            if surface := surface_code(value):
                race["surface"] = surface


def partial_extract(text: str) -> tuple[str, str, dict[int, dict]] | None:
    title = _TITLE.search(text)
    if not title or not _PARTIAL.search(text):
        return None
    track = canonical_track_code(title[1].strip(), title[1].strip())
    try:
        day = date.fromisoformat(title[2]).isoformat()
    except ValueError:
        return None
    if not track:
        return None

    races: dict[int, dict] = {}

    def race_for(number: int) -> dict:
        return races.setdefault(number, {"race_number": number, "runners": [], "scratch_reports": []})

    first = re.search(r"\bFirst post ET:\s*(\d{1,2}:\d{2}\s*[AP]M)\b", text, re.I)
    if first:
        _conditions(race_for(1), [first[1]], day)
    current = None
    schedule = False
    am_pm = first[1][-2:].upper() if first else None
    for line in text.splitlines():
        line = line.strip()
        if match := _RACE.fullmatch(line):
            current = race_for(int(match[1]))
            schedule = False
            values = match[2].split("|")
            current["race_class"] = re.sub(r"\s+\$[\d,]+$", "", values[0].strip()).title()
            _conditions(current, values[1:], day)
            continue
        if scratch := _SCRATCH.fullmatch(line):
            race_for(int(scratch[1]))["scratch_reports"].append(scratch[2])
            continue
        if line.lower().startswith("race schedule (et;"):
            schedule, current = True, None
            continue
        if schedule and re.match(r"^\d{1,2},\d{1,2}:\d{2},", line):
            values = next(csv.reader([line]))
            if len(values) >= 6:
                number, clock, purse, race_class, distance = values[:5]
                race = race_for(int(number))
                race["race_class"] = race_class.title()
                if re.fullmatch(r"\d+(?:\.\d+)?", distance):
                    distance += "f"
                _conditions(race, [f"${purse}", distance], day)
                # A clock without a meridiem is unsafe when the schedule spans noon or midnight.
                if am_pm == "PM" and first and int(first[1].split(":")[0]) != 12:
                    minutes = lambda value: int(value.split(":")[0]) * 60 + int(value.split(":")[1][:2])
                    if minutes(first[1]) <= minutes(clock) < 12 * 60:
                        _conditions(race, [f"{clock} PM"], day)
            continue
        fields = [part.strip() for part in line.split("|")]
        if current is None or len(fields) != 6 or not re.fullmatch(r"\d{1,2}[A-Z]?", fields[0], re.I):
            continue
        program, horse, figures, jockey, trainer, odds = fields
        if horse:
            current["runners"].append({
                "program_number": program.upper(), "horse_name": horse, "jockey_name": jockey or None,
                "jockey_id": f"jn:{norm_name(jockey)}" if jockey else None,
                "trainer_name": trainer or None, "trainer_id": f"tn:{norm_name(trainer)}" if trainer else None,
                "ml_odds": parse_odds(odds), "beyer_text": figures, "source_line": line,
            })
    return track, day, races
