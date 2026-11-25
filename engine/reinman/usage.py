"""D1 write accounting: what Cloudflare reports, and what Rein Man itself wrote.

Two kinds of numbers, never mixed:

* **measured (Cloudflare)** – ``account_usage`` asks Cloudflare's GraphQL analytics for
  rows written per database per UTC day. This is the number the free-tier limit is
  enforced on (100,000 rows written per day, account-wide, reset 00:00 UTC). It needs a
  token with *Account Analytics: Read*; without one the result says so.
* **measured (this engine)** – every D1 response carries ``meta.rows_written`` for the
  statement just run (index writes included). ``D1Database`` adds these up per job, and
  ``job_runs.rows_written`` keeps the total for each job run, so the engine always knows
  what it has written today without an extra write of its own.
* **estimated** – anything derived from table counts (``table_activity``) is labelled as
  an estimate.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timedelta, timezone

from .db import Database

log = logging.getLogger(__name__)

DAILY_LIMIT = 100_000          # Cloudflare D1 free tier, rows written per day, account-wide
GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"


def _post(url: str, token: str, body: dict | None = None, method: str = "POST") -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def account_usage(account_id: str, token: str, days: int = 3) -> dict:
    """Rows written/read per database per UTC day, as Cloudflare reports them."""
    start = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    query = """query($a: String!, $s: Date!, $e: Date!) { viewer { accounts(filter: {accountTag: $a}) {
      d1AnalyticsAdaptiveGroups(limit: 1000, filter: {date_geq: $s, date_leq: $e}) {
        sum { rowsWritten rowsRead writeQueries readQueries } dimensions { date databaseId } } } } }"""
    try:
        out = _post(GRAPHQL, token, {"query": query, "variables": {"a": account_id, "s": start, "e": end}})
    except Exception as error:  # noqa: BLE001
        return {"available": False, "error": str(error)[:300]}
    if out.get("errors"):
        return {"available": False, "error": json.dumps(out["errors"])[:500]}
    groups = out["data"]["viewer"]["accounts"][0]["d1AnalyticsAdaptiveGroups"]
    names = {}
    try:
        listing = _post(f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database?per_page=100",
                        token, method="GET")
        names = {d["uuid"]: d["name"] for d in listing.get("result") or []}
    except Exception:  # noqa: BLE001 — names are a nicety
        pass
    by_day: dict[str, dict] = {}
    for g in groups:
        day = by_day.setdefault(g["dimensions"]["date"], {"rows_written": 0, "databases": {}})
        db = names.get(g["dimensions"]["databaseId"], g["dimensions"]["databaseId"])
        day["rows_written"] += g["sum"]["rowsWritten"]
        entry = day["databases"].setdefault(db, {"rows_written": 0, "rows_read": 0, "write_queries": 0})
        entry["rows_written"] += g["sum"]["rowsWritten"]
        entry["rows_read"] += g["sum"]["rowsRead"]
        entry["write_queries"] += g["sum"]["writeQueries"]
    for day in by_day.values():
        day["pct_of_free_tier"] = round(100 * day["rows_written"] / DAILY_LIMIT, 1)
    return {"available": True, "source": "Cloudflare GraphQL analytics (measured)", "days": by_day}


def engine_writes_today(db: Database) -> dict:
    """Rows this engine wrote today (UTC), from D1's own per-statement counts."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        rows = db.query("SELECT job, COUNT(*) AS runs, SUM(rows_written) AS rows_written FROM job_runs "
                        "WHERE started_at>=? GROUP BY job ORDER BY rows_written DESC", [day])
    except Exception as error:  # noqa: BLE001 — column missing before migration 0101
        return {"available": False, "error": str(error)[:200]}
    total = sum(r["rows_written"] or 0 for r in rows)
    return {"available": True, "source": "D1 meta.rows_written per statement (measured)", "date": day,
            "rows_written": total, "by_job": rows}


ACTIVITY = (
    ("odds_snapshots", "captured_at"), ("predictions", "created_at"), ("job_runs", "started_at"),
    ("runners", "updated_at"), ("races", "updated_at"), ("racing_news", "first_seen_at"),
    ("race_changes", "seen_at"), ("model_events", "at"), ("wagers", "created_at"),
)


