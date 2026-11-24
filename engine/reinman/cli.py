"""Rein Man command line. Every job is logged to ``job_runs``.

    reinman init                     create schema (SQLite) and register the baseline model
    reinman monitor                  ingest today's cards/changes/results, re-predict, lock, grade, stats
    reinman daily                    full daily cycle (cards for today+tomorrow, results, grading, stats)
    reinman ingest --date D          fetch cards for D
    reinman results --date D         fetch official results for D
    reinman predict --date D         analyze & record predictions for D
    reinman lock | grade | stats     individual steps
    reinman train                    train a candidate and promote only if it wins out-of-sample
    reinman backtest                 walk-forward historical backtest
    reinman backfill --start --end   build the historical database from a source
    reinman import FILE              load a canonical JSON file (licensed data)
    reinman source-check --date D    validate the live source against adapter assumptions
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import traceback
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import backtest, documents, grading, ingest, performance, predict, train
from .db import Database, SQLiteDatabase, connect
from .registry import ensure_baseline
from .sources import configured_sources, get_source
from .sources.canonical import CanonicalFile
from .sources.otb import read_result
from .util import date_range, norm_name, now_iso

log = logging.getLogger("reinman")
ET = ZoneInfo("America/New_York")


def today() -> str:
    return datetime.now(ET).date().isoformat()


def _shift(d: str, n: int) -> str:
    return (datetime.fromisoformat(d) + timedelta(days=n)).date().isoformat()


def job(db: Database, name: str, fn, *a, **k):
    jid = db.insert("job_runs", {"job": name, "started_at": now_iso()})
    try:
        out = fn(*a, **k)
        db.execute("UPDATE job_runs SET finished_at=?, status='ok', details=? WHERE id=?",
                   [now_iso(), json.dumps(out, default=str)[:4000], jid])
        log.info("%s: %s", name, out)
        return out
    except Exception as e:  # noqa: BLE001
        db.execute("UPDATE job_runs SET finished_at=?, status='error', details=? WHERE id=?",
                   [now_iso(), (traceback.format_exc())[-4000:], jid])
        log.error("%s failed: %s", name, e)
        raise


def do_ingest(db: Database, race_date: str, sources=None) -> dict:
    changed: dict[str, list[str]] = {}
    meets = 0
    for src in configured_sources() if sources is None else sources:
        for meet in src.meets(race_date):
            card = src.card(meet)
            changed.update(ingest.store_card(db, card, src.name))
            meets += 1
    weather_supplement(db, race_date)
    return {"date": race_date, "meets": meets, "changed_races": changed}


def weather_supplement(db: Database, race_date: str) -> int:
    """Fill missing temperature/precipitation from NWS for tracks with known coordinates."""
    from .sources import nws
    rows = db.query("SELECT r.race_id, r.post_time, t.lat, t.lon FROM races r JOIN tracks t ON t.track_id=r.track_id "
                    "WHERE r.race_date=? AND r.temp_f IS NULL AND t.lat IS NOT NULL AND r.post_time > ?",
                    [race_date, now_iso()])
    n = 0
    for r in rows:
        try:
            w = nws.hourly_forecast_at(r["lat"], r["lon"], r["post_time"])
        except Exception as e:  # noqa: BLE001 — weather is a supplement, never fatal
            log.warning("NWS lookup failed for %s: %s", r["race_id"], e)
            break
        if w:
            db.execute("UPDATE races SET temp_f=?, precip_pct=?, weather_desc=COALESCE(weather_desc, ?) WHERE race_id=?",
                       [w["temp_f"], w["precip_pct"], w["weather_desc"], r["race_id"]])
            n += 1
    return n


def do_results(db: Database, race_date: str, sources=None) -> dict:
    done = []
    for src in configured_sources() if sources is None else sources:
        for meet in getattr(src, "result_meets", src.meets)(race_date):
            done += ingest.store_results(db, meet, src.results(meet), src.name)
    return {"date": race_date, "official": len(done)}


def do_import(db: Database, path: str) -> dict:
    f = CanonicalFile(path)
    changed, official = 0, 0
    for card in f.all_cards():
        changed += len(ingest.store_card(db, card, f.name))
    for r in f.all_results():
        official += len(ingest.store_results(db, r["meet"], r["races"], f.name))
    return {"cards": len(f.all_cards()), "changed": changed, "official": official}


def do_import_otb(db: Database, path: str, track: str, race_date: str, race_number: int, evening: bool) -> dict:
    result = read_result(path, race_number)
    result["day_evening"] = "E" if evening else "D"
    rid = ingest.race_id_for(track, race_date, race_number, result["day_evening"])
    runners = db.query("SELECT horse_name, program_number FROM runners WHERE race_id=?", [rid])
    by_name = {norm_name(r["horse_name"]): r["program_number"] for r in runners}
    if runners and any(by_name.get(norm_name(f["horse_name"])) != f["program_number"] for f in result["finishers"]):
        raise ValueError(f"OTB horse names or programs do not match the stored card for {rid}")
    meet = {"track_id": track, "race_date": race_date}
    existing = db.one("SELECT source FROM results WHERE race_id=?", [rid])
    if existing:
        return {"race_id": rid, "official": 0, "already_official_from": existing["source"]}
    done = ingest.store_results(db, meet, [result], "otb")
    return {"race_id": rid, "official": len(done), "payouts": len(result["payoffs"])}


def automatic_sources(db: Database):
    """Reserve two credits per attempt, across both scheduled jobs and reruns."""
    def reserve(endpoint: str) -> bool:
        now = now_iso()
        cap = {"tote": 19, "results": 4}[endpoint]
        job_name = f"puntersedge_{endpoint}_request"
        return db.one(
            "INSERT INTO job_runs (job, started_at, finished_at, status) SELECT ?, ?, ?, 'reserved' "
            "WHERE (SELECT COUNT(*) FROM job_runs WHERE job=? AND started_at>=?) < ? RETURNING id",
            [job_name, now, now, job_name, now[:10], cap],
        ) is not None

    return configured_sources(reserve)


def source_check(race_date: str) -> dict:
    """Fetch one meet and report how well the adapter's assumptions hold."""
    out = {}
    for src in configured_sources():
        meets = src.meets(race_date)
        rep = {"meets": len(meets)}
        if meets:
            card = src.card(meets[0])
            runners = [rn for r in card["races"] for rn in r["runners"]]
            rep["races"] = len(card["races"])
            rep["post_time_parsed"] = sum(1 for r in card["races"] if r["post_time"]) / max(1, len(card["races"]))
            rep["distance_parsed"] = sum(1 for r in card["races"] if r["distance_yards"]) / max(1, len(card["races"]))
            rep["surface_parsed"] = sum(1 for r in card["races"] if r["surface"]) / max(1, len(card["races"]))
            rep["ml_odds_parsed"] = sum(1 for x in runners if x["ml_odds"] is not None) / max(1, len(runners))
            rep["sample_race"] = {k: v for k, v in card["races"][0].items() if k != "runners"} if card["races"] else None
            res = src.results(meets[0])
            rep["results_races"] = len(res)
            if res:
                rep["sample_result"] = res[0]
                rep["results_with_single_winner"] = sum(
                    1 for r in res if sum(1 for f in r["finishers"] if f["finish_position"] == 1) == 1) / len(res)
        out[src.name] = rep
    return out


