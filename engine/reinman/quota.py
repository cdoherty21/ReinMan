"""Daily D1 write budget: how much of Cloudflare's free tier is used, and what may run.

Cloudflare's free tier allows 100,000 rows written per day, account-wide, reset at
00:00 UTC; past it, every write fails until the reset. Rein Man aims for well under
50,000 in normal operation and degrades in steps as the day's total climbs:

    level      from   behaviour
    normal       0%   everything runs
    watch       50%   far-out repeat snapshots limited; all races still analyzed
    defer       65%   background work waits; repeat snapshots focus on nearer races
    essential   75%   far-from-post odds kept only on large moves; predictions far from
                      post only on a change of pick
    pause_bulk  85%   bulk imports, backfills, maintenance and training refuse to start
    emergency   90%   only results, locks, grading, near-post odds and near-post picks

Deferred work is state-based, not a queue of writes: each step re-derives what it owes
from the data the next time it runs (a stale stats cache is refreshed, a pending
document is still pending, news not yet polled is polled), so nothing is lost and the
queue itself costs nothing. After the 00:00 UTC reset the level is normal again.

Where the numbers come from (never mixed silently; ``status()['source']`` says which):

* measured, Cloudflare: GraphQL analytics, account-wide (needs CLOUDFLARE_ANALYTICS_TOKEN
  with Account Analytics: Read). Lags a few minutes, so this run's own writes are added.
* measured, engine: D1's per-statement ``rows_written`` summed in ``job_runs`` today,
  plus an allowance for the Worker's own writes. Used when analytics are unavailable.

Checking the budget writes nothing.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from .db import Database
from .usage import DAILY_LIMIT, account_usage

log = logging.getLogger(__name__)

LEVELS = (("emergency", 0.90), ("pause_bulk", 0.85), ("essential", 0.75), ("defer", 0.65),
          ("watch", 0.50), ("normal", 0.0))
RANK = {name: i for i, (name, _) in enumerate(reversed(LEVELS))}  # normal=0 … emergency=5
WORKER_ALLOWANCE = 1_500  # rows/day the Worker writes on its own (dispatch claims, logins, uploads)
TARGET = 50_000

# The level at which each kind of work stops. "critical" and "essential" never stop.
STOPS_AT = {"standard": "emergency", "background": "defer", "bulk": "pause_bulk"}

_cache: dict = {}


def _level(pct: float) -> str:
    for name, floor in LEVELS:
        if pct >= floor:
            return name
    return "normal"


def status(db: Database, refresh: bool = False) -> dict:
    """Today's usage and level. Cloudflare's figure is fetched at most every 5 minutes
    per process; this process's own writes since then are added on top."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    override = os.environ.get("REINMAN_QUOTA_USED")  # tests and drills: pretend this many rows are used
    if override:
        used, source = int(override), "override (REINMAN_QUOTA_USED)"
    else:
        c = _cache.get(id(db))
        if refresh or not c or c["db"] is not db or c["day"] != day or time.time() - c["at"] > 300:
            c = _cache[id(db)] = {"db": db, "day": day, "at": time.time(), "base": _measure(db, day),
                                  "mark": db.rows_written}
        base = c["base"]
        used = base["rows"] + max(0, db.rows_written - c["mark"])
        source = base["source"]
    pct = used / DAILY_LIMIT
    return {"date": day, "rows_written": used, "limit": DAILY_LIMIT, "pct": round(100 * pct, 1),
            "level": _level(pct), "source": source}


def _measure(db: Database, day: str) -> dict:
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    token = os.environ.get("CLOUDFLARE_ANALYTICS_TOKEN")
    if account and token:
        cf = account_usage(account, token, days=1)
        if cf.get("available"):
            return {"rows": int((cf["days"].get(day) or {}).get("rows_written", 0)),
                    "source": "measured: Cloudflare analytics (account-wide)"}
        log.warning("Cloudflare analytics unavailable: %s", cf.get("error"))
    try:
        rows = int(db.scalar("SELECT COALESCE(SUM(rows_written),0) FROM job_runs WHERE started_at>=?", [day]) or 0)
    except Exception:  # noqa: BLE001 — before migration 0101
        rows = 0
    hour = datetime.now(timezone.utc).hour + 1
    return {"rows": rows + WORKER_ALLOWANCE * hour // 24,
            "source": "measured: engine rows_written today + estimated Worker allowance"}


def may(db: Database, kind: str) -> bool:
    """Whether work of ``kind`` (critical, essential, standard, background, bulk) may write now."""
    if kind not in STOPS_AT:
        return True
    return RANK[status(db)["level"]] < RANK[STOPS_AT[kind]]


def odds_far_move(db: Database) -> float:
    """How large a far-from-post odds move must be to be stored, by level."""
    level = status(db)["level"]
    return {"watch": 0.15, "defer": 0.20, "essential": 0.25,
            "pause_bulk": 0.25, "emergency": 1e9}.get(level, 0.10)


def predict_scope(db: Database) -> dict:
    """Which races get a stored prediction snapshot at this level. Analyses still run."""
    level = status(db)["level"]
    if level == "emergency":
        return {"within_min": 30, "prob_step": 0.03}
    if level in ("essential", "pause_bulk"):
        return {"within_min": 90, "prob_step": 0.02}
    if level == "defer":
        return {"within_min": 120, "prob_step": 0.02}
    if level == "watch":
        return {"within_min": 180, "prob_step": 0.01}
    return {"within_min": None, "prob_step": 0.01}


def guard_bulk(db: Database, what: str) -> dict | None:
    """Refuse a bulk job at pause_bulk or above; returns the refusal, or None to go ahead."""
    s = status(db, refresh=True)
    if RANK[s["level"]] >= RANK["pause_bulk"]:
        return {"deferred": what, "until": "00:00 UTC", "quota": s}
    return None


# ---- bulk imports: resumable, never allowed to crowd out race-day writes -------------
BULK_RUN_CAP = 10_000   # rows one run may spend on imports before handing over to the next
SMALL_JOB = 2_000       # an ordinary upload (one program or chart) always finishes


class Deferred(Exception):
    """Raised inside an import when the budget says stop; the work resumes next run
    (every import is idempotent: what is stored is skipped, what is missing is added)."""


def start_run(db: Database) -> None:
    db._run_start = db.rows_written  # type: ignore[attr-defined]


def keep_going(db: Database) -> bool:
    """Whether a bulk import may continue in this run. Small jobs may finish until
    the pause_bulk level; large ones stop at the per-run cap or once the day reaches the
    ``defer`` level, leaving at least a third of the day's writes for live racing."""
    spent = db.rows_written - getattr(db, "_run_start", 0)
    if not may(db, "bulk"):
        return False
    if spent < SMALL_JOB:
        return True
    return spent < BULK_RUN_CAP and may(db, "background")


def check(db: Database) -> None:
    if not keep_going(db):
        raise Deferred(f"write budget: {status(db)['level']} ({status(db)['pct']}%)")
