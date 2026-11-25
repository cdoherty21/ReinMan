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
import subprocess
from collections import defaultdict
from datetime import date, timedelta

from .db import Database
from . import charts, historical, past_performances, programs
from .document_formats import read_document
from .document_extracts import partial_extract
from .document_tables import table_cards
from .ingest import race_id_for, store_card
from .sources.otb import _PAYOUT
from .sources.tracks import canonical_track_code
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
    ids = sorted(doc.get("program_race_ids", []))
    races = (db.query(f"SELECT * FROM races WHERE race_id IN ({','.join('?' * len(ids))})", ids)
             if ids else candidate_races(db, doc, text))
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
    from .research_bundle import cards_from_bundle
    bundle = cards_from_bundle(data)
    if bundle is not None:
        imported, linked = _store_program_cards(db, bundle)
        return {"canonical_import": True, "race_cards_imported": imported, "races_linked": sorted(linked),
                "research_bundle": True, "research_profile_version": 2, "cards": len(bundle)}
    if not isinstance(data, dict) or not ({"cards", "results"} & set(data)):
        return None
    from . import ingest
    src = data.get("source") or "upload"
    from . import quota
    changed = official = 0
    for c in data.get("cards", []):
        quota.check(db)  # resumable: a deferred file stays pending and skips what is stored
        changed += len(ingest.store_card(db, c, src))
    for r in data.get("results", []):
        quota.check(db)
        official += len(ingest.store_results(db, r["meet"], r["races"], src))
    return {"canonical_import": True, "cards": len(data.get("cards", [])), "changed_races": changed,
            "official": official}


def _original_bytes(db: Database, doc_id: int) -> bytes | None:
    chunks = []
    for start in range(0, 50, 3):
        rows = db.query(
            "SELECT hex(bytes) AS encoded FROM document_file_chunks WHERE document_id=? AND seq>=? ORDER BY seq LIMIT 3",
            [doc_id, start],
        )
        chunks.extend(bytes.fromhex(row["encoded"]) for row in rows)
        if len(rows) < 3:
            break
    return b"".join(chunks) if chunks else None


def _replace_text(db: Database, doc_id: int, text: str) -> None:
    db.execute("DELETE FROM document_chunks WHERE document_id=?", [doc_id])
    for seq, i in enumerate(range(0, len(text), 40_000)):
        db.insert("document_chunks", {"document_id": doc_id, "seq": seq, "text": text[i:i + 40_000]})
    db.execute("UPDATE documents SET chars=? WHERE id=?", [len(text), doc_id])


def _fill_card_gaps(db: Database, rid: str, race: dict) -> None:
    fields = ("post_time", "distance_yards", "distance_desc", "surface", "race_class", "purse", "track_condition")
    updates = {key: race[key] for key in fields if race.get(key) is not None}
    if updates:
        db.execute("UPDATE races SET " + ", ".join(f"{key}=COALESCE({key},?)" for key in updates)
                   + " WHERE race_id=?", [*updates.values(), rid])

    runners = {r["program_number"]: r for r in db.query(
        "SELECT program_number, horse_name, extra FROM runners WHERE race_id=?", [rid])}
    fields = ("post_position", "weight", "ml_odds", "jockey_id", "jockey_name", "trainer_id",
              "trainer_name", "sire", "dam", "dam_sire", "equipment", "medication")
    for runner in race["runners"]:
        program = runner["program_number"]
        old = runners.get(program)
        if program is None:
            matching = [r for r in runners.values() if norm_name(r["horse_name"]) == norm_name(runner["horse_name"])]
            if len(matching) == 1:
                old = matching[0]
                program = old["program_number"]
        if not old or norm_name(old["horse_name"]) != norm_name(runner["horse_name"]):
            continue
        incoming = dict(runner.get("extra") or {})
        old_extra = json.loads(old["extra"]) if old.get("extra") else {}
        live_medication = (old_extra.get("medication_report") or {}).get("source") == "PuntersEdge"
        if live_medication:
            incoming.pop("medication_report", None)
        if incoming:
            from .ingest import _merge_extra
            merged = _merge_extra(old, incoming)
            db.execute("UPDATE runners SET extra=? WHERE race_id=? AND program_number=?", [merged, rid, program])
        report = incoming.get("medication_report")
        if report is not None:
            db.execute("UPDATE runners SET medication=? WHERE race_id=? AND program_number=?",
                       [report.get("code"), rid, program])
        updates = {key: runner[key] for key in fields if runner.get(key) is not None and key != "medication"}
        if report is None and not live_medication and runner.get("medication") is not None:
            updates["medication"] = runner["medication"]
        if updates:
            db.execute("UPDATE runners SET " + ", ".join(f"{key}=COALESCE({key},?)" for key in updates)
                       + " WHERE race_id=? AND program_number=?", [*updates.values(), rid, program])