def monitor(db: Database) -> dict:
    d = today()
    sources = automatic_sources(db)
    free = any(src.name == "puntersedge" for src in sources)
    last_board = db.scalar("SELECT MAX(started_at) FROM job_runs WHERE job='puntersedge_tote_request' "
                           "AND started_at >= ?", [now_iso()[:10]]) if free else None
    if free and last_board and datetime.fromisoformat(now_iso()) - datetime.fromisoformat(last_board) < timedelta(minutes=45):
        out = {"ingest": {"changed_races": {}, "skipped": "board recently polled"}}
    else:
        out = {"ingest": job(db, "ingest", do_ingest, db, d, sources)}
    out["results"] = ({"official": 0, "deferred_to": "daily"} if free else
                      job(db, "results", do_results, db, d, sources))
    out["grade_pre"] = job(db, "lock", predict.lock_due, db)
    out["documents"] = job(db, "documents", documents.process_pending, db)
    out["predict"] = job(db, "predict", predict.predict_date, db, d, out["ingest"]["changed_races"])
    out["grade"] = job(db, "grade", grading.grade_all, db)
    if out["results"]["official"] or out["grade"]["predictions_graded"]:
        out["stats"] = job(db, "stats", performance.refresh_stats, db)
    return out


def daily(db: Database) -> dict:
    d = today()
    out = {}
    sources = automatic_sources(db)
    for day in (_shift(d, -1), d):
        out[f"results_{day}"] = job(db, "results", do_results, db, day, sources)
    for day in (d, _shift(d, 1)):
        out[f"ingest_{day}"] = job(db, "ingest", do_ingest, db, day, sources)
    out["documents"] = job(db, "documents", documents.process_pending, db)
    for day in (d, _shift(d, 1)):
        out[f"predict_{day}"] = job(db, "predict", predict.predict_date, db, day)
    out["lock"] = job(db, "lock", predict.lock_due, db)
    out["grade"] = job(db, "grade", grading.grade_all, db)
    out["stats"] = job(db, "stats", performance.refresh_stats, db)
    return out


