"""Read past-performance lines from uploaded race programs.

Two layouts are supported, both read from ``pdftotext -layout`` text of real uploads:

* **Track program** (e.g. the Finger Lakes official program)::

    26Oct26 8 FL ft 6f :22.. :47.. 1:14.. 3g  Clm 5000nw2/L  52 57-53 4 4 5¹ 2«¬ 2«¬ 2«¬ Berrios J C 124 bL 3.57  <top 3> <comment> 6

  date, race no., track, condition, distance, fractions, restriction, class,
  optional Equibase speed figure, pace figures, post, five running positions
  (start, three calls, finish), jockey, weight, meds/equipment, odds (* = favorite),
  top three, trip comment, field size.

* **Equibase past-performance page** ("MAKE SELECTION BY PROGRAM NUMBER")::

    31Oct26 Prx 7f ft 1:25.. SOC 5000/10000  48 8 4º¡ 6¹¢ 6¶º 6¶º£ 19.10 Hernandez A  three wide, faltered

  date, track, distance, condition, time, class, Equibase speed figure, post, four
  running positions (three calls, finish), odds, jockey, trip comment.

Lengths-behind margins are printed with font symbols that can't be decoded reliably, so
they are not used. Only ASCII digits are read as positions; anything that doesn't match a
known line shape is ignored rather than guessed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from .db import Database
from .util import norm_name

_DATE = r"(?P<date>\d{2}[A-Z][a-z]{2}\d{2})"
_POS = r"\d{1,2}[^\s\d]*"            # running position followed by margin symbols
_ODDS = r"(?P<fav>\*)?(?P<odds>\d*\.\d{2})"

PROGRAM_LINE = re.compile(
    rf"^\s*{_DATE}\s+(?P<race>\d{{1,2}})\s+(?P<track>[A-Z][A-Za-z]{{1,3}})\s+(?P<cond>[a-z]{{2,4}})\s+(?P<dist>\S+)\s+"
    r"(?P<middle>.*?)"
    r"(?:(?<!\S)(?P<fig>\d{1,3})\s+)?(?<!\S)(?P<pace>\d{1,3}-\d{1,3})\s+(?P<pp>\d{1,2})\s+"
    rf"(?P<pos>(?:{_POS}\s+){{4}}{_POS})\s+"
    r"(?P<jockey>[A-Z][A-Za-z.,' -]*?)\s+(?P<weight>\d{3})\S*\s+(?P<meds>[A-Za-z]*)\s+"
    rf"{_ODDS}(?P<rest>.*)$")

POCKET_LINE = re.compile(
    rf"(?<!\S){_DATE}\s+(?P<track>[A-Z][A-Za-z]{{1,3}})\s+(?P<dist>\S+)\s+(?P<cond>[a-z]{{2,4}})\s+"
    r"(?P<time>\d{0,2}:?\d{2}\S*)\s+(?P<middle>.*?)"
    r"(?<!\S)(?P<fig>\d{1,3})\s+(?P<pp>\d{1,2})\s+"
    rf"(?P<pos>(?:{_POS}\s+){{3}}{_POS})\s+"
    rf"{_ODDS}(?:\s+(?P<rest>.*))?$")

# Distances as printed. "K" is the half-furlong symbol and "ÇÀ" the 1/16-mile symbol in
# these fonts (confirmed by final times: 5K ~ 1:05-1:07, 1ÇÀ ~ 1:44-1:46).
_DIST = {"1m": 1760, "1ÇÀ": 1870}

TROUBLE = re.compile(
    r"bump|bmp|steadi|stdy|check|took up|tkup|block|bother|bthrd|slam|slmd|stumbl|stmbl|"
    r"step slow|stp slw|brk slow|broke slow|awkward|awkwrd|wkwrd|shuffl|impeded|pinch|"
    r"tight st|lost rider|eased|\bDQ", re.I)


def _yards(dist: str) -> int | None:
    if dist in _DIST:
        return _DIST[dist]
    m = re.fullmatch(r"(\d{1,2})([fK])", dist)
    if m:
        furlongs = int(m[1]) + (0.5 if m[2] == "K" else 0.0)
        return int(round(furlongs * 220)) if 2 <= furlongs <= 12 else None
    return None


def _positions(text: str) -> list[int]:
    return [int(re.match(r"\d+", tok)[0]) for tok in text.split()]


def _date(raw: str) -> str | None:
    try:
        return datetime.strptime(raw, "%d%b%y").date().isoformat()
    except ValueError:
        return None


def parse_line(line: str) -> dict | None:
    m = PROGRAM_LINE.match(line)
    layout = "program"
    if not m:
        m = POCKET_LINE.search(line)
        layout = "pocket"
    if not m:
        return None
    day = _date(m["date"])
    pos = _positions(m["pos"])
    if not day or any(p < 1 or p > 24 for p in pos):
        return None
    fig = int(m["fig"]) if m["fig"] else None
    if fig is not None and fig > 130:
        return None
    if layout == "program":
        calls, finish = pos[1:4], pos[4]          # start, 3 calls, finish
        rest = m["rest"].strip()
        size = re.search(r"(\d{1,2})\s*$", rest)
        field_size = int(size[1]) if size else None
        rest = re.sub(r"\s*\d{1,2}\s*$", "", rest)
        # "<top three, each ending in its weight + margin symbols> <comment>"
        tail = re.match(r".*\d{3}[^\s\d]*\s+(.*)$", rest)
        comment = (tail[1] if tail else rest).strip()
    else:
        calls, finish = pos[0:3], pos[3]
        field_size = None
        rest = (m["rest"] or "").strip()
        if re.search(r"\S {2,}\S", rest):
            comment = re.split(r" {2,}", rest)[-1]          # "Jockey A      comment"
        else:                                                # "Jockey A comment": drop the name
            comment = re.sub(r"^[A-Z][a-z]+(?:,? [A-Z][a-z.]*)*\s+(?:[A-Z]\s+)*", "", rest)
        comment = comment.strip()
    if field_size is not None and (field_size < finish or field_size > 24):
        field_size = None
    return {
        "date": day, "track": m["track"].upper(),
        "race_number": int(m["race"]) if layout == "program" else None,
        "distance_raw": m["dist"], "distance_yards": _yards(m["dist"]),
        "condition": m["cond"], "speed_fig": fig, "post_position": int(m["pp"]),
        "call_positions": calls + [finish], "finish": finish, "field_size": field_size,
        "odds": float(m["odds"]), "favorite": bool(m["fav"]),
        "comment": comment[:120] or None,
        "trouble": bool(TROUBLE.search(comment or "")),
        "layout": layout,
    }


_OWNER = re.compile(r"^\s*Owner:", re.M)
_RECORD = re.compile(r" {2,}[A-Z][A-Z0-9' ().&/-]+? {2,}20\d{2}: *\d+ +\d+ +\d+ +\d+")


def horse_blocks(text: str) -> list[str]:
    """Split a program into one block per horse, anchored on layout markers (never on a
    horse's name, which also appears in other horses' lines as a top finisher)."""
    lines = text.splitlines()
    if _OWNER.search(text):
        starts = [i for i, ln in enumerate(lines) if _OWNER.match(ln)]
    else:
        starts = [i for i, ln in enumerate(lines) if _RECORD.search(ln)]
    return ["\n".join(lines[s:e]) for s, e in zip(starts, starts[1:] + [len(lines)])]


def _header(block: str) -> str:
    """The part of a block before its first past-performance line (where its own name is)."""
    out = []
    for i, ln in enumerate(block.splitlines()):
        if i > 0 and (PROGRAM_LINE.match(ln) or POCKET_LINE.search(ln)):
            break
        out.append(ln)
        if len(out) >= 10:
            break
    return "\n".join(out)


def extract(text: str, runners: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Map (race_id, program_number) -> past performances, for runners whose own block
    is found unambiguously."""
    by_name: dict[str, list[dict]] = {}
    for r in runners:
        name = norm_name(r["horse_name"])
        if len(name.replace(" ", "")) >= 4:
            by_name.setdefault(name, []).append(r)
    found: dict[tuple[str, str], list[dict]] = {}
    for block in horse_blocks(text):
        header = " " + norm_name(_header(block)) + " "
        owners = [rs for name, rs in by_name.items() if f" {name} " in header]
        if len(owners) != 1 or len(owners[0]) != 1:
            continue                     # no match, or ambiguous: skip rather than guess
        runner = owners[0][0]
        lines = [parse_line(ln) for ln in block.splitlines()]
        pps = [p for p in lines if p and p["date"] < runner["race_date"]]
        if pps:
            key = (runner["race_id"], runner["program_number"])
            found.setdefault(key, []).extend(pps)
    return found