def _scan_partial(db: Database, doc_id: int, partial: tuple[str, str, dict]) -> dict:
    track, day, races = partial
    linked, missing, conflicts, notes = [], [], [], []
    matched = 0
    for number, race in sorted(races.items()):
        rid = race_id_for(track, day, number)
        by_program = {row["program_number"]: row["horse_name"] for row in db.query(
            "SELECT program_number, horse_name FROM runners WHERE race_id=?", [rid],
        )}
        if not by_program:
            missing.append(number)
            continue
        samples = race["runners"]
        verified = [r for r in samples if norm_name(by_program.get(r["program_number"])) == norm_name(r["horse_name"])]
        if samples and len(verified) != len(samples):
            conflicts.append(number)
        # Keep race-level conditions separate from a partial extract whose field contradicts the card.
        if len(verified) == len(samples):
            _fill_card_gaps(db, rid, race)
        elif verified:
            _fill_card_gaps(db, rid, {"runners": verified})
        linked.append(rid)
        matched += len(verified)
        race_fields = [f'{key.replace("_", " ")}: {race[key]}' for key in ("race_class", "distance_desc", "purse", "surface")
                       if race.get(key) is not None]
        if race.get("post_time"):
            race_fields.append(f"listed post: {race['post_time']} (UTC)")
        if race_fields and len(verified) == len(samples):
            notes.append({"race_id": rid, "program_number": None, "horse_name": None,
                          "kind": "race details", "text": " · ".join(race_fields), "data": None})
        for runner in verified:
            notes.append({"race_id": rid, "program_number": runner["program_number"], "horse_name": runner["horse_name"],
                          "kind": "runner extract", "text": runner["source_line"], "data": None})
        for horse in race["scratch_reports"]:
            if norm_name(horse) in {norm_name(name) for name in by_program.values()}:
                notes.append({"race_id": rid, "program_number": None, "horse_name": horse,
                              "kind": "scratch report", "text": f"Uploaded extract reports {horse} scratched.", "data": None})
    db.insert_many("document_notes", [dict(document_id=doc_id, **note) for note in notes])
    summary = {"races_linked": linked, "horses_matched": matched, "race_cards_imported": [],
               "partial_missing_races": missing, "partial_conflicts": conflicts}
    if not linked:
        summary["message"] = "Partial extract recognized. Upload the complete race program to link these notes."
    return summary


def _store_program_cards(db: Database, cards: list[dict]) -> tuple[list[str], set[str]]:
    imported, race_ids = [], set()
    for card in cards:
        meet, race = card["meet"], card["races"][0]
        rid = race_id_for(meet["track_id"], meet["race_date"], race["race_number"])
        # Older feeds used the full track name or a vendor code for some meets.
        if not db.scalar("SELECT EXISTS(SELECT 1 FROM races WHERE race_id=?)", [rid]):
            matches = [
                row for row in db.query(
                    "SELECT r.race_id, r.track_id, t.name FROM races r JOIN tracks t ON t.track_id=r.track_id "
                    "WHERE r.race_date=? AND r.race_number=? AND EXISTS (SELECT 1 FROM runners rn WHERE rn.race_id=r.race_id)",
                    [meet["race_date"], race["race_number"]],
                )
                if canonical_track_code(row["name"], row["track_id"]) == meet["track_id"]
                and row["race_id"] == race_id_for(row["track_id"], meet["race_date"], race["race_number"])
            ]
            if len(matches) == 1:
                rid = matches[0]["race_id"]
        if db.scalar("SELECT EXISTS(SELECT 1 FROM runners WHERE race_id=?)", [rid]):
            race_ids.add(rid)
            _fill_card_gaps(db, rid, race)
            continue
        if not card.get("complete", True):
            continue
        race_ids.add(rid)
        store_card(db, card, "uploaded_program")
        db.execute("UPDATE races SET status='official' WHERE race_id=? AND EXISTS "
                   "(SELECT 1 FROM results WHERE race_id=?)", [rid, rid])
        imported.append(rid)
    return imported, race_ids


