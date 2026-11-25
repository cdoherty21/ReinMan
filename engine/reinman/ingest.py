"""Ingestion: store canonical cards/results, detect material race changes."""
from __future__ import annotations

import json
import logging

from .db import Database
from .sources.tracks import track_info
from .util import norm_name, now_iso

log = logging.getLogger("reinman.ingest")

ODDS_MOVE = 0.35  # relative change in implied win probability that triggers an update


def race_id_for(track_id: str, race_date: str, race_number: int, day_evening: str = "D") -> str:
    return f"{track_id.upper()}-{race_date.replace('-', '')}-{int(race_number)}" + ("E" if day_evening == "E" else "")


def upsert_track(db: Database, meet: dict) -> None:
    info = track_info(meet["track_id"])
    db.upsert_many("tracks", [{
        "track_id": meet["track_id"], "name": info.get("name") or meet.get("track_name") or meet["track_id"],
        "country": meet.get("country") or "USA", "category": meet.get("category") or "thoroughbred",
        "timezone": meet.get("timezone") or info.get("tz"), "lat": info.get("lat"), "lon": info.get("lon"),
        "updated_at": now_iso()}], key=["track_id"])


def _merge_extra(previous: dict | None, incoming: dict | None) -> str | None:
    old = json.loads(previous["extra"]) if previous and previous.get("extra") else {}
    new = incoming or {}
    merged = {**old, **{key: value for key, value in new.items() if value is not None}}
    for key in ("past_performances", "workouts"):
        if old.get(key) and new.get(key):
            records = {}
            for record in old[key] + new[key]:
                identity = (record.get("date"), record.get("track"), record.get("race_number"))
                if key == "workouts":
                    identity += (record.get("distance_yards"), record.get("time"))
                records[identity] = {**records.get(identity, {}),
                                     **{k: v for k, v in record.items() if v is not None}}
            merged[key] = list(records.values())
        elif old.get(key) and not new.get(key):
            merged[key] = old[key]
    return json.dumps(merged) if merged else None


def store_card(db: Database, card: dict, source: str | None = None) -> dict[str, list[str]]:
    """Insert/refresh a meet's races and runners.

    Returns {race_id: [reasons]} for races whose handicapping inputs changed in a way
    that warrants a fresh prediction (new card, scratch, jockey change, odds move,
    track condition or surface change).
    """
    meet = card["meet"]
    source = source or meet.get("source") or "unknown"
    upsert_track(db, meet)
    db.upsert_many("meets", [{"meet_id": meet["meet_id"], "track_id": meet["track_id"],
                              "race_date": meet["race_date"], "source": source,
                              "fetched_at": now_iso()}], key=["meet_id"])
    changed: dict[str, list[str]] = {}
    for r in card["races"]:
        rid = race_id_for(meet["track_id"], meet["race_date"], r["race_number"], r.get("day_evening", "D"))
        prev_race = db.one("SELECT * FROM races WHERE race_id=?", [rid])
        prev_runners = {x["program_number"]: x for x in db.query("SELECT * FROM runners WHERE race_id=?", [rid])}
        reasons: list[str] = []
        if prev_race is None:
            reasons.append("initial")
        else:
            if (r.get("track_condition") or "") != (prev_race.get("track_condition") or "") and r.get("track_condition"):
                reasons.append("condition")
            if r.get("surface") and prev_race.get("surface") and r["surface"] != prev_race["surface"]:
                reasons.append("surface")
        status = "cancelled" if r.get("is_cancelled") else (prev_race or {}).get("status", "scheduled")
        db.upsert_many("races", [{
            "race_id": rid, "meet_id": meet["meet_id"], "track_id": meet["track_id"],
            "race_date": meet["race_date"], "race_number": r["race_number"], "post_time": r.get("post_time"),
            "surface": r.get("surface"), "distance_yards": r.get("distance_yards"),
            "distance_desc": r.get("distance_desc"), "race_type": r.get("race_type"),
            "race_class": r.get("race_class"), "race_name": r.get("race_name"), "grade": r.get("grade"),
            "purse": r.get("purse"), "min_claim": r.get("min_claim"), "max_claim": r.get("max_claim"),
            "age_restriction": r.get("age_restriction"), "sex_restriction": r.get("sex_restriction"),
            "breed": r.get("breed"), "pools": r.get("pools") or None, "track_condition": r.get("track_condition"),
            "weather_desc": r.get("weather_desc"), "temp_f": r.get("temp_f"), "precip_pct": r.get("precip_pct"),
            "is_cancelled": bool(r.get("is_cancelled")), "status": status, "source": source,
            "updated_at": now_iso()}], key=["race_id"])
        rows, snaps = [], []
        for rn in r["runners"]:
            p = prev_runners.get(rn["program_number"])
            if p is not None:
                if rn.get("scratched") and not p["scratched"]:
                    reasons.append("scratch")
                if rn.get("jockey_id") and p.get("jockey_id") and rn["jockey_id"] != p["jockey_id"]:
                    reasons.append("jockey")
                lo, po = rn.get("live_odds"), p.get("live_odds")
                if lo is not None and po is not None:
                    a, b = 1 / (lo + 1), 1 / (po + 1)
                    if abs(a - b) / max(b, 1e-6) > ODDS_MOVE:
                        reasons.append("odds")
                elif lo is not None and po is None and prev_race is not None:
                    reasons.append("odds")
            rows.append({
                "race_id": rid, "program_number": rn["program_number"], "post_position": rn.get("post_position"),
                "horse_id": rn["horse_id"], "horse_name": rn["horse_name"], "jockey_id": rn.get("jockey_id"),
                "jockey_name": rn.get("jockey_name"), "trainer_id": rn.get("trainer_id"),
                "trainer_name": rn.get("trainer_name"), "sire": rn.get("sire"), "dam": rn.get("dam"),
                "dam_sire": rn.get("dam_sire"), "weight": rn.get("weight"), "equipment": rn.get("equipment"),
                "medication": rn.get("medication"), "claiming": rn.get("claiming"), "ml_odds": rn.get("ml_odds"),
                "live_odds": rn.get("live_odds"), "coupled_type": rn.get("coupled_type"),
                "scratched": bool(rn.get("scratched")) or bool(p and p["scratched"]),
                "extra": _merge_extra(p if p and p["horse_id"] == rn["horse_id"] else None, rn.get("extra")),
                "updated_at": now_iso()})
            if p and p["horse_id"] == rn["horse_id"]:
                for key in ("jockey_id", "jockey_name", "trainer_id", "trainer_name", "sire", "dam",
                            "dam_sire", "weight", "equipment", "medication", "claiming", "ml_odds", "post_position"):
                    if rows[-1][key] is None:
                        rows[-1][key] = p.get(key)
            if rn.get("live_odds") is not None or rn.get("win_pool") is not None:
                snaps.append({"race_id": rid, "program_number": rn["program_number"], "captured_at": now_iso(),
                              "live_odds": rn.get("live_odds"), "win_pool": rn.get("win_pool"),
                              "place_pool": rn.get("place_pool"), "show_pool": rn.get("show_pool")})
        db.upsert_many("runners", rows, key=["race_id", "program_number"])
        db.insert_many("odds_snapshots", snaps)
        for c in r.get("changes") or []:
            db.insert_many("race_changes", [{"race_id": rid, "change_type": c.get("type"), "text": c["text"]}],
                           verb="INSERT OR IGNORE")
        if reasons:
            changed[rid] = sorted(set(reasons))
    return changed


