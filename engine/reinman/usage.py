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
    for label in ("CLOUDFLARE_ANALYTICS_TOKEN", "CLOUDFLARE_API_TOKEN"):
        token = os.environ.get(label)
        if token and account:
            out["cloudflare"] = {"token": label, **account_usage(account, token)}
            if out["cloudflare"].get("available"):
                break
    return out
