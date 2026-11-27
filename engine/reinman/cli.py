"""Rein Man command line. Every job is logged to ``job_runs`` with the D1 rows it wrote;
every write path respects the daily write budget (``quota``, docs/D1_BUDGET.md).

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
    reinman canonicalize-people      one-time: rewrite stored jockey/trainer ids from names
    reinman d1-usage                 READ-ONLY: D1 rows written today (Cloudflare-measured and per job)
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

from . import backtest, documents, grading, ingest, news, news_events, performance, predict, quota, schedule, stables, train
from .db import Database, SQLiteDatabase, connect
from .registry import ensure_baseline
from .sources import configured_sources, get_source
from .sources.canonical import CanonicalFile
from .sources.formfav import FormFav, enrich as enrich_formfav
from .sources.otb import read_result
from .util import date_range, norm_name, now_iso

log = logging.getLogger("reinman")
ET = ZoneInfo("America/New_York")


def today() -> str:
    return datetime.now(ET).date().isoformat()


def _shift(d: str, n: int) -> str:
    return (datetime.fromisoformat(d) + timedelta(days=n)).date().isoformat()


def _log_job(db: Database, name: str, started: str, status: str, details: str, rows: int) -> None:
    row = {"job": name, "started_at": started, "finished_at": now_iso(), "status": status, "details": details}
    try:
        db.insert("job_runs", {**row, "rows_written": rows})
    except Exception as error:  # noqa: BLE001 — job_runs.rows_written arrives with migration 0101
        if "rows_written" not in str(error):
            raise
        db.insert("job_runs", row)


def job(db: Database, name: str, fn, *a, quiet: bool = False, **k):
    """Run one step and log it as a single job_runs row (status, details and the D1 rows
    it wrote). ``quiet`` steps are logged only when they wrote something or failed; the
    monitor's own summary row records the rest."""
    started, before = now_iso(), db.rows_written
    try:
        out = fn(*a, **k)
    except Exception as e:  # noqa: BLE001
        _log_job(db, name, started, "error", traceback.format_exc()[-4000:], db.rows_written - before)
        log.error("%s failed: %s", name, e)
        raise
    rows = db.rows_written - before
    if not quiet or rows:
        _log_job(db, name, started, "ok", json.dumps(out, default=str)[:4000], rows)
    log.info("%s: %s", name, out)
    return out


def deferred(what: str, s: dict) -> dict:
    return {"deferred": what, "quota_level": s["level"], "quota_pct": s["pct"], "resumes": "next run below the level"}


def stats_stale(db: Database) -> bool:
    last = db.scalar("SELECT computed_at FROM stats_cache WHERE key='overall'")
    graded = db.scalar("SELECT MAX(graded_at) FROM grades")
    return bool(graded) and (not last or graded > last)


def report_quota(s: dict) -> None:
    """Expose the level to GitHub Actions (outputs, summary, annotations) — no DB write."""
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write(f"quota_level={s['level']}\nquota_pct={s['pct']}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(f"D1 rows written today: {s['rows_written']:,} of {s['limit']:,} ({s['pct']}%), "
                     f"level **{s['level']}** — {s['source']}\n")
    if quota.RANK[s["level"]] >= quota.RANK["defer"]:
        print(f"::warning title=D1 write budget::{s['pct']}% of today's free-tier writes used "
              f"({s['level']}); non-essential writes are deferred until 00:00 UTC. Source: {s['source']}")