def table_activity(db: Database) -> dict:
    """ESTIMATE: rows touched today per table (inserted or rewritten), from timestamps."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = {}
    for table, col in ACTIVITY:
        try:
            out[table] = {"total": db.scalar(f"SELECT COUNT(*) FROM {table}"),
                          "touched_today": db.scalar(f"SELECT COUNT(*) FROM {table} WHERE {col}>=?", [day])}
        except Exception as error:  # noqa: BLE001
            out[table] = {"error": str(error)[:120]}
    try:
        out["prediction_runners"] = {"touched_today": db.scalar(
            "SELECT COUNT(*) FROM prediction_runners pr JOIN predictions p ON p.prediction_id=pr.prediction_id "
            "WHERE p.created_at>=?", [day])}
    except Exception as error:  # noqa: BLE001
        out["prediction_runners"] = {"error": str(error)[:120]}
    return {"source": "table timestamps (estimate; excludes index writes)", "date": day, "tables": out}


def report(db: Database) -> dict:
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    out = {"engine": engine_writes_today(db), "tables": table_activity(db), "cloudflare": {}}
    try:
        out["performance"] = performance_check(db)
    except Exception as error:  # noqa: BLE001 — diagnostics never fail the report
        out["performance"] = {"error": str(error)[:300]}
    for label in ("CLOUDFLARE_ANALYTICS_TOKEN", "CLOUDFLARE_API_TOKEN"):
        token = os.environ.get(label)
        if token and account:
            out["cloudflare"] = {"token": label, **account_usage(account, token)}
            if out["cloudflare"].get("available"):
                break
    return out


def performance_check(db: Database, days: int = 7) -> dict:
    """READ-ONLY: is the Performance page keeping up? Per race date: races, how many have
    run, official results (by source), locked picks, grades; plus when the stats cache was
    last computed and the latest grading/results job runs."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    per_day = db.query(
        "SELECT r.race_date, COUNT(*) AS races, "
        "SUM(CASE WHEN r.post_time <= ? THEN 1 ELSE 0 END) AS run, "
        "SUM(CASE WHEN r.status='official' THEN 1 ELSE 0 END) AS official, "
        "SUM(CASE WHEN l.race_id IS NOT NULL THEN 1 ELSE 0 END) AS locked, "
        "SUM(CASE WHEN g.race_id IS NOT NULL THEN 1 ELSE 0 END) AS graded, "
        "SUM(CASE WHEN l.race_id IS NOT NULL AND g.race_id IS NULL AND r.status='official' THEN 1 ELSE 0 END) "
        "  AS locked_official_ungraded, "
        "SUM(CASE WHEN r.post_time <= ? AND l.race_id IS NULL THEN 1 ELSE 0 END) AS run_without_lock "
        "FROM races r LEFT JOIN locks l ON l.race_id=r.race_id LEFT JOIN grades g ON g.race_id=r.race_id "
        "WHERE r.race_date>=? GROUP BY r.race_date ORDER BY r.race_date", [now, now, since])
    sources = db.query("SELECT r.race_date, s.source, COUNT(*) AS n FROM results s JOIN races r ON r.race_id=s.race_id "
                       "WHERE r.race_date>=? GROUP BY r.race_date, s.source ORDER BY r.race_date", [since])
    grades = db.query("SELECT status, COUNT(*) AS n, MAX(graded_at) AS last FROM grades GROUP BY status")
    cache = db.query("SELECT key, computed_at, json_extract(json,'$.picks.graded') AS graded, "
                     "json_extract(json,'$.wagers.wagers') AS wagers FROM stats_cache ORDER BY key LIMIT 40")
    wagers = db.query("SELECT COUNT(*) AS wagers, SUM(CASE WHEN wg.wager_id IS NOT NULL THEN 1 ELSE 0 END) AS graded "
                      "FROM wagers w JOIN locks l ON l.prediction_id=w.prediction_id "
                      "LEFT JOIN wager_grades wg ON wg.wager_id=w.wager_id")
    jobs = db.query("SELECT job, started_at, status, substr(details,1,300) AS details FROM job_runs "
                    "WHERE job IN ('results','grade','stats','daily_cycle','monitor','puntersedge_results_request',"
                    "'otb_page_request') ORDER BY id DESC LIMIT 30")
    return {"per_day": per_day, "result_sources": sources, "grades": grades, "stats_cache": cache,
            "locked_wagers": wagers, "recent_jobs": jobs}