def ran_recently(db: Database, job_name: str, hours: float) -> bool:
    since = (datetime.fromisoformat(now_iso().replace("Z", "+00:00")) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    return bool(db.scalar("SELECT EXISTS(SELECT 1 FROM job_runs WHERE job=? AND status='ok' AND started_at>=?)",
                          [job_name, since]))


def guarded(db: Database, job_name: str, hours: float, force: bool, fn):
    """Run ``fn`` as ``job_name`` unless it already succeeded within ``hours`` (both the
    Worker's clock and GitHub's backup schedule may start the same job)."""
    if not force and ran_recently(db, job_name, hours):
        return {"skipped": f"{job_name} already ran in the last {hours:g} hours"}
    return job(db, job_name, fn)


def documents_ready(db: Database) -> dict:
    """Print how many documents are ready to scan; also expose it to GitHub Actions."""
    n = documents.ready_count(db)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"ready={n}\n")
    return {"ready": n}


def backfill(db: Database, start: str, end: str) -> dict:
    n = 0
    sources = configured_sources()
    for d in date_range(start, end):
        job(db, "backfill_cards", do_ingest, db, d, sources)
        job(db, "backfill_results", do_results, db, d, sources)
        n += 1
    return {"days": n}


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="reinman", description="Rein Man — The Intelligence Behind the Odds.")
    ap.add_argument("--db", help="sqlite:///path.db or d1 (default: $REINMAN_DB)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("init", "monitor", "lock", "grade", "stats", "backtest", "documents-ready"):
        sub.add_parser(c)
    for c in ("daily", "train"):
        sub.add_parser(c).add_argument("--force", action="store_true", help="run even if it ran recently")
    sub.add_parser("documents").add_argument("--rescan", action="store_true")
    for c in ("ingest", "results", "predict", "source-check"):
        s = sub.add_parser(c)
        s.add_argument("--date", default=None)
    s = sub.add_parser("backfill")
    s.add_argument("--start", required=True)
    s.add_argument("--end", required=True)
    s = sub.add_parser("import")
    s.add_argument("path")
    s = sub.add_parser("import-otb", help="import a saved OTB single-race results page")
    s.add_argument("path")
    s.add_argument("--track", required=True, type=str.upper, help="Rein Man track code, such as AQU")
    s.add_argument("--date", required=True, type=lambda value: date.fromisoformat(value).isoformat())
    s.add_argument("--race", required=True, type=int)
    s.add_argument("--evening", action="store_true")
    s = sub.add_parser("source")
    s.add_argument("name")
    a = ap.parse_args(argv)
    if a.cmd == "import-otb" and (a.race < 1 or not re.fullmatch(r"[A-Z0-9_]+", a.track)):
        ap.error("import-otb requires a valid track code and positive race number")
    if a.cmd == "source-check":
        print(json.dumps(source_check(a.date or today()), indent=2, default=str))
        return 0
    db = connect(a.db)
    if a.cmd == "init":
        if isinstance(db, SQLiteDatabase):
            db.migrate()
        ensure_baseline(db)
        print("Rein Man is ready.")
        return 0
    ensure_baseline(db)
    d = getattr(a, "date", None) or today()
    fn = {
        "monitor": lambda: monitor(db),
        "daily": lambda: guarded(db, "daily_cycle", 12, a.force, lambda: daily(db)),
        "documents-ready": lambda: documents_ready(db),
        "ingest": lambda: job(db, "ingest", do_ingest, db, d),
        "results": lambda: job(db, "results", do_results, db, d),
        "predict": lambda: job(db, "predict", predict.predict_date, db, d),
        "lock": lambda: job(db, "lock", predict.lock_due, db),
        "documents": lambda: job(db, "documents", documents.process_queue, db, getattr(a, "rescan", False)),
        "grade": lambda: job(db, "grade", grading.grade_all, db),
        "stats": lambda: job(db, "stats", performance.refresh_stats, db),
        "train": lambda: guarded(db, "train", 24 * 6, a.force, lambda: train.train_and_maybe_promote(db)),
        "backtest": lambda: job(db, "backtest", backtest.run, db),
        "backfill": lambda: backfill(db, a.start, a.end),
        "import": lambda: job(db, "import", do_import, db, a.path),
        "import-otb": lambda: job(db, "import_otb", do_import_otb, db, a.path, a.track, d, a.race, a.evening),
        "source": lambda: get_source(a.name).capabilities,
    }[a.cmd]
    print(json.dumps(fn(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