def do_ingest(db: Database, race_date: str, sources=None, fresh: dict | None = None) -> dict:
    """``fresh`` receives this run's full tote read for the predictions that follow."""
    changed: dict[str, list[str]] = {}
    meets = 0
    for src in configured_sources() if sources is None else sources:
        for meet in src.meets(race_date):
            card = src.card(meet)
            changed.update(ingest.store_card(db, card, src.name, fresh, far_move=quota.odds_far_move(db)))
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
    """Import a canonical file. Idempotent and resumable: stored races cost reads only,
    and a run stops cleanly at the write budget (``quota.keep_going``); run it again,
    after 00:00 UTC if need be, to continue."""
    f = CanonicalFile(path)
    quota.start_run(db)
    changed, official, cards, results = 0, 0, 0, 0
    try:
        for card in f.all_cards():
            quota.check(db)
            changed += len(ingest.store_card(db, card, f.name))
            cards += 1
        for r in f.all_results():
            quota.check(db)
            official += len(ingest.store_results(db, r["meet"], r["races"], f.name))
            results += 1
    except quota.Deferred as stop:
        return {"cards": cards, "of_cards": len(f.all_cards()), "results": results, "of_results": len(f.all_results()),
                "changed": changed, "official": official, "deferred": str(stop), "resume": "run the same import again"}
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

    return configured_sources(reserve, db)


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
    started, before = now_iso(), db.rows_written
    quota.start_run(db)
    q = quota.status(db, refresh=True)
    report_quota(q)
    sources = automatic_sources(db)
    free = any(src.name == "puntersedge" for src in sources)
    # Free board reads are aimed just before posts (schedule.board_due); other sources
    # are read on every run.
    read, why = schedule.board_due(db) if free else (True, "")
    fresh: dict = {}
    if not read:
        out = {"ingest": {"changed_races": {}, "skipped": why}}
    else:
        out = {"ingest": job(db, "ingest", do_ingest, db, d, sources, fresh)}
        if why:
            out["ingest"]["board"] = why
    # PuntersEdge results wait for the daily job (4 reads a day); OTB fills in during the day.
    live = [src for src in sources if src.name != "puntersedge"] if free else sources
    out["results"] = (job(db, "results", do_results, db, d, live, quiet=True) if live else
                      {"official": 0, "deferred_to": "daily"})
    out["grade_pre"] = job(db, "lock", predict.lock_due, db, quiet=True)
    out["documents"] = (job(db, "documents", documents.process_pending, db, quiet=True)
                        if quota.may(db, "standard") else deferred("documents", quota.status(db)))
    out["news"] = (job(db, "news", news.refresh, db, quiet=True)
                   if quota.may(db, "background") else deferred("news poll", quota.status(db)))
    out["news_events"] = (job(db, "news_events", news_events.refresh, db, d, quiet=True)
                          if quota.may(db, "standard") else deferred("news events", quota.status(db)))
    out["predict"] = job(db, "predict", predict.predict_date, db, d, out["ingest"]["changed_races"], fresh=fresh,
                         scope=quota.predict_scope(db), quiet=True)
    out["grade"] = job(db, "grade", grading.grade_all, db, quiet=True)
    if out["results"]["official"] or out["grade"]["predictions_graded"] or stats_stale(db):
        out["stats"] = (job(db, "stats", performance.refresh_stats, db, quiet=True)
                        if quota.may(db, "standard") else deferred("stats refresh", quota.status(db)))
    q = quota.status(db)
    out["quota"] = q
    _log_job(db, "monitor", started, "ok", json.dumps(out, default=str)[:4000], db.rows_written - before)
    return out


def daily(db: Database) -> dict:
    d = today()
    out = {}
    quota.start_run(db)
    q = quota.status(db, refresh=True)
    report_quota(q)
    sources = automatic_sources(db)
    for day in (_shift(d, -1), d):
        out[f"results_{day}"] = job(db, "results", do_results, db, day, sources)
    fresh: dict = {}
    for day in (d, _shift(d, 1)):
        out[f"ingest_{day}"] = job(db, "ingest", do_ingest, db, day, sources, fresh)
    out["documents"] = (job(db, "documents", documents.process_pending, db)
                        if quota.may(db, "standard") else deferred("documents", quota.status(db)))
    out["news"] = (job(db, "news", news.refresh, db)
                   if quota.may(db, "background") else deferred("news poll", quota.status(db)))
    formfav = FormFav()
    if formfav.key and quota.may(db, "standard"):
        for day in (d, _shift(d, 1)):
            out[f"formfav_{day}"] = job(db, "formfav", enrich_formfav, db, day, formfav)
    for day in (d, _shift(d, 1)):
        out[f"news_events_{day}"] = (job(db, "news_events", news_events.refresh, db, day)
                                     if quota.may(db, "standard") else deferred("news events", quota.status(db)))
    for day in (d, _shift(d, 1)):
        out[f"predict_{day}"] = job(db, "predict", predict.predict_date, db, day, fresh=fresh,
                                    scope=quota.predict_scope(db))
    out["lock"] = job(db, "lock", predict.lock_due, db)
    out["grade"] = job(db, "grade", grading.grade_all, db)
    out["stats"] = (job(db, "stats", performance.refresh_stats, db)
                    if quota.may(db, "standard") else deferred("stats refresh", quota.status(db)))
    out["stables"] = job(db, "stables", stables.refresh, db) if quota.may(db, "background") else deferred("horse enrichment", quota.status(db))
    out["quota"] = quota.status(db)
    return out


