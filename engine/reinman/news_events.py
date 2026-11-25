"""Turn racing news and posts into structured events about today's runners.

A headline is not a fact, so nothing here moves a probability directly. Each event names
a runner on the card (by the horse, its jockey or its trainer), a kind, the source and when
Rein Man first saw it:

* ``scratch`` / ``jockey_change`` / ``equipment``: shown as *unconfirmed* until the official
  card agrees (a scratch is confirmed when the runner is scratched on the card).
  Unconfirmed scratch or jockey talk about a race posting soon earns an extra odds read
  (``schedule.board_due``).
* ``barn_trouble`` (trainer or jockey suspended, a positive test, a quarantine) and
  ``health`` (injury, bleeding, setback about the horse): a caution flag, and the
  ``news_caution`` model input, recorded as of each race so training can test whether
  it predicts anything. It affects picks only if a trained model that uses it wins the
  promotion gate.

Matching is deliberately strict: the name and the event words must be in the same
sentence, horses need a distinctive name, people need two name words.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .db import Database
from .news import ensure_tables
from .util import norm_name, now_iso, to_iso

KINDS = {
    "scratch": (("horse",), re.compile(
        r"\b(scratch(?:ed|es|ing)?|withdrawn|will not (?:run|start)|won'?t (?:run|start)|declared out)\b")),
    "jockey_change": (("horse", "jockey"), re.compile(
        r"\b(named to ride|picks up the (?:mount|ride)|takes over (?:the )?(?:mount|ride)|jockey change|"
        r"replac(?:es|ed|ing) .{0,30}aboard|in for .{0,30}aboard|off (?:his|her|all) mounts)\b")),
    "equipment": (("horse",), re.compile(
        r"\b(blinkers? (?:on|off|added|removed)|(?:adds|removes|adding|removing) blinkers|first[- ]time "
        r"(?:lasix|blinkers))\b")),
    "barn_trouble": (("trainer", "jockey"), re.compile(
        r"\b(suspen(?:d|ded|ds|sion)|positive (?:test|for)|medication (?:violation|positive)|quarantin\w*|"
        r"ehv-?1?|ruled off|banned|fined)\b")),
    "health": (("horse",), re.compile(
        r"\b(injur(?:y|ed|ies)|lame(?:ness)?|bled|bleeder|vet'?s? list|setback|sidelined|fractur\w*|colic)\b")),
}
CAUTION = {"barn_trouble", "health"}
CONFIRMABLE = {"scratch", "jockey_change", "equipment"}
COMMON = {"STREET", "THUNDER", "MAGIC", "VICTORY", "LIBERTY", "FREEDOM", "WARRIOR", "DIAMOND", "SPIRIT",
          "TRIUMPH", "PRINCESS", "MONARCH", "GENERAL", "CAPTAIN", "JUSTICE", "HONOR", "COUNTRY"}


def _person(name: str | None) -> str:
    """'Ortiz, Jr., Irad' -> 'IRAD ORTIZ': people are written first-name-first in news."""
    if not name:
        return ""
    name = re.sub(r"\b(Jr|Sr|II|III)\b\.?", "", name)
    if "," in name:
        last, first = name.split(",", 1)
        name = f"{first} {last}"
    full = norm_name(name)
    return full if len(full.split()) >= 2 else ""


def _horse(name: str | None) -> str:
    full = norm_name(name)
    words = full.split()
    if len(words) >= 2 and len(full) >= 7:
        return full
    if len(words) == 1 and len(full) >= 7 and full not in COMMON:
        return full
    return ""


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def extract(items: list[dict], runners: list[dict]) -> list[dict]:
    """Events linking ``items`` (title + body) to ``runners`` (with race_id)."""
    aliases = []
    for r in runners:
        for role, name in (("horse", _horse(r.get("horse_name"))), ("jockey", _person(r.get("jockey_name"))),
                           ("trainer", _person(r.get("trainer_name")))):
            if name:
                aliases.append((f" {name} ", role, r))
    out, seen = [], set()
    for item in items:
        for sentence in _sentences(f"{item['title']}. {item.get('body') or ''}"):
            padded = f" {norm_name(sentence)} "
            lowered = sentence.lower()
            for needle, role, r in aliases:
                if needle not in padded:
                    continue
                for kind, (roles, pattern) in KINDS.items():
                    if role not in roles or not pattern.search(lowered):
                        continue
                    key = (r["race_id"], r["program_number"], kind, item["url"])
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({"race_id": r["race_id"], "program_number": r["program_number"],
                                "horse_name": r["horse_name"], "role": role, "entity": needle.strip().title(),
                                "kind": kind, "source": item["source"], "title": item["title"][:300],
                                "url": item["url"], "published_at": item["published_at"],
                                "first_seen_at": item["first_seen_at"], "sentence": sentence.strip()[:300]})
    return out


def ensure_table(db: Database) -> None:
    ensure_tables(db)
    db.execute("CREATE TABLE IF NOT EXISTS news_events (race_id TEXT NOT NULL, program_number TEXT NOT NULL, "
               "horse_name TEXT, role TEXT NOT NULL, entity TEXT, kind TEXT NOT NULL, source TEXT, title TEXT, "
               "url TEXT NOT NULL, published_at TEXT, first_seen_at TEXT NOT NULL, sentence TEXT, "
               "PRIMARY KEY (race_id, program_number, kind, url))")


def refresh(db: Database, race_date: str) -> dict:
    """Link the last week's news to the runners of races on ``race_date`` not yet run."""
    ensure_table(db)
    runners = db.query(
        "SELECT ru.race_id, ru.program_number, ru.horse_name, ru.jockey_name, ru.trainer_name FROM runners ru "
        "JOIN races ra ON ra.race_id = ru.race_id WHERE ra.race_date=? AND ra.status='scheduled'", [race_date])
    if not runners:
        return {"events": 0}
    since = to_iso(datetime.fromisoformat(f"{race_date}T00:00:00+00:00") - timedelta(days=7))
    items = db.query("SELECT n.*, b.body FROM racing_news n LEFT JOIN news_bodies b ON b.url = n.url "
                     "WHERE n.published_at >= ? AND n.first_seen_at <= ?", [since, now_iso()])
    events = extract(items, runners)
    if events:
        db.insert_many("news_events", events, verb="INSERT OR IGNORE")
    return {"events": len(events), "items": len(items)}


