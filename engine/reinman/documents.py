"""Scan uploaded documents and keep what Rein Man can use.

Documents arrive as plain text (the browser extracts text from PDFs). For each
pending document the scanner:

1. imports it directly if it is a Rein Man canonical JSON file;
2. otherwise links it to stored races by finding their horses' names in the text
   (a race is linked when enough of its field appears);
3. keeps each matched horse's passage, so it can be read on the race page;
4. extracts facts only in formats it can read reliably:
   * workouts written in the standard "date track distance condition time grade
     rank/field" form (e.g. ``Nov15 FL 4f ft :49.20 B 12/31``);
   * payout lines such as ``$2 Exacta (3-5) Paid $45.60``;
   * scratch mentions next to a horse.

Workouts from documents are merged into the runner's
``extra.workouts``, where the feature builder reads them. Nothing is guessed: text
that matches no known format is kept as a passage only.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import date, timedelta

from .db import Database
from . import programs
from .ingest import race_id_for, store_card
from .sources.otb import _PAYOUT
from .util import norm_name, now_iso, to_float
from .wagertypes import normalize_wager_type

log = logging.getLogger("reinman.documents")

MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
_WORK = re.compile(
    rf"(?P<date>(?:{_MON}\s?\d{{1,2}}(?:,?\s?'?\d{{2,4}})?)|(?:\d{{1,2}}\s?{_MON}\s?'?\d{{0,4}}))\s+"
    r"(?P<track>[A-Z][A-Za-z]{1,3})\s+"
    r"(?P<dist>\d(?:\s?1/2)?)\s?[fF]\s+"
    r"(?P<cond>[a-z]{1,3}|[A-Z]{1,3})\s+"
    r"(?P<time>(?:1:)?:?\d{2}(?:\.\d{1,2})?|\d{2}\s?\d/\d)\s*"
    r"(?P<grade>[A-Za-z]{1,3})\s+"
    r"(?P<rank>\d{1,3})/(?P<of>\d{1,3})")
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2}|\d{2})\b")
_POCKET_CARD = re.compile(r"(?im)^\s*([A-Z0-9]{1,8})-(\d{1,2}/\d{1,2}/20\d{2})-(\d{1,2})\s*$")
_LONG_DATE = re.compile(rf"\b({_MON})\s+(\d{{1,2}}),?\s+(20\d{{2}})\b", re.I)
# Same payout grammar as the OTB reader, but the colon after "Paid" is optional in documents.
_DOC_PAYOUT = re.compile(_PAYOUT.pattern.replace(r"(?:Paid\s*)?:\s*\$", r"(?:Paid\s*)?:?\s*\$"), _PAYOUT.flags)
MIN_NAME_LEN = 5          # shorter normalized names match too much ordinary text
MAX_PASSAGE_LINES = 40
MAX_PASSAGE_CHARS = 3000


def document_text(db: Database, doc_id: int) -> str:
    rows = db.query("SELECT text FROM document_chunks WHERE document_id=? ORDER BY seq", [doc_id])
    return "".join(r["text"] for r in rows)


def dates_in(text: str) -> set[str]:
    out = set()
    for y, m, d in _ISO_DATE.findall(text):
        out.add(_safe_date(int(y), int(m), int(d)))
    for m, d, y in _US_DATE.findall(text):
        yy = int(y) + (2000 if len(y) == 2 else 0)
        out.add(_safe_date(yy, int(m), int(d)))
    for mon, d, y in _LONG_DATE.findall(text):
        out.add(_safe_date(int(y), MONTHS[mon[:3].upper()], int(d)))
    out.discard(None)
    return out


def _safe_date(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def pocket_card(text: str) -> tuple[str, str, int] | None:
    cards = set(_POCKET_CARD.findall(text))
    if len(cards) != 1:
        return None
    track, raw_date, race_number = cards.pop()
    month, day, year = map(int, raw_date.split("/"))
    race_date = _safe_date(year, month, day)
    return (track.upper(), race_date, int(race_number)) if race_date else None


def candidate_races(db: Database, doc: dict, text: str) -> list[dict]:
    card = pocket_card(text)
    if card:
        return db.query("SELECT * FROM races WHERE track_id=? AND race_date=? AND race_number=?", card)
    if doc.get("race_date"):
        days = {doc["race_date"]}
    else:
        days = dates_in(text)
        up = date.fromisoformat(doc["uploaded_at"][:10])
        days |= {(up + timedelta(days=k)).isoformat() for k in range(-7, 4)}
    track = doc.get("track_id")
    days = sorted(days)
    races = []
    step = db.max_params - 1
    for i in range(0, len(days), step):
        params = days[i:i + step]
        query = f"SELECT * FROM races WHERE race_date IN ({','.join('?' * len(params))})"
        if track:
            query += " AND track_id=?"
            params.append(track.upper())
        races.extend(db.query(query, params))
    return races


def _work_date(raw: str, race_date: str) -> str | None:
    """Resolve a workout date (often without a year) to the latest date not after the race."""
    s = raw.replace(",", " ").replace("'", " ").replace(".", " ")
    m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", s, re.I)
    mon = MONTHS.get(m.group(1)[:3].upper()) if m else None
    nums = [int(x) for x in re.findall(r"\d+", s)]
    if not mon or not nums:
        return None
    day = nums[0]
    rd = date.fromisoformat(race_date)
    if len(nums) > 1:
        y = nums[1] + (2000 if nums[1] < 100 else 0)
        return _safe_date(y, mon, day)
    for y in (rd.year, rd.year - 1):
        cand = _safe_date(y, mon, day)
        if cand and cand <= race_date:
            return cand
    return None


def parse_workouts(passage: str, race_date: str) -> list[dict]:
    works = []
    for m in _WORK.finditer(passage):
        wd = _work_date(m["date"], race_date)
        rank, of = int(m["rank"]), int(m["of"])
        if not wd or not (1 <= rank <= of) or wd > race_date:
            continue
        works.append({"date": wd, "track": m["track"].upper(),
                      "distance_yards": int(round(_furlongs(m["dist"]) * 220)),
                      "time": m["time"], "grade": m["grade"], "rank": rank, "of": of,
                      "source_text": m.group(0)})
    return works


def _furlongs(s: str) -> float:
    s = s.replace(" ", "")
    return int(s[0]) + (0.5 if "1/2" in s else 0.0)


def parse_payouts(text: str) -> list[dict]:
    out, seen = [], set()
    flat = re.sub(r"\s+", " ", text)
    for m in _DOC_PAYOUT.finditer(flat):
        name = m["wager"].strip()
        base = to_float(m["dollars"]) if m["dollars"] else int(m["cents"]) / 100
        sel = re.sub(r"\s*[,–-]\s*", "-", m["selection"].upper().strip())
        key = (name, sel, base, m["payout"])
        if key in seen:
            continue
        seen.add(key)
        out.append({"wager_type": normalize_wager_type(name), "wager_name": name, "base_amount": base,
                    "winning_numbers": sel, "payoff_amount": to_float(m["payout"]), "source_text": m.group(0)})
    return out


def scan(db: Database, doc: dict, text: str) -> dict:
    lines = text.splitlines()
    norm_lines = [" " + norm_name(x) + " " for x in lines]
    races = candidate_races(db, doc, text)
    if doc.get("program_race_ids"):
        races = [race for race in races if race["race_id"] in doc["program_race_ids"]]
    by_race = defaultdict(list)
    if races:
        ids = [r["race_id"] for r in races]
        for i in range(0, len(ids), 90):
            chunk = ids[i:i + 90]
            for r in db.query(f"SELECT * FROM runners WHERE race_id IN ({','.join('?' * len(chunk))})", chunk):
                by_race[r["race_id"]].append(r)
    race_map = {r["race_id"]: r for r in races}
    # first line where each horse appears
    hits: dict[tuple[str, str], int] = {}
    linked: list[str] = []
    for rid, runners in by_race.items():
        found = {}
        for r in runners:
            name = norm_name(r["horse_name"])
            if len(name.replace(" ", "")) < MIN_NAME_LEN:
                continue
            needle = f" {name} "
            for i, ln in enumerate(norm_lines):
                if needle in ln:
                    found[r["program_number"]] = i
                    break
        need = max(2, int(0.4 * len(runners) + 0.999))
        if len(found) >= need:
            linked.append(rid)
            for prog, i in found.items():
                hits[(rid, prog)] = i
    starts = sorted(set(hits.values()))
    notes, workouts_applied, horses = [], 0, 0
    for (rid, prog), i in sorted(hits.items(), key=lambda kv: kv[1]):
        later = [s for s in starts if s > i]
        end = min(later[0] if later else len(lines), i + MAX_PASSAGE_LINES)
        passage = "\n".join(lines[i:end]).strip()[:MAX_PASSAGE_CHARS]
        runner = next(r for r in by_race[rid] if r["program_number"] == prog)
        horses += 1
        notes.append({"race_id": rid, "program_number": prog, "horse_name": runner["horse_name"],
                      "kind": "passage", "text": passage, "data": None})
        if re.search(r"\bSCR(ATCH(ED)?)?\b", passage.upper()):
            notes.append({"race_id": rid, "program_number": prog, "horse_name": runner["horse_name"],
                          "kind": "scratch", "text": "Document mentions a scratch for this horse", "data": None})
        works = parse_workouts(passage, race_map[rid]["race_date"])
        for w in works:
            notes.append({"race_id": rid, "program_number": prog, "horse_name": runner["horse_name"],
                          "kind": "workout", "text": w["source_text"], "data": w})
        if works:
            workouts_applied += _merge_workouts(db, runner, works, doc["id"])
    for p in parse_payouts(text):
        notes.append({"race_id": linked[0] if len(linked) == 1 else None, "program_number": None,
                      "horse_name": None, "kind": "payout", "text": p["source_text"], "data": p})
    db.insert_many("document_notes", [dict(document_id=doc["id"], **n) for n in notes])
    return {"races_linked": sorted(linked), "horses_matched": horses,
            "workouts": sum(1 for n in notes if n["kind"] == "workout"),
            "workouts_applied_to_cards": workouts_applied,
            "payouts": sum(1 for n in notes if n["kind"] == "payout"),
            "scratch_mentions": sum(1 for n in notes if n["kind"] == "scratch")}


def _merge_workouts(db: Database, runner: dict, works: list[dict], doc_id: int) -> int:
    extra = json.loads(runner["extra"]) if runner.get("extra") else {}
    have = {(w.get("date"), w.get("distance_yards"), w.get("time")) for w in extra.get("workouts") or []}
    new = [{k: v for k, v in w.items() if k != "source_text"} | {"document_id": doc_id}
           for w in works if (w["date"], w["distance_yards"], w["time"]) not in have]
    if not new:
        return 0
    extra["workouts"] = (extra.get("workouts") or []) + new
    db.execute("UPDATE runners SET extra=? WHERE race_id=? AND program_number=?",
               [json.dumps(extra), runner["race_id"], runner["program_number"]])
    return len(new)


def _try_canonical(db: Database, text: str) -> dict | None:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or not ({"cards", "results"} & set(data)):
        return None
    from . import ingest
    src = data.get("source") or "upload"
    changed = sum(len(ingest.store_card(db, c, src)) for c in data.get("cards", []))
    official = sum(len(ingest.store_results(db, r["meet"], r["races"], src)) for r in data.get("results", []))
    return {"canonical_import": True, "cards": len(data.get("cards", [])), "changed_races": changed,
            "official": official}


def _program_text(db: Database, doc: dict, text: str) -> str:
    signature = db.scalar(
        "SELECT hex(substr(bytes,1,5)) FROM document_file_chunks WHERE document_id=? AND seq=0",
        [doc["id"]],
    )
    if signature != "255044462D":
        return text
    chunks = db.query("SELECT hex(bytes) AS encoded FROM document_file_chunks WHERE document_id=? ORDER BY seq",
                      [doc["id"]])
    return programs.pdf_layout(b"".join(bytes.fromhex(row["encoded"]) for row in chunks))


def _store_program_cards(db: Database, cards: list[dict]) -> tuple[list[str], set[str]]:
    imported, race_ids = [], set()
    for card in cards:
        meet, race = card["meet"], card["races"][0]
        rid = race_id_for(meet["track_id"], meet["race_date"], race["race_number"])
        race_ids.add(rid)
        if db.scalar("SELECT EXISTS(SELECT 1 FROM runners WHERE race_id=?)", [rid]):
            if race["post_time"]:
                db.execute("UPDATE races SET post_time=? WHERE race_id=? AND post_time IS NULL",
                           [race["post_time"], rid])
            continue
        store_card(db, card, "uploaded_program")
        db.execute("UPDATE races SET status='official' WHERE race_id=? AND EXISTS "
                   "(SELECT 1 FROM results WHERE race_id=?)", [rid, rid])
        imported.append(rid)
    return imported, race_ids


def process_pending(db: Database, limit: int = 20) -> dict:
    docs = db.query(
        "SELECT d.* FROM documents d WHERE d.status='pending' OR (d.status='waiting' AND EXISTS ("
        "SELECT 1 FROM races r JOIN runners rn ON rn.race_id=r.race_id "
        "WHERE r.track_id=d.track_id AND r.race_date=d.race_date "
        "AND r.race_number=json_extract(d.summary, '$.waiting_for_race'))) ORDER BY d.id LIMIT ?", [limit])
    done = errors = 0
    for doc in docs:
        try:
            text = document_text(db, doc["id"])
            summary = None
            if doc["filename"].lower().endswith(".json"):
                summary = _try_canonical(db, text)
            if summary is None:
                text = _program_text(db, doc, text)
                cards = programs.program_cards(text)
                imported, race_ids = _store_program_cards(db, cards)
                if cards:
                    meet = cards[0]["meet"]
                    doc = dict(doc, race_date=meet["race_date"], track_id=meet["track_id"], program_race_ids=race_ids)
                card = pocket_card(text)
                if card and not db.scalar(
                    "SELECT EXISTS(SELECT 1 FROM races r JOIN runners rn ON rn.race_id=r.race_id "
                    "WHERE r.track_id=? AND r.race_date=? AND r.race_number=?)", card
                ):
                    track, race_date, race_number = card
                    db.execute("UPDATE documents SET status='waiting', race_date=?, track_id=?, summary=? WHERE id=?",
                               [race_date, track, json.dumps({"waiting_for_race": race_number}), doc["id"]])
                    continue
                summary = scan(db, doc, text)
                summary["race_cards_imported"] = imported
            db.execute("UPDATE documents SET status='processed', processed_at=?, summary=? WHERE id=?",
                       [now_iso(), json.dumps(summary), doc["id"]])
            done += 1
        except Exception as e:  # noqa: BLE001 — one bad document must not stop the others
            log.exception("document %s failed", doc["id"])
            db.execute("UPDATE documents SET status='error', processed_at=?, error=? WHERE id=?",
                       [now_iso(), str(e)[:500], doc["id"]])
            errors += 1
    return {"processed": done, "errors": errors}