def bulk(db: Database, name: str, fn):
    """Bulk work (imports, backfills, maintenance, training) waits at pause_bulk and above."""
    refusal = quota.guard_bulk(db, name)
    if refusal:
        report_quota(refusal["quota"])
        return refusal
    return fn()


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


def inspect_documents(db: Database, doc_id: int | None, grep: str | None, max_chars: int) -> dict:
    """READ-ONLY. List uploads, or print one upload's text as the scanner sees it
    (PDF originals through pdftotext -layout). Used to build readers for real layouts."""
    if doc_id is None:
        rows = db.query("SELECT id, uploaded_at, filename, size_bytes, chars, status, track_id, race_date, "
                        "json_extract(summary,'$.races_linked') AS linked, json_extract(summary,'$.past_performances') AS past_performances "
                        "FROM documents ORDER BY id DESC LIMIT 60")
        return {"documents": rows}
    doc = db.one("SELECT id, filename, status FROM documents WHERE id=?", [doc_id])
    if not doc:
        return {"error": f"no document {doc_id}"}
    original = documents._original_bytes(db, doc_id)
    from .document_formats import read_document
    text, _ = read_document(doc["filename"], original, documents.document_text(db, doc_id))
    if grep:
        pat = re.compile(grep, re.I)
        lines = text.splitlines()
        hits = [i for i, ln in enumerate(lines) if pat.search(ln)]
        keep = sorted({j for i in hits[:40] for j in range(max(0, i - 3), min(len(lines), i + 12))})
        text = "\n".join(f"{j:05d}| {lines[j]}" for j in keep)
    print(f"===== document {doc_id}: {doc['filename']} ({len(text)} chars shown up to {max_chars}) =====")
    print(text[:max_chars])
    return {"id": doc_id, "filename": doc["filename"], "chars": len(text)}


NOT_A_RACE = "not_a_race"


def exclude_non_races(db: Database) -> dict:
    """One-off data fix: board entries stored as races that are not races on a card
    (futures pools, or anything posting more than 36 hours from its card day) are marked
    status='not_a_race' and cancelled. Nothing is deleted. Idempotent: a second run
    writes nothing."""
    rows = db.query(
        "SELECT race_id, race_date, post_time FROM races WHERE status != ? AND ("
        "UPPER(race_id) LIKE '%FUTURE%' OR UPPER(track_id) LIKE '%FUTURE%' OR "
        "(post_time IS NOT NULL AND ABS(julianday(post_time) - julianday(race_date || 'T12:00:00Z')) > 1.5))",
        [NOT_A_RACE])
    for r in rows:
        db.execute("UPDATE races SET status=?, is_cancelled=1 WHERE race_id=?", [NOT_A_RACE, r["race_id"]])
    return {"marked_not_a_race": [r["race_id"] for r in rows]}


