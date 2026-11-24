"""Read complete entry fields from supported Equibase program layouts."""
from __future__ import annotations

import re
from datetime import datetime
import subprocess
from collections import defaultdict

from .sources.tracks import track_info
from .util import norm_name, parse_odds, parse_post_time, to_int

_POCKET_ID = re.compile(r"^\s*([A-Z0-9]{1,8})-(\d{1,2}/\d{1,2}/20\d{2})-(\d{1,2})\s*$", re.M)
_POCKET_RUNNER = re.compile(r"^ {1,5}(\d{1,2}[A-Z]?)(?= |$)", re.M)
_POCKET_NAME = re.compile(r" {2,}([A-Z][A-Z0-9' ().&/-]*?) {2,}20\d{2}: *\d+")
_FL_FOOTER = re.compile(r"^\s*(\d{1,2}/\d{1,2}/20\d{2}) Race (\d{1,2})(?:\s|$)", re.M)
_FL_PROGRAM = re.compile(r"^ {2,12}([0-9]{1,2}[A-Z]?)(?: +(\d{1,3}-\d{1,2}))? *$", re.M)
_FL_HORSE = re.compile(
    r"^ *(?:(?:[A-Za-z]+ *)?\$[\d,]+ +)?([A-Z][A-Za-z0-9' .&/-]*?)"
    r"(?: +(?:\d{1,3}|--))?µ?(?: +\([A-Z0-9]+\))? {2,}"
    r"(?=.*(?:[1-9]\d{2}|\( \d+-))[^\n]*\n"
    r"(?: +(?:\d{3}|\( \d+-)[^\n]*\n){0,3}[^\n]* - Bred in ",
    re.M,
)
_ODDS = re.compile(r"^\s*(\d{1,3}-\d{1,2})(?=\s|$)", re.M)


def pdf_layout(data: bytes) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", "-", "-"], input=data, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True, timeout=30,
    ).stdout.decode("utf-8", errors="replace")


def _runner(program: str, name: str, block: str) -> dict:
    odds = _ODDS.search(block)
    return {"program_number": program, "horse_name": name.strip(),
            "horse_id": f"name:{norm_name(name)}", "ml_odds": parse_odds(odds[1]) if odds else None}


def _race(track: str, day: str, number: int, text: str, runners: list[dict], post: str | None = None) -> dict | None:
    programs = [r["program_number"] for r in runners]
    # A field must have unique programs and an opening betting interest. This
    # keeps truncated pages and ads from creating widely incomplete cards.
    numbers = {int(re.match(r"\d+", p)[0]) for p in programs}
    if (len(programs) < 2 or len(programs) != len(set(programs))
            or numbers != set(range(1, max(numbers) + 1))):
        return None
    purse = re.search(r"\bPurse\s+\$([\d,]+)", text)
    info = track_info(track)
    return {"meet": {"meet_id": f"document:{track}:{day}", "track_id": track, "race_date": day,
                     "track_name": info.get("name", track)},
            "races": [{"race_number": number, "post_time": parse_post_time(day, post, track_tz=info.get("tz")),
                       "purse": to_int(purse[1]) if purse else None, "runners": runners}]}


def _date(raw: str) -> str:
    return datetime.strptime(raw, "%m/%d/%Y").date().isoformat()


def program_cards(text: str) -> list[dict]:
    pocket = list(_POCKET_ID.finditer(text))
    if pocket and "PAST PERFORMANCES" in text and "MAKE SELECTION BY PROGRAM NUMBER" in text:
        identities = {m.groups() for m in pocket}
        if len(identities) != 1:
            return []
        track, day, number = identities.pop()
        if track == "EVD" or "QUARTER HORSE" in text[:1500].upper():
            return []
        field = text.split("PAST PERFORMANCES", 1)[1]
        matches = list(_POCKET_RUNNER.finditer(field))
        names = _POCKET_NAME.findall(field)
        if len(names) != len(matches):
            return []
        runners = [_runner(m[1], names[i], field[m.end(): matches[i+1].start() if i+1 < len(matches) else len(field)])
                   for i, m in enumerate(matches)]
        card = _race(track, _date(day), int(number), text, runners)
        return [card] if card else []

    if "Finger Lakes Racing Association" not in text or "Official Racing Program" not in text:
        return []
    pages = defaultdict(list)
    date_match = _FL_FOOTER.search(text)
    if not date_match:
        return []
    day = _date(date_match[1])
    for page in text.split("\f"):
        footer = _FL_FOOTER.search(page)
        continued = re.match(r"\s*RACE (\d+) CONTINUED", page)
        if footer:
            pages[(_date(footer[1]), int(footer[2]))].append(page)
        elif continued:
            pages[(day, int(continued[1]))].append(page)
    first_post = re.search(r"\bPost Time\s+(\d{1,2}:\d{2}\s*[ap]m)\b", text, re.I)
    cards = []
    for (day, number), race_pages in pages.items():
        body = "\n".join(race_pages)
        matches = list(_FL_PROGRAM.finditer(body))
        runners = []
        for i, m in enumerate(matches):
            block = body[m.end(): matches[i+1].start() if i+1 < len(matches) else len(body)]
            horse = _FL_HORSE.search(block.split("Workout", 1)[0])
            if "Owner:" in block and horse:
                runner = _runner(m[1], horse[1], block)
                if m[2]:
                    runner["ml_odds"] = parse_odds(m[2])
                runners.append(runner)
        if len(runners) != len(matches):
            continue
        card = _race("FL", day, number, body, runners, first_post[1] if number == 1 and first_post else None)
        if card:
            cards.append(card)
    return cards
