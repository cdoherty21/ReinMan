"""Read Equibase result charts uploaded on the Documents page.

Built from a real upload (Parx, 2026-11-23, ``pdftotext -layout`` text). Each race is one
section headed ``PARX RACING - November 23, 2026 - Race 1`` (PDF extraction sometimes
splits the track name, e.g. ``PAR X RACING``) and contains, in order:

* conditions, ``Distance: Six Furlongs On The Dirt``, ``Track: Fast``;
* the starters table, in finish order::

    25Oct26 1PRX2   1   Donna Light (Adorno, Abner)   122   Lb   1   5   41/2  41  1Head  13 1/2   2.50   angled out, edged off

  (last race, program, horse, jockey, weight, meds/equipment, post, start, the calls,
  odds with ``*`` for the favorite, trip comment);
* ``Fractional Times: 23.18 47.54 1:13.82   Final Time: 1:20.57``;
* ``Scratched Horse(s): Likeastraightshot (Veterinarian)``;
* the win/place/show table beside the exotic payoffs (``$0.50 Pick 4  1-4-2-1/3/5 (4 correct)  68.45``);
* ``Past Performance Running Line Preview``: position and lengths *behind the leader* at
  each call (``41 1/2`` = 4th, 1 1/2 lengths back; the leader's figure is its lead).

Each charted race gives two things:

* an official result (finish order, payoffs, scratches, times), stored only when Rein Man
  holds the card and every starter's number and name agree with it;
* a past-performance line per starter (calls, lengths behind, odds, comment, trouble),
  kept in ``chart_lines`` by horse so it feeds that horse's *later* races.

Races with a dead heat or a disqualification, or whose preview disagrees with the finish
order, are skipped rather than guessed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from fractions import Fraction

from .db import Database
from .ingest import race_id_for, store_results
from .past_performances import TROUBLE
from .programs import _distance_yards
from .sources.tracks import TRACK_NAMES, TRACKS, canonical_track_code
from .util import norm_name, now_iso, to_float
from .wagertypes import normalize_wager_type

PREVIEW = "Past Performance Running Line Preview"
_TITLE = re.compile(r"^\f?(?P<track>[A-Z][A-Z .&'-]+?) - (?P<date>[A-Z][a-z]+ \d{1,2}, \d{4}) - Race (?P<race>\d{1,2})\s*$", re.M)
_STARTER = re.compile(
    r"^(?P<last>---|\d{1,2}[A-Z][a-z]{2}\d{2}\s+\d{1,2}[A-Z]{1,4}\d{1,2})\s+(?P<pgm>\d{1,2}[A-Z]?)\s+"
    r"(?P<name>.+?)\s+\((?P<jockey>[^()]+)\)\s+(?P<wgt>\d{3})\s+(?P<me>[A-Za-z ]*?)\s*(?<!\S)(?P<pp>\d{1,2})\s+"
    r"(?P<start>\d{1,2})\s+(?P<calls>.+?)\s+(?P<odds>\d+\.\d{2})(?P<fav>\*)?(?:\s+(?P<comment>.*))?$")
_PREVIEW_ROW = re.compile(r"^\s+(?P<pgm>\d{1,2}[A-Z]?)\s+(?P<name>\S.*?)\s{2,}(?P<start>\d{1,2})\s{2,}(?P<calls>\S.*\S)\s*$")
_WPS = re.compile(r"^\s?(?P<pgm>\d{1,2}[A-Z]?)\s{2,}(?P<name>\S.*?)\s{2,}(?P<nums>(?:\d+\.\d{2}\s*)+)(?P<rest>\$.*)?$")
_WAGER = re.compile(r"\$(?P<base>\d*\.\d{2})\s+(?P<name>[A-Za-z][A-Za-z0-9 ]*?)\s{2,}(?P<numbers>[0-9A-Z/-]+)"
                    r"(?:\s*\((?P<correct>\d+) correct\))?\s{2,}(?P<payoff>[\d,]+\.\d{2})")
_MARGIN = {"Nose": 0.05, "Head": 0.1, "Neck": 0.25}
_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}\.\d{2})")


def _seconds(raw: str | None) -> float | None:
    m = _TIME.fullmatch(raw or "")
    return round(int(m[1] or 0) * 60 + float(m[2]), 2) if m else None


def _margin(raw: str) -> float | None:
    if raw == "":
        return 0.0
    if raw in _MARGIN:
        return _MARGIN[raw]
    m = re.fullmatch(r"(\d+)?(?: ?(\d/\d))?", raw)
    if not m or not (m[1] or m[2]):
        return None
    return float(int(m[1] or 0) + (Fraction(m[2]) if m[2] else 0))


def _splits(token: str, n: int) -> list[tuple[int, float]]:
    """Ways to read a call figure such as ``112`` (1st by 12, or 11th by 2) in a field of n."""
    out = []
    for k in (1, 2):
        if len(token) >= k and token[:k].isdigit():
            pos, margin = int(token[:k]), _margin(token[k:].strip())
            if 1 <= pos <= n and margin is not None and not (k == 2 and token[0] == "0"):
                out.append((pos, margin))
    return out


def _resolve(tokens: list[str], n: int) -> list[tuple[int, float]] | None:
    """Pick one reading per horse so the positions at this call are exactly 1..n."""
    options = [_splits(t, n) for t in tokens]
    if any(not o for o in options):
        return None
    chosen: list[tuple[int, float] | None] = [None] * len(tokens)

    def search(i: int, used: set[int]) -> bool:
        if i == len(tokens):
            return True
        for pos, margin in options[i]:
            if pos not in used:
                chosen[i] = (pos, margin)
                if search(i + 1, used | {pos}):
                    return True
        return False

    return chosen if search(0, set()) and len(set(p for p, _ in chosen)) == n else None  # type: ignore[misc]


def _track_code(title: str, filename: str | None) -> str | None:
    letters = re.sub(r"[^A-Z]", "", title.upper())
    by_letters = {re.sub(r"[^A-Z]", "", name): code for name, code in TRACK_NAMES.items()}
    code = by_letters.get(letters) or canonical_track_code(title)
    if not code and filename:
        m = re.search(r"\d{8}-usa-([a-z]{1,4})-", filename.lower())
        code = m[1].upper() if m and m[1].upper() in TRACKS else None
    return code


def split_races(text: str) -> list[tuple[re.Match, str]]:
    titles = list(_TITLE.finditer(text))
    return [(m, text[m.end():(titles[i + 1].start() if i + 1 < len(titles) else len(text))])
            for i, m in enumerate(titles)]


def is_chart(text: str) -> bool:
    return PREVIEW in text and bool(_TITLE.search(text)) and "Last Raced" in text


def parse_race(title: re.Match, body: str, track: str) -> dict | None:
    race_date = datetime.strptime(title["date"], "%B %d, %Y").date().isoformat()
    number = int(title["race"])
    lines = body.splitlines()
    starters = [m for m in (_STARTER.match(ln) for ln in lines) if m]
    n = len(starters)
    if n < 2:
        return None
    flags = re.search(r"\bDH\b|Dead Heat|Disqualif|\bDQ\b", body)

    # Preview: positions and lengths behind the leader at each call.
    head = next((i for i, ln in enumerate(lines) if PREVIEW in ln), None)
    preview_rows = []
    if head is not None:
        for ln in lines[head + 2:head + 2 + n + 3]:
            m = _PREVIEW_ROW.match(ln)
            if m:
                preview_rows.append(m)
    calls_by_pgm: dict[str, list[tuple[int, float]]] = {}
    if len(preview_rows) == n:
        columns = [re.split(r"\s{2,}", m["calls"]) for m in preview_rows]
        width = {len(c) for c in columns}
        if len(width) == 1:
            resolved = [_resolve([c[j] for c in columns], n) for j in range(width.pop())]
            if all(resolved):
                for i, m in enumerate(preview_rows):
                    calls_by_pgm[m["pgm"]] = [col[i] for col in resolved]  # type: ignore[index]

    # Win/place/show and exotic payoffs.
    header = re.search(r"^Pgm Horse\s+Win\s+Place(?P<show>\s+Show)?", body, re.M)
    has_show = bool(header and header["show"])
    wps: dict[str, list[float]] = {}
    payoffs, seen = [], set()
    for ln in lines:
        m = _WPS.match(ln)
        if m:
            wps[m["pgm"]] = [float(x) for x in m["nums"].split()]
        for w in _WAGER.finditer(ln):
            wager_type = normalize_wager_type(w["name"])
            if wager_type == "OTHER":
                continue
            if w["correct"] and wager_type.startswith("PICK") and int(w["correct"]) != int(wager_type[-1]):
                continue
            item = {"wager_type": wager_type, "wager_name": w["name"].strip(), "base_amount": float(w["base"]),
                    "winning_numbers": w["numbers"], "payoff_amount": to_float(w["payoff"])}
            key = tuple(item.values())
            if key not in seen:
                seen.add(key)
                payoffs.append(item)

    dist = re.search(r"Distance: (.+?) On The (Dirt|Turf|Inner Turf|All Weather|Synthetic)", body)
    cond = re.search(r"Track: ([A-Za-z ]+?)\s*$", body, re.M)
    fractions = re.search(r"Fractional Times: (.+?)\s{2,}Final Time: (\S+)", body)
    scratched = re.search(r"Scratched Horse\(s\): (.+)", body)
    scratches = [s.strip() for s in re.findall(r"([^,()]+?)\s*(?:\([^)]*\))?\s*(?:,|$)", scratched[1])
                 if s.strip()] if scratched else []

    finishers, lines_out = [], []
    ok = not flags and len(calls_by_pgm) == n
    for place, m in enumerate(starters, start=1):
        calls = calls_by_pgm.get(m["pgm"])
        if calls and calls[-1][0] != place:
            ok = False  # the preview disagrees with the finish order
        pays = wps.get(m["pgm"], [])
        # A row's payoffs fill from Show leftwards: 3 values = W/P/S, 2 = P/S, 1 = S
        # (or W/P, P when the race had no show betting).
        cols = ["win", "place", "show"] if has_show else ["win", "place"]
        paid = dict(zip(cols[len(cols) - len(pays):], pays)) if len(pays) <= len(cols) else {}
        finishers.append({"program_number": m["pgm"], "horse_name": m["name"].strip(), "finish_position": place,
                          "win_payoff": paid.get("win"), "place_payoff": paid.get("place"),
                          "show_payoff": paid.get("show"), "dead_heat": False})
        comment = (m["comment"] or "").strip()
        lines_out.append({
            "horse_name": m["name"].strip(), "program_number": m["pgm"],
            "data": {"date": race_date, "track": track, "race_number": number,
                     "distance_raw": dist[1] if dist else None,
                     "distance_yards": _distance_yards(dist[1]) if dist else None,
                     "surface": {"Dirt": "D", "Turf": "T", "Inner Turf": "T"}.get(dist[2], "A") if dist else None,
                     "condition": cond[1].strip().lower() if cond else None, "speed_fig": None,
                     "post_position": int(m["pp"]), "jockey": m["jockey"].strip(), "weight": int(m["wgt"]),
                     "call_positions": [p for p, _ in calls] if calls else None,
                     "call_lengths": [0.0 if p == 1 else b for p, b in calls] if calls else None,
                     "finish": place, "field_size": n,
                     "beaten_lengths": (0.0 if place == 1 else calls[-1][1]) if calls else None,
                     "odds": float(m["odds"]), "favorite": bool(m["fav"]),
                     "comment": comment[:120] or None, "trouble": bool(TROUBLE.search(comment)),
                     "final_time": _seconds(fractions[2]) if fractions else None, "layout": "chart"}})
    if finishers[0]["win_payoff"] is None or sum(1 for f in finishers if f["win_payoff"]) != 1:
        ok = False
    result = {"race_number": number, "finishers": finishers, "payoffs": payoffs, "scratches": scratches,
              "track_condition": cond[1].strip() if cond else None,
              "surface": lines_out[0]["data"]["surface"],
              "winning_time": _seconds(fractions[2]) if fractions else None,
              "fractions": [_seconds(x) for x in fractions[1].split()] if fractions else []}
    return {"track_id": track, "race_date": race_date, "race_number": number,
            "settled": ok, "result": result, "lines": lines_out}


def parse_chart(text: str, filename: str | None = None) -> list[dict]:
    races = []
    for title, body in split_races(text):
        track = _track_code(title["track"], filename)
        if not track:
            continue
        race = parse_race(title, body, track)
        if race:
            races.append(race)
    return races


def _card_matches(db: Database, rid: str, finishers: list[dict]) -> bool:
    runners = {r["program_number"].upper(): norm_name(r["horse_name"])
               for r in db.query("SELECT program_number, horse_name FROM runners WHERE race_id=?", [rid])}
    return bool(runners) and all(runners.get(f["program_number"]) == norm_name(f["horse_name"]) for f in finishers)


def ensure_table(db: Database) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS chart_lines (horse_key TEXT NOT NULL, race_date TEXT NOT NULL, "
               "track_id TEXT NOT NULL, race_number INTEGER NOT NULL, horse_name TEXT NOT NULL, "
               "data TEXT NOT NULL, document_id INTEGER, added_at TEXT, "
               "PRIMARY KEY (horse_key, race_date, track_id, race_number))")


def apply(db: Database, text: str, filename: str | None = None, document_id: int | None = None) -> dict:
    """Store results and per-horse chart lines from one chart document."""
    ensure_table(db)
    races = parse_chart(text, filename)
    official, linked, skipped, lines = [], [], [], 0
    for race in races:
        rid = race_id_for(race["track_id"], race["race_date"], race["race_number"])
        rows = [{"horse_key": norm_name(ln["horse_name"]), "race_date": race["race_date"],
                 "track_id": race["track_id"], "race_number": race["race_number"],
                 "horse_name": ln["horse_name"], "data": json.dumps(ln["data"]),
                 "document_id": document_id, "added_at": now_iso()} for ln in race["lines"]]
        db.upsert_many("chart_lines", rows, key=["horse_key", "race_date", "track_id", "race_number"])
        lines += len(rows)
        if not race["settled"] or not _card_matches(db, rid, race["result"]["finishers"]):
            skipped.append(rid)
            continue
        linked.append(rid)
        meet = {"track_id": race["track_id"], "race_date": race["race_date"]}
        official += store_results(db, meet, [race["result"]], "chart")
        res = race["result"]
        db.execute("UPDATE results SET winning_time=COALESCE(winning_time, ?), "
                   "fractions=CASE WHEN fractions IS NULL OR fractions IN ('', '[]') THEN ? ELSE fractions END, "
                   "track_condition=COALESCE(track_condition, ?) WHERE race_id=?",
                   [res["winning_time"], json.dumps(res["fractions"]), res["track_condition"], rid])
    return {"chart_races": len(races), "races_linked": linked, "results_added": len(official),
            "chart_lines": lines, "skipped": skipped}


def load_lines(db: Database) -> dict[str, list[dict]]:
    """horse_key -> chart lines, for the feature builder (which filters by date)."""
    ensure_table(db)
    out: dict[str, list[dict]] = {}
    for row in db.query("SELECT horse_key, data FROM chart_lines"):
        out.setdefault(row["horse_key"], []).append(json.loads(row["data"]))
    return out