def canonicalize_people(db: Database) -> dict:
    """One-time repair: rewrite stored jockey/trainer ids from their names with the same
    rule ingest now uses (``util.person_key``), so one person has one id everywhere."""
    out = {}
    for role, make in (("jockey", ingest.jockey_id), ("trainer", ingest.trainer_id)):
        rows = db.query(f"SELECT DISTINCT {role}_name AS name, {role}_id AS id FROM runners "
                        f"WHERE {role}_name IS NOT NULL")
        fix = sorted({r["name"]: make(r["name"]) for r in rows if make(r["name"]) != r["id"]}.items())
        step = max(1, (db.max_params - 1) // 3)
        for i in range(0, len(fix), step):
            chunk = fix[i:i + step]
            case = " ".join("WHEN ? THEN ?" for _ in chunk)
            params = [v for pair in chunk for v in pair] + [name for name, _ in chunk]
            db.execute(f"UPDATE runners SET {role}_id = CASE {role}_name {case} END "
                       f"WHERE {role}_name IN ({','.join('?' * len(chunk))})", params)
        out[f"{role}_names_updated"] = len(fix)
        out[f"{role}_ids_after"] = db.scalar(f"SELECT COUNT(DISTINCT {role}_id) FROM runners") or 0
    return out


def backfill(db: Database, start: str, end: str) -> dict:
    """Day by day; stops cleanly at the write budget and resumes where it left off
    (stored days cost reads only)."""
    n = 0
    sources = configured_sources()
    quota.start_run(db)
    for d in date_range(start, end):
        if not quota.keep_going(db):
            return {"days": n, "deferred_from": d, "quota": quota.status(db), "resume": f"--start {d} --end {end}"}
        job(db, "backfill_cards", do_ingest, db, d, sources)
        job(db, "backfill_results", do_results, db, d, sources)
        n += 1
    return {"days": n}


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="reinman", description="Rein Man — The Intelligence Behind the Odds.")
    ap.add_argument("--db", help="sqlite:///path.db or d1 (default: $REINMAN_DB)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("init", "monitor", "lock", "grade", "stats", "backtest", "documents-ready", "news",
              "canonicalize-people", "d1-usage", "stables-refresh", "free-pp-collect",
              "exclude-non-races"):
        sub.add_parser(c)
    for c in ("daily", "train"):
        sub.add_parser(c).add_argument("--force", action="store_true", help="run even if it ran recently")
    sub.add_parser("documents").add_argument("--rescan", action="store_true")
    for c in ("ingest", "results", "predict", "source-check"):
        s = sub.add_parser(c)
        s.add_argument("--date", default=None)
    s = sub.add_parser("inspect-documents", help="READ-ONLY: list uploads or print one upload's text")
    s.add_argument("--id", type=int, default=None)
    s.add_argument("--grep", default=None, help="only show lines around matches of this regex")
    s.add_argument("--max-chars", type=int, default=60000)
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
    if a.cmd == "d1-usage":  # READ-ONLY: writes nothing, not even a job_runs row
        from . import usage
        print(json.dumps(usage.report(db), indent=2, default=str))
        return 0
    if a.cmd == "init":
        if isinstance(db, SQLiteDatabase):
            db.migrate()
        ensure_baseline(db)
        print("Rein Man is ready.")
        return 0
    ensure_baseline(db)
    d = getattr(a, "date", None) or today()
    from . import free_pp
    fn = {
        "monitor": lambda: monitor(db),
        "free-pp-collect": lambda: bulk(db, "free-pp-collect", lambda: job(db, "free_pp_collect", free_pp.collect, db, quiet=True)),
        "stables-refresh": lambda: guarded(db, "stables", 20, False, lambda: stables.refresh(db)),
        "news": lambda: job(db, "news", news.refresh, db),
        "exclude-non-races": lambda: job(db, "exclude_non_races", exclude_non_races, db),
        "canonicalize-people": lambda: bulk(db, "canonicalize-people",
                                            lambda: job(db, "canonicalize_people", canonicalize_people, db)),
        "daily": lambda: guarded(db, "daily_cycle", 12, a.force, lambda: daily(db)),
        "documents-ready": lambda: documents_ready(db),
        "inspect-documents": lambda: inspect_documents(db, a.id, a.grep, a.max_chars),
        "ingest": lambda: job(db, "ingest", do_ingest, db, d),
        "results": lambda: job(db, "results", do_results, db, d),
        "predict": lambda: job(db, "predict", predict.predict_date, db, d),
        "lock": lambda: job(db, "lock", predict.lock_due, db),
        "documents": lambda: (job(db, "documents", documents.process_queue, db, getattr(a, "rescan", False))
                              if quota.may(db, "standard") else deferred("documents", quota.status(db))),
        "grade": lambda: job(db, "grade", grading.grade_all, db),
        "stats": lambda: job(db, "stats", performance.refresh_stats, db),
        "train": lambda: bulk(db, "train", lambda: guarded(db, "train", 24, a.force,
                                                           lambda: train.train_and_maybe_promote(db))),
        "backtest": lambda: bulk(db, "backtest", lambda: job(db, "backtest", backtest.run, db)),
        "backfill": lambda: bulk(db, "backfill", lambda: backfill(db, a.start, a.end)),
        "import": lambda: bulk(db, "import", lambda: job(db, "import", do_import, db, a.path)),
        "import-otb": lambda: job(db, "import_otb", do_import_otb, db, a.path, a.track, d, a.race, a.evening),
        "source": lambda: get_source(a.name).capabilities,
    }[a.cmd]
    print(json.dumps(fn(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())


