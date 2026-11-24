"""Performance tracking: records, ROI, drawdown, streaks, calibration, benchmarks.

Everything is computed from locked predictions and their permanent grades — no
race is excluded. Picks scratched after lock and no-contest races are reported in
their own counts rather than dropped silently.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

from .db import Database
from .grading import entry


def _odds_band(o):
    if o is None:
        return "no odds"
    for hi, name in ((1, "odds-on"), (2.5, "1-1 to 5-2"), (5, "5-2 to 5-1"), (10, "5-1 to 10-1"), (20, "10-1 to 20-1")):
        if o <= hi:
            return name
    return "20-1+"


def _dist_band(y):
    if not y:
        return "unknown"
    return "sprint (<1 mile)" if y < 1760 else "route (1 mile+)"


def _surface(s):
    return {"D": "Dirt", "T": "Turf", "S": "Synthetic"}.get(s or "", "Unknown")


def pick_metrics(rows: list[dict]) -> dict:
    graded = [r for r in rows if r["status"] == "graded"]
    n = len(graded)
    wins = sum(1 for r in graded if r["top1_finish"] == 1)
    places = sum(1 for r in graded if r["top1_finish"] and r["top1_finish"] <= 2)
    shows = sum(1 for r in graded if r["top1_finish"] and r["top1_finish"] <= 3)
    returned = sum(r["top1_win_payoff"] or 0 for r in graded)
    wagered = 2.0 * n
    ll = [r["log_loss"] for r in graded if r["log_loss"] is not None]
    br = [r["brier"] for r in graded if r["brier"] is not None]
    odds = [r["top1_odds"] for r in graded if r["top1_odds"] is not None]
    top3 = sum(1 for r in graded if r["winner_rank"] and r["winner_rank"] <= 3)
    return {
        "races": len(rows), "graded": n,
        "no_contest": sum(1 for r in rows if r["status"] == "no_contest"),
        "top1_scratched": sum(1 for r in rows if r["status"] == "top1_scratched"),
        "wins": wins, "win_pct": wins / n if n else None,
        "place_pct": places / n if n else None, "show_pct": shows / n if n else None,
        "winner_in_top3_pct": top3 / n if n else None,
        "flat_win_wagered": wagered, "flat_win_returned": round(returned, 2),
        "flat_win_net": round(returned - wagered, 2), "flat_win_roi": (returned - wagered) / wagered if wagered else None,
        "avg_odds": sum(odds) / len(odds) if odds else None,
        "log_loss": sum(ll) / len(ll) if ll else None, "brier": sum(br) / len(br) if br else None,
    }


def _drawdown_and_streak(seq: list[tuple[str, float, bool]]) -> tuple[float, int]:
    cum = peak = mdd = 0.0
    streak = best = 0
    for _, net, lost in seq:
        cum += net
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
        streak = streak + 1 if lost else 0
        best = max(best, streak)
    return round(mdd, 2), best


def wager_metrics(rows: list[dict]) -> dict:
    rows = sorted(rows, key=lambda r: (r["graded_at"], r["wager_id"]))
    wagered = sum(r["cost"] for r in rows)
    returned = sum(r["payout"] for r in rows)
    won = [r for r in rows if r["status"] == "won"]
    seq = [(r["graded_at"], r["payout"] - r["cost"], r["status"] == "lost") for r in rows]
    mdd, streak = _drawdown_and_streak(seq)
    decided = [r for r in rows if r["status"] in ("won", "lost", "partial_refund")]
    return {
        "wagers": len(rows), "won": len(won), "hit_pct": len(won) / len(decided) if decided else None,
        "wagered": round(wagered, 2), "returned": round(returned, 2), "net": round(returned - wagered, 2),
        "roi": (returned - wagered) / wagered if wagered else None,
        "refunded": sum(1 for r in rows if r["status"] in ("refunded", "unpriced")),
        "avg_est_ev": sum((r["ev"] or 0) for r in rows) / len(rows) if rows else None,
        "max_drawdown": mdd, "longest_losing_streak": streak,
    }


def _group(rows, keyf):
    g = defaultdict(list)
    for r in rows:
        g[keyf(r)].append(r)
    return g


def calibration(db: Database, lock_rows: list[dict], bins: int = 10) -> list[dict]:
    if not lock_rows:
        return []
    winners = {r["race_id"]: set(json.loads(r["details"] or "{}").get("winners", [])) for r in lock_rows}
    scratched = {r["race_id"]: set(json.loads(r["details"] or "{}").get("scratched", [])) for r in lock_rows}
    pid_to_race = {r["prediction_id"]: r["race_id"] for r in lock_rows}
    acc = [[0.0, 0.0, 0] for _ in range(bins)]
    ids = list(pid_to_race)
    for i in range(0, len(ids), 90):
        chunk = ids[i:i + 90]
        q = f"SELECT prediction_id, program_number, win_prob FROM prediction_runners WHERE prediction_id IN ({','.join('?' * len(chunk))})"
        for pr in db.query(q, chunk):
            rid = pid_to_race[pr["prediction_id"]]
            e = entry(pr["program_number"])
            if e in scratched[rid]:
                continue
            b = min(bins - 1, int(pr["win_prob"] * bins / 0.6)) if pr["win_prob"] < 0.6 else bins - 1
            acc[b][0] += pr["win_prob"]
            acc[b][1] += 1.0 if e in winners[rid] else 0.0
            acc[b][2] += 1
    out = []
    for b, (sp, sw, n) in enumerate(acc):
        if n:
            out.append({"bin": b, "predicted": sp / n, "actual": sw / n, "n": n})
    return out


def benchmarks(db: Database, lock_rows: list[dict]) -> dict:
    """Model vs morning-line favorite, public (tote) favorite and market probabilities."""
    model_w = ml_w = pub_w = pub_n = 0
    ll_model, ll_market, ll_uniform = [], [], []
    for r in lock_rows:
        if r["status"] != "graded":
            continue
        winners = set(json.loads(r["details"] or "{}").get("winners", []))
        model_w += int(r["top1_finish"] == 1)
        runners = db.query("SELECT program_number, ml_odds FROM runners WHERE race_id=? AND scratched=0", [r["race_id"]])
        mls = [x for x in runners if x["ml_odds"] is not None]
        if mls:
            fav = min(mls, key=lambda x: x["ml_odds"])
            ml_w += int(entry(fav["program_number"]) in winners)
        pr = db.query("SELECT program_number, odds_used, odds_source, market_prob, win_prob FROM prediction_runners "
                      "WHERE prediction_id=?", [r["prediction_id"]])
        live = [x for x in pr if x["odds_source"] == "live" and x["odds_used"] is not None]
        if live and len(live) == len(pr):
            pf = min(live, key=lambda x: x["odds_used"])
            pub_w += int(entry(pf["program_number"]) in winners)
            pub_n += 1
        if all(x["market_prob"] is not None for x in pr) and r["log_loss"] is not None:
            pm = sum(x["market_prob"] for x in pr if entry(x["program_number"]) in winners)
            ll_market.append(-math.log(max(pm, 1e-9)))
            ll_model.append(r["log_loss"])
            ll_uniform.append(math.log(len(pr)))
    n = sum(1 for r in lock_rows if r["status"] == "graded")
    return {
        "races": n,
        "model_top_pick_win_pct": model_w / n if n else None,
        "ml_favorite_win_pct": ml_w / n if n else None,
        "public_favorite_win_pct": pub_w / pub_n if pub_n else None, "public_favorite_races": pub_n,
        "paired_races_with_market": len(ll_model),
        "model_log_loss": sum(ll_model) / len(ll_model) if ll_model else None,
        "market_log_loss": sum(ll_market) / len(ll_market) if ll_market else None,
        "uniform_log_loss": sum(ll_uniform) / len(ll_uniform) if ll_uniform else None,
    }


def compute(db: Database, track_id: str | None = None) -> dict:
    where = "WHERE r.track_id=?" if track_id else ""
    params = [track_id] if track_id else []
    picks = db.query(f"""SELECT g.*, p.confidence, p.model_version, p.decision, r.track_id, r.race_date,
        COALESCE(r.race_class, r.race_type, 'Unknown') AS race_class, r.surface, r.distance_yards,
        COALESCE(r.track_condition,'Unknown') AS track_condition
        FROM grades g JOIN predictions p ON p.prediction_id=g.prediction_id JOIN races r ON r.race_id=g.race_id
        {where} ORDER BY r.post_time""", params)
    wagers = db.query(f"""SELECT w.wager_id, w.wager_type, w.cost, w.ev, w.est_prob, wg.status, wg.payout, wg.graded_at,
        p.confidence, p.model_version, r.track_id, r.race_date, r.surface, r.distance_yards,
        COALESCE(r.race_class, r.race_type, 'Unknown') AS race_class, COALESCE(r.track_condition,'Unknown') AS track_condition,
        (SELECT odds_used FROM prediction_runners pr WHERE pr.prediction_id=w.prediction_id
            AND pr.program_number=w.selection) AS sel_odds
        FROM wager_grades wg JOIN wagers w ON w.wager_id=wg.wager_id JOIN predictions p ON p.prediction_id=w.prediction_id
        JOIN races r ON r.race_id=w.race_id {where} ORDER BY wg.graded_at""", params)
    by = {
        "track": lambda r: r["track_id"], "race_class": lambda r: r["race_class"],
        "distance": lambda r: _dist_band(r["distance_yards"]), "surface": lambda r: _surface(r["surface"]),
        "track_condition": lambda r: r["track_condition"], "confidence": lambda r: r["confidence"],
        "model_version": lambda r: r["model_version"], "year": lambda r: r["race_date"][:4],
        "month": lambda r: r["race_date"][:7], "day": lambda r: r["race_date"],
    }
    pick_breakdowns = {k: {g: pick_metrics(v) for g, v in sorted(_group(picks, f).items())} for k, f in by.items()}
    pick_breakdowns["odds_range"] = {g: pick_metrics(v) for g, v in
                                     _group(picks, lambda r: _odds_band(r["top1_odds"])).items()}
    wager_by = dict(by)
    wager_by["wager_type"] = lambda r: r["wager_type"]
    wager_by["odds_range"] = lambda r: _odds_band(r["sel_odds"]) if r["wager_type"] == "WIN" else "n/a (exotic)"
    wager_breakdowns = {k: {g: wager_metrics(v) for g, v in sorted(_group(wagers, f).items())}
                        for k, f in wager_by.items()}
    # selection ranking: how often rank 1/2/3 wins
    graded = [p for p in picks if p["status"] == "graded"]
    ranking = {}
    for k, col in ((1, "top1_finish"), (2, "top2_finish"), (3, "top3_finish")):
        xs = [p[col] for p in graded if p[col] is not None]
        ranking[f"rank_{k}"] = {"n": len(xs), "win_pct": sum(1 for x in xs if x == 1) / len(xs) if xs else None,
                                "itm_pct": sum(1 for x in xs if x <= 3) / len(xs) if xs else None}
    # P/L series (by race date)
    series = []
    cum_w = cum_p = 0.0
    by_date_w = _group(wagers, lambda r: r["race_date"])
    by_date_p = _group(graded, lambda r: r["race_date"])
    for d in sorted(set(by_date_w) | set(by_date_p)):
        cum_w += sum(r["payout"] - r["cost"] for r in by_date_w.get(d, []))
        cum_p += sum((r["top1_win_payoff"] or 0) - 2.0 for r in by_date_p.get(d, []))
        series.append({"date": d, "wagers_net": round(cum_w, 2), "top_pick_flat_net": round(cum_p, 2)})
    out = {
        "scope": track_id or "all",
        "picks": pick_metrics(picks), "wagers": wager_metrics(wagers),
        "pick_breakdowns": pick_breakdowns, "wager_breakdowns": wager_breakdowns,
        "selection_ranking": ranking, "pl_series": series,
        "calibration": calibration(db, picks), "benchmarks": benchmarks(db, picks),
    }
    if not track_id:
        out["actual_wagers"] = actual_metrics(db)
        out["drift"] = drift(picks)
    return out


def actual_metrics(db: Database) -> dict:
    rows = db.query("SELECT * FROM actual_wagers ORDER BY created_at")
    settled = [r for r in rows if r["payout"] is not None]
    wagered = sum(r["amount"] for r in settled)
    returned = sum(r["payout"] for r in settled)
    return {"recorded": len(rows), "settled": len(settled), "wagered": round(wagered, 2),
            "returned": round(returned, 2), "net": round(returned - wagered, 2),
            "roi": (returned - wagered) / wagered if wagered else None}


def drift(picks: list[dict], window: int = 300) -> dict:
    ll = [p["log_loss"] for p in picks if p["status"] == "graded" and p["log_loss"] is not None]
    if len(ll) < 2 * window:
        return {"status": "insufficient data", "races": len(ll)}
    recent = sum(ll[-window:]) / window
    prior = sum(ll[:-window]) / (len(ll) - window)
    status = "declining" if recent > prior * 1.05 else "stable"
    return {"status": status, "recent_log_loss": recent, "prior_log_loss": prior, "window": window}


def refresh_stats(db: Database) -> dict:
    overall = compute(db)
    db.upsert_many("stats_cache", [{"key": "overall", "json": json.dumps(overall), "computed_at": _now()}], key=["key"])
    tracks = [r["track_id"] for r in db.query("SELECT DISTINCT r.track_id FROM grades g JOIN races r ON r.race_id=g.race_id")]
    for t in tracks:
        db.upsert_many("stats_cache", [{"key": f"track:{t}", "json": json.dumps(compute(db, t)), "computed_at": _now()}],
                       key=["key"])
    return {"tracks": len(tracks), "graded": overall["picks"]["graded"], "drift": overall.get("drift")}


def _now():
    from .util import now_iso
    return now_iso()