# Documents ready to scan: new uploads, plus ones that were waiting for a race card that now exists.
_READY = ("d.status='pending' OR (d.status='processed' AND json_extract(d.summary, '$.research_bundle')=1 AND COALESCE(json_extract(d.summary, '$.research_profile_version'), 0)<2) OR (d.status='processed' "
          "AND lower(d.filename) LIKE '%keeneland%csv' "
          "AND COALESCE(json_extract(d.summary, '$.historical_scan_version'), 0)=0 "
          "AND json_extract(d.summary, '$.message') LIKE 'No race card%') "
          "OR (d.status='waiting' AND EXISTS ("
          "SELECT 1 FROM races r JOIN runners rn ON rn.race_id=r.race_id "
          "WHERE r.track_id=d.track_id AND r.race_date=d.race_date "
          "AND r.race_number=json_extract(d.summary, '$.waiting_for_race')))")


def ready_count(db: Database) -> int:
    """How many documents a scan would process right now (cheap; no PDF tools needed)."""
    return int(db.scalar(f"SELECT COUNT(*) FROM documents d WHERE {_READY}") or 0)


def process_pending(db: Database, limit: int = 20, touched: set[str] | None = None,
                    budget_started: bool = False) -> dict:
    from . import quota
    if not budget_started:
        quota.start_run(db)  # documents measure their own writes against the bulk caps
    docs = db.query(f"SELECT d.* FROM documents d WHERE {_READY} ORDER BY d.id LIMIT ?", [limit])
    done = errors = 0
    for doc in docs:
        if not quota.keep_going(db):
            return {"processed": done, "errors": errors, "deferred": "write budget; resumes next run"}
        try:
            text = document_text(db, doc["id"])
            summary = None
            if doc["filename"].lower().endswith(".json"):
                summary = _try_canonical(db, text)
            if summary is None:
                original = _original_bytes(db, doc["id"])
                text, tables = read_document(doc["filename"], original, text)
                if not text.strip():
                    raise ValueError("No readable racing text recovered. Try a clearer scan or an original program.")
                if original and doc["chars"] != len(text):
                    _replace_text(db, doc["id"], text)
                summary = historical.apply(db, tables, doc)
                if summary is not None:
                    summary["historical_scan_version"] = 1
                    db.execute("UPDATE documents SET status='processed', processed_at=?, summary=?, error=NULL WHERE id=?",
                               [now_iso(), json.dumps(summary), doc["id"]])
                    done += 1
                    continue
                if charts.is_chart(text):
                    summary = charts.apply(db, text, doc["filename"], doc["id"])
                    if not summary["races_linked"] and not summary["chart_lines"]:
                        summary["message"] = "Result chart read, but no race could be matched or stored."
                    db.execute("UPDATE documents SET status='processed', processed_at=?, summary=?, error=NULL WHERE id=?",
                               [now_iso(), json.dumps(summary), doc["id"]])
                    if touched is not None:
                        touched.update(summary["races_linked"])
                    done += 1
                    continue
                partial = partial_extract(text)
                if partial:
                    summary = _scan_partial(db, doc["id"], partial)
                    db.execute("UPDATE documents SET status='processed', processed_at=?, summary=?, error=NULL WHERE id=?",
                               [now_iso(), json.dumps(summary), doc["id"]])
                    if touched is not None:
                        touched.update(summary["races_linked"])
                    done += 1
                    continue
                cards = programs.program_cards(text) or table_cards(tables, doc)
                imported, race_ids = _store_program_cards(db, cards)
                if cards:
                    doc = dict(doc, program_race_ids=race_ids)
                card = pocket_card(text)
                if card and not race_ids and not db.scalar(
                    "SELECT EXISTS(SELECT 1 FROM races r JOIN runners rn ON rn.race_id=r.race_id "
                    "WHERE r.track_id=? AND r.race_date=? AND r.race_number=?)", card
                ):
                    track, race_date, race_number = card
                    db.execute("UPDATE documents SET status='waiting', race_date=?, track_id=?, summary=? WHERE id=?",
                               [race_date, track, json.dumps({"waiting_for_race": race_number}), doc["id"]])
                    continue
                summary = scan(db, doc, text)
                summary["race_cards_imported"] = imported
                summary["historical_scan_version"] = 1
                pp = past_performances.apply(db, text, sorted(set(summary["races_linked"]) | set(imported)), doc["id"])
                if pp["horses"]:
                    summary["past_performances"] = pp
                if not summary["races_linked"] and not imported:
                    if original is None and doc["filename"].lower().endswith(".pdf"):
                        summary["message"] = "Saved text did not match a race. Upload the original PDF for layout extraction."
                    else:
                        summary["message"] = "No race card could be identified. Include the track, date, race number, and labeled entries."
            db.execute("UPDATE documents SET status='processed', processed_at=?, summary=? WHERE id=?",
                       [now_iso(), json.dumps(summary), doc["id"]])
            if touched is not None:
                touched.update(summary.get("races_linked") or [])
                touched.update(summary.get("race_cards_imported") or [])
            done += 1
        except quota.Deferred as stop:
            # Out of today's write budget: the document stays pending and resumes next
            # run, skipping whatever it already stored.
            log.warning("document %s deferred: %s", doc["id"], stop)
            return {"processed": done, "errors": errors, "deferred": str(stop)}
        except Exception as e:  # noqa: BLE001 — one bad document must not stop the others
            log.exception("document %s failed", doc["id"])
            message = str(e)
            if isinstance(e, subprocess.TimeoutExpired):
                message = "Document extraction took too long. Try splitting the file into smaller sections."
            elif isinstance(e, subprocess.CalledProcessError):
                message = "Could not read this PDF or image. Check whether the file opens and is unlocked."
            db.execute("UPDATE documents SET status='error', processed_at=?, error=? WHERE id=?",
                       [now_iso(), message[:500], doc["id"]])
            errors += 1
    return {"processed": done, "errors": errors}