def for_race(db: Database, race_id: str, runners: list[dict], asof: str) -> list[dict]:
    """Events for one race known at ``asof``, with ``confirmed`` set from the official card."""
    try:
        rows = db.query("SELECT * FROM news_events WHERE race_id=? AND first_seen_at<=? ORDER BY first_seen_at",
                        [race_id, asof])
    except Exception:  # noqa: BLE001 — table not created yet
        return []
    scratched = {r["program_number"] for r in runners if r.get("scratched")}
    out = []
    for e in rows:
        e = dict(e)
        e["confirmed"] = (e["program_number"] in scratched) if e["kind"] == "scratch" else (
            None if e["kind"] in CONFIRMABLE else True)
        out.append(e)
    return out


def load_caution(db: Database) -> dict[tuple[str, str], list[str]]:
    """(race_id, program_number) -> first_seen_at of caution events, for the feature builder."""
    try:
        rows = db.query(f"SELECT race_id, program_number, first_seen_at FROM news_events WHERE kind IN "
                        f"({','.join(repr(k) for k in sorted(CAUTION))})")
    except Exception:  # noqa: BLE001
        return {}
    out: dict[tuple[str, str], list[str]] = {}
    for r in rows:
        out.setdefault((r["race_id"], r["program_number"]), []).append(r["first_seen_at"])
    return out


def urgent_races(db: Database, start: str, end: str) -> int:
    """Races posting in [start, end] with unconfirmed scratch or jockey talk."""
    try:
        return int(db.scalar(
            "SELECT COUNT(DISTINCT e.race_id) FROM news_events e JOIN races r ON r.race_id = e.race_id "
            "JOIN runners ru ON ru.race_id = e.race_id AND ru.program_number = e.program_number "
            "WHERE e.kind IN ('scratch','jockey_change') AND ru.scratched = 0 AND r.status='scheduled' "
            "AND r.post_time BETWEEN ? AND ?", [start, end]) or 0)
    except Exception:  # noqa: BLE001
        return 0


def summary(events: list[dict]) -> str:
    """A line for the take, in Rein Man's voice."""
    talk = [e for e in events if e["kind"] in CONFIRMABLE and not e["confirmed"]]
    caution = [e for e in events if e["kind"] in CAUTION]
    parts = []
    if talk:
        bits = "; ".join(f"#{e['program_number']} {e['horse_name']}: {e['kind'].replace('_', ' ')} "
                         f"({e['source']})" for e in talk[:3])
        parts.append(f"Word on the rail, unconfirmed: {bits}. I read rumours; I don't bet them until "
                     "the stewards agree.")
    if caution:
        bits = "; ".join(f"#{e['program_number']} {e['horse_name']} ({e['entity']}: "
                         f"{'barn news' if e['kind'] == 'barn_trouble' else 'health news'})" for e in caution[:3])
        parts.append(f"Noted, not priced: {bits}.")
    return " ".join(parts)


def as_notes(events: list[dict]) -> list[dict]:
    keep = ("program_number", "horse_name", "role", "entity", "kind", "source", "title", "url",
            "published_at", "confirmed", "sentence")
    return [{k: e.get(k) for k in keep} for e in events]