def apply(db: Database, text: str, race_ids: list[str], document_id: int | None = None) -> dict:
    """Attach past performances from ``text`` to the runners of ``race_ids``."""
    if not race_ids:
        return {"horses": 0, "lines": 0}
    rows = []
    for i in range(0, len(race_ids), 90):
        chunk = race_ids[i:i + 90]
        rows += db.query(
            "SELECT rn.race_id, rn.program_number, rn.horse_name, rn.extra, r.race_date FROM runners rn "
            f"JOIN races r ON r.race_id=rn.race_id WHERE rn.race_id IN ({','.join('?' * len(chunk))})", chunk)
    found = extract(text, rows)
    lines = 0
    for (rid, prog), pps in found.items():
        row = next(r for r in rows if r["race_id"] == rid and r["program_number"] == prog)
        extra = json.loads(row["extra"]) if row.get("extra") else {}
        have = {(p.get("date"), p.get("track"), p.get("race_number")) for p in extra.get("past_performances") or []}
        new = [dict(p, document_id=document_id) for p in pps
               if (p["date"], p["track"], p["race_number"]) not in have]
        if not new:
            continue
        merged = sorted((extra.get("past_performances") or []) + new, key=lambda p: p["date"], reverse=True)
        extra["past_performances"] = merged
        db.execute("UPDATE runners SET extra=? WHERE race_id=? AND program_number=?",
                   [json.dumps(extra), rid, prog])
        lines += len(new)
    return {"horses": len(found), "lines": lines}