def process_queue(db: Database, rescan: bool = False) -> dict:
    if rescan:
        # Preserve already-imported canonical result files. Replaying one can
        # replace a newer card with historical values.
        predicate = """status IN ('processed', 'error', 'waiting')
                       AND COALESCE(json_extract(summary, '$.canonical_import'), 0) = 0"""
        db.execute(f"DELETE FROM document_notes WHERE document_id IN (SELECT id FROM documents WHERE {predicate})")
        db.execute(f"UPDATE documents SET status='pending', processed_at=NULL, summary=NULL, error=NULL WHERE {predicate}")
    total = {"processed": 0, "errors": 0}
    touched: set[str] = set()
    from . import quota
    quota.start_run(db)
    while True:
        batch = process_pending(db, touched=touched, budget_started=True)
        for key in total:
            total[key] += batch[key]
        if batch.get("deferred"):
            total["deferred"] = batch["deferred"]
            break
        # No progress means nothing more can be done now (also guards against looping
        # forever on a document that is pending but not ready).
        if batch["processed"] + batch["errors"] == 0:
            break
    total["waiting_for_cards"] = db.scalar("SELECT COUNT(*) FROM documents WHERE status='waiting'")
    if touched:
        from .predict import predict_date
        ids = sorted(touched)
        future = defaultdict(dict)
        for offset in range(0, len(ids), 90):
            chunk = ids[offset:offset + 90]
            for row in db.query(
                f"SELECT race_id, race_date FROM races WHERE race_id IN ({','.join('?' * len(chunk))}) "
                "AND status='scheduled' AND post_time>?", [*chunk, now_iso()],
            ):
                future[row["race_date"]][row["race_id"]] = ["document"]
        total["predictions_written"] = sum(
            predict_date(db, day, changes, only_changed=True)["snapshots_written"]
            for day, changes in sorted(future.items())
        )
    return total