def store_results(db: Database, meet: dict, results: list[dict], source: str) -> list[str]:
    """Store official results; returns race_ids that became official."""
    done = []
    for res in results:
        rid = race_id_for(meet["track_id"], meet["race_date"], res["race_number"], res.get("day_evening", "D"))
        if db.one("SELECT race_id FROM results WHERE race_id=?", [rid]):
            continue
        if not res.get("finishers"):
            continue
        runners = db.query("SELECT program_number, horse_name FROM runners WHERE race_id=?", [rid])
        by_name = {norm_name(r["horse_name"]): r["program_number"] for r in runners}
        fin_rows = []
        for f in res["finishers"]:
            prog = f.get("program_number") or by_name.get(norm_name(f["horse_name"]))
            fin_rows.append({"race_id": rid, "program_number": prog, "horse_name": f["horse_name"],
                             "finish_position": f["finish_position"], "win_payoff": f.get("win_payoff"),
                             "place_payoff": f.get("place_payoff"), "show_payoff": f.get("show_payoff"),
                             "dead_heat": bool(f.get("dead_heat"))})
        if not db.one("SELECT race_id FROM races WHERE race_id=?", [rid]):
            log.warning("result for unknown race %s (no card stored) — storing result only", rid)
        db.insert_many("result_finishers", fin_rows)
        db.insert_many("result_payoffs", [dict(race_id=rid, **p) for p in res.get("payoffs") or []])
        db.insert_many("result_scratches", [{"race_id": rid, "horse_name": s} for s in res.get("scratches") or []])
        for s in res.get("scratches") or []:
            prog = by_name.get(norm_name(s))
            if prog:
                db.execute("UPDATE runners SET scratched=1 WHERE race_id=? AND program_number=?", [rid, prog])
        db.insert("results", {"race_id": rid, "winning_time": res.get("winning_time"),
                              "fractions": res.get("fractions") or [], "track_condition": res.get("track_condition"),
                              "surface": res.get("surface"), "source": source, "official_at": now_iso()})
        db.execute("UPDATE races SET status='official' WHERE race_id=?", [rid])
        if res.get("track_condition"):
            db.execute("UPDATE races SET track_condition=COALESCE(track_condition, ?) WHERE race_id=?",
                       [res["track_condition"], rid])
        done.append(rid)
    return done
