"""Daily race analysis: produce and record Rein Man's predictions."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import timedelta

import numpy as np

from .config import POLICY
from . import news, news_events
from .db import Database
from .features import build_history, data_availability, featurize_race, projected_pace
from .models import Ensemble
from .probability import DEFAULT_LAMBDAS, summarize
from .registry import champion
from .selections import fair_odds, runner_factors, select
from .util import now_iso, parse_iso, stable_hash, to_iso, us_date
from .wagers import multi_race_ticket, race_wagers, win_watch
from .wagertypes import LEGS

log = logging.getLogger("reinman.predict")


def _latest_pools(db: Database, race_id: str, progs: list[str], since: str = "") -> dict | None:
    rows = db.query("SELECT program_number, win_pool, place_pool, show_pool, captured_at FROM odds_snapshots "
                    "WHERE race_id=? AND captured_at>=? ORDER BY captured_at DESC", [race_id, since])
    latest = {}
    for r in rows:
        latest.setdefault(r["program_number"], r)
    if not latest or any(p not in latest for p in progs):
        return None
    out = {}
    for key in ("win", "place", "show"):
        arr = np.array([latest[p][f"{key}_pool"] if latest[p][f"{key}_pool"] is not None else np.nan
                        for p in progs], dtype=float)
        if np.all(np.isfinite(arr)):
            out[key] = arr
    return out or None


def _fresh_pools(read: dict | None, progs: list[str]) -> dict | None:
    pools = (read or {}).get("pools") or {}
    if not pools or any(p not in pools for p in progs):
        return None
    out = {}
    for key in ("win", "place", "show"):
        arr = np.array([pools[p][key] if pools[p].get(key) is not None else np.nan for p in progs], dtype=float)
        if np.all(np.isfinite(arr)):
            out[key] = arr
    return out or None


def analyze_race(db: Database, h, model_version: str, model, artifact: dict, race: dict,
                 runners: list[dict], fresh: dict | None = None) -> dict | None:
    """``fresh`` is this run's tote read ({race_id: {"read_at", "odds", "pools"}}, from
    ``ingest.store_card``). Live odds count only when read within 5 minutes; the read is
    used whole even where a small move was not stored."""
    live = [r for r in runners if not r["scratched"]]
    if len(live) < 2:
        return None
    since = ""
    read = (fresh or {}).get(race["race_id"])
    if race.get("source") == "puntersedge":
        since = to_iso(parse_iso(now_iso()) - timedelta(minutes=5))
        if read and read["read_at"] >= since:
            live = [dict(r, live_odds=read["odds"].get(r["program_number"])) for r in live]
        else:
            live = [dict(r, live_odds=None) for r in live]
    X, evidence = featurize_race(h, race, runners)
    progs = [r["program_number"] for r in live]
    names = [r["horse_name"] for r in live]
    live_odds = np.array([r["live_odds"] if r["live_odds"] is not None else np.nan for r in live], float)
    ml = np.array([r["ml_odds"] if r["ml_odds"] is not None else np.nan for r in live], float)
    odds = np.where(np.isfinite(live_odds), live_odds, ml)
    odds_src = ["live" if np.isfinite(live_odds[i]) else "morning_line" if np.isfinite(ml[i]) else None
                for i in range(len(live))]
    market = live_odds if np.all(np.isfinite(live_odds)) else ml if np.all(np.isfinite(ml)) else None
    p, comps = model.predict(X, market)
    lambdas = tuple(artifact.get("lambdas") or DEFAULT_LAMBDAS)
    summ = summarize(p, race["race_id"], lambdas)
    contrib = model.logit.contributions(X) if isinstance(model, Ensemble) and model.logit else None
    factors = [runner_factors(i, contrib, evidence[i], X) for i in range(len(live))]
    headlines = getattr(h, "news", [])
    relevant_news = []
    for i, runner in enumerate(live):
        matched = news.relevant(headlines, runner, evidence[i].get("pedigree", {}))
        relevant_news.append([(item["title"], item["url"]) for item in matched])
        for item in matched:
            factors[i].append(f"• related headline ({item['source']}, {us_date(item['published_at'])}): {item['title']} — {item['url']}")
    avail = data_availability(X)
    completeness = float(np.mean([avail["morning_line"], avail["horse_history"], avail["jockey_trainer"],
                                  avail["pedigree"], avail["speed_figures"] or avail["horse_history"]]))
    offered = None
    if race.get("pools"):
        try:
            offered = {x["wager_type"] for x in json.loads(race["pools"])} | {"WIN", "PLACE", "SHOW"}
        except (ValueError, TypeError, KeyError):
            offered = None
    pools = _fresh_pools(read, progs) if read else None
    if pools is None:
        pools = _latest_pools(db, race["race_id"], progs, since)
    wagers = race_wagers(p, progs, odds, odds_src, pools, race["track_id"], race["race_id"], offered,
                         POLICY, lambdas)
    watch = win_watch(p, progs, odds, odds_src, POLICY)
    for w in watch:
        w["horse_name"] = names[progs.index(w["selection"])]
    odds_as_of = (read or {}).get("read_at") or db.scalar(
        "SELECT MAX(captured_at) FROM odds_snapshots WHERE race_id=? AND live_odds IS NOT NULL", [race["race_id"]])
    sel = select(race["race_id"], progs, names, p, comps, odds, odds_src, factors, wagers, completeness,
                 artifact.get("kind", "?"), watch=watch)
    events = news_events.for_race(db, race["race_id"], runners, now_iso())
    if events:
        sel["take"] = sel["take"] + " " + news_events.summary(events)
    q = None
    if market is not None:
        q = 1 / (market + 1)
        q = q / q.sum()
    unc = None
    if "logit" in comps and "gbm" in comps:
        unc = np.abs(comps["logit"] - comps["gbm"]) / 2
    elif "fundamental" in comps and "market" in comps:
        unc = np.abs(comps["fundamental"] - comps["market"]) / 2
    rank = {i: r + 1 for r, i in enumerate(np.argsort(-p))}
    runner_rows = []
    for i in range(len(live)):
        runner_rows.append({
            "program_number": progs[i], "horse_name": names[i], "rank": rank[i],
            "win_prob": float(p[i]), "place_prob": float(summ["place"][i]), "show_prob": float(summ["show"][i]),
            "exp_finish": float(summ["exp_finish"][i]), "fair_odds": fair_odds(float(p[i])),
            "odds_used": float(odds[i]) if np.isfinite(odds[i]) else None, "odds_source": odds_src[i],
            "market_prob": float(q[i]) if q is not None else None,
            "ev_win": float(p[i] * (odds[i] + 1) - 1) if np.isfinite(odds[i]) else None,
            "uncertainty": float(unc[i]) if unc is not None else None, "factors": factors[i]})
    unavailable = [k for k, v in avail.items() if not v]
    pace = projected_pace(X)
    # Everything the analysis rests on except the tote's minute-to-minute numbers: the
    # card, the people, the news, the profiles. Odds-driven change is judged on the
    # output instead (``material_change``), so a tick in the pools does not store a copy.
    inputs_hash = stable_hash({"m": model_version, "progs": progs, "odds_sources": odds_src,
                               "card": [(r.get("post_position"), r.get("jockey_id"), r.get("trainer_id"),
                                         r.get("weight"), r.get("equipment"), r.get("medication"))
                                        for r in live],
                               "cond": race.get("track_condition"), "surface": race.get("surface"),
                               "distance": race.get("distance_yards"), "post": race.get("post_time"),
                               "news": relevant_news,
                               "horse_profiles": [e.get("horse_profile") for e in evidence],
                               "events": [(e["program_number"], e["kind"], e["url"], e["confirmed"]) for e in events]})
    return {"race": race, "selection": sel, "runners": runner_rows, "wagers": wagers, "p": p, "q": q,
            "progs": progs, "pace": pace, "inputs_hash": inputs_hash,
            "data_notes": {"unavailable": unavailable, "odds_basis": "live" if market is live_odds else
                           "morning_line" if market is not None else "none",
                           "odds_as_of": odds_as_of, "watch": watch,
                           "news_events": news_events.as_notes(events)}}


PROB_STEP = 0.01      # a win probability moving by 1 point or more is a new view of the race
NEAR_POST = 16        # minutes: inside this, the stored view is kept no older than STALE_MIN
STALE_MIN = 10
SELECTION_KEYS = ("top1", "top2", "top3", "most_likely", "value_pick", "dark_horse", "decision", "confidence")


def last_snapshots(db: Database, race_ids: list[str]) -> dict[str, dict]:
    """The latest stored snapshot of each race, with its probabilities and wagers."""
    out: dict[str, dict] = {}
    for i in range(0, len(race_ids), 90):
        chunk = race_ids[i:i + 90]
        marks = ",".join("?" for _ in chunk)
        rows = db.query(f"SELECT p.* FROM predictions p JOIN (SELECT race_id, MAX(prediction_id) AS id FROM predictions "
                        f"WHERE race_id IN ({marks}) GROUP BY race_id) m ON p.prediction_id=m.id", chunk)
        for r in rows:
            out[r["race_id"]] = {"row": r, "probs": {}, "wagers": set()}
        ids = [r["prediction_id"] for r in rows]
        if not ids:
            continue
        marks = ",".join("?" for _ in ids)
        by_id = {r["prediction_id"]: r["race_id"] for r in rows}
        for r in db.query(f"SELECT prediction_id, program_number, win_prob FROM prediction_runners "
                          f"WHERE prediction_id IN ({marks})", ids):
            out[by_id[r["prediction_id"]]]["probs"][r["program_number"]] = r["win_prob"]
        for r in db.query(f"SELECT prediction_id, wager_type, selection FROM wagers WHERE prediction_id IN ({marks})", ids):
            out[by_id[r["prediction_id"]]]["wagers"].add((r["wager_type"], r["selection"]))
    return out


def material_change(a: dict, last: dict | None, now: str | None = None, prob_step: float = PROB_STEP) -> str | None:
    """Why this analysis is worth storing as a new snapshot, or None when it says what the
    stored one already says. Every analysis is still computed fresh; this only avoids
    writing identical opinions again."""
    if not last:
        return "first"
    row = last["row"]
    if row.get("inputs_hash") != a["inputs_hash"]:
        return "inputs"
    s = a["selection"]
    if any((s.get(k) or None) != (row.get(k) or None) for k in SELECTION_KEYS):
        return "selection"
    if {(w["wager_type"], w["selection"]) for w in a["wagers"]} != last["wagers"]:
        return "wagers"
    notes = row.get("data_notes")
    try:
        notes = json.loads(notes) if isinstance(notes, str) else (notes or {})
    except ValueError:
        notes = {}
    if notes.get("odds_basis") != a["data_notes"].get("odds_basis"):
        return "odds_basis"
    if {w["selection"] for w in notes.get("watch") or []} != {w["selection"] for w in a["data_notes"].get("watch") or []}:
        return "watch"
    probs = {r["program_number"]: r["win_prob"] for r in a["runners"]}
    if set(probs) != set(last["probs"]) or any(abs(probs[k] - (last["probs"][k] or 0)) >= prob_step for k in probs):
        return "probabilities"
    now = now or now_iso()
    post = a["race"].get("post_time")
    if post and row.get("created_at") and a["data_notes"].get("odds_basis") == "live":
        to_post = (parse_iso(post) - parse_iso(now)).total_seconds() / 60
        age = (parse_iso(now) - parse_iso(row["created_at"])).total_seconds() / 60
        if 0 < to_post <= NEAR_POST and age >= STALE_MIN:
            return "pre_post_refresh"
    return None


def record(db: Database, model_version: str, a: dict, reason: str, last: dict | None = None,
           checked: bool = False, prob_step: float = PROB_STEP) -> int | None:
    """Append a prediction snapshot when it says something new (``material_change``)."""
    race = a["race"]
    if not checked:
        last = last_snapshots(db, [race["race_id"]]).get(race["race_id"])
    if material_change(a, last, prob_step=prob_step) is None:
        return None
    s = a["selection"]
    try:
        pid = db.insert("predictions", {
            "race_id": race["race_id"], "model_version": model_version, "reason": reason,
            "top1": s["top1"], "top2": s["top2"], "top3": s["top3"], "most_likely": s["most_likely"],
            "value_pick": s["value_pick"], "dark_horse": s["dark_horse"], "confidence": s["confidence"],
            "confidence_score": s["confidence_score"], "decision": s["decision"],
            "pace": a["pace"], "take": s["take"], "inputs_hash": a["inputs_hash"], "data_notes": a["data_notes"]})
    except Exception as e:  # noqa: BLE001 — post time passed between analysis and insert
        log.warning("prediction for %s rejected: %s", race["race_id"], e)
        return None
    if pid is None:
        pid = db.scalar("SELECT MAX(prediction_id) FROM predictions WHERE race_id=?", [race["race_id"]])
    db.insert_many("prediction_runners", [dict(prediction_id=pid, **r) for r in a["runners"]])
    db.insert_many("wagers", [{
        "prediction_id": pid, "race_id": w.get("legs", [race["race_id"]])[-1],
        "legs": w.get("legs", [race["race_id"]]), "wager_type": w["wager_type"], "structure": w["structure"],
        "selection": w["selection"], "combos": w["combos"], "base_amount": w["base_amount"], "cost": w["cost"],
        "est_prob": w["est_prob"], "est_return": w.get("est_return"), "ev": w.get("ev"),
        "rationale": w.get("rationale")} for w in a["wagers"]])
    return pid


def _pool_legs(race: dict, races_by_num: dict[int, dict]) -> list[tuple[str, list[dict]]]:
    out = []
    if not race.get("pools"):
        return out
    try:
        pools = json.loads(race["pools"])
    except ValueError:
        return out
    for pl in pools:
        wt = pl.get("wager_type")
        if wt not in LEGS:
            continue
        rl = str(pl.get("race_list") or "")
        nums = [int(x) for x in rl.replace(",", "-").replace("/", "-").split("-") if x.strip().isdigit()]
        if not nums:
            nums = list(range(race["race_number"], race["race_number"] + LEGS[wt]))
        if len(nums) != LEGS[wt] or nums[0] != race["race_number"]:
            continue
        legs = [races_by_num.get(n) for n in nums]
        if all(legs):
            out.append((wt, legs))
    return out


def predict_date(db: Database, race_date: str, changed: dict[str, list[str]] | None = None,
                 only_changed: bool = False, fresh: dict | None = None, scope: dict | None = None) -> dict:
    """Analyze every upcoming race on ``race_date``; record new snapshots."""
    now = now_iso()
    version, model, row = champion(db)
    artifact = json.loads(row["artifact"])
    h = build_history(db, race_date)
    h.news = news.recent(db, now)
    races = db.query("SELECT * FROM races WHERE race_date=? AND is_cancelled=0 AND status='scheduled' "
                     "AND post_time IS NOT NULL AND post_time > ? ORDER BY track_id, race_number", [race_date, now])
    runners = defaultdict(list)
    for r in db.query("SELECT ru.* FROM runners ru JOIN races ra ON ra.race_id=ru.race_id WHERE ra.race_date=? "
                      "ORDER BY ru.post_position, ru.program_number", [race_date]):
        runners[r["race_id"]].append(r)
    analyses: dict[str, dict] = {}
    for race in races:
        if only_changed and changed is not None and race["race_id"] not in changed:
            continue
        a = analyze_race(db, h, version, model, artifact, race, runners[race["race_id"]], fresh)
        if a:
            analyses[race["race_id"]] = a
    # multi-race tickets: attach to the first leg's snapshot
    by_track = defaultdict(dict)
    for race in races:
        by_track[race["track_id"]][race["race_number"]] = race
    for race in races:
        a = analyses.get(race["race_id"])
        if not a:
            continue
        for wt, legs in _pool_legs(race, by_track[race["track_id"]]):
            leg_data = []
            for lr in legs:
                la = analyses.get(lr["race_id"]) or analyze_race(db, h, version, model, artifact, lr,
                                                                  runners[lr["race_id"]], fresh)
                if not la:
                    break
                if la["data_notes"]["odds_basis"] != "live":
                    break  # multi-race payouts are only estimated from live tote odds
                leg_data.append({"race_id": lr["race_id"], "progs": la["progs"], "p": la["p"], "q": la["q"]})
            if len(leg_data) == len(legs):
                t = multi_race_ticket(leg_data, wt, race["track_id"], POLICY)
                if t:
                    a["wagers"].append(t)
                    a["selection"]["decision"] = "play"
    # ``scope`` (quota.predict_scope) narrows what is stored when the day's write budget
    # is tight: a race never left without a snapshot, and races near post always stored.
    scope = scope or {}
    within, step = scope.get("within_min"), scope.get("prob_step", PROB_STEP)
    written, held = 0, 0
    stored = last_snapshots(db, list(analyses))
    for rid, a in analyses.items():
        last = stored.get(rid)
        if within is not None and last is not None:
            to_post = (parse_iso(a["race"]["post_time"]) - parse_iso(now)).total_seconds() / 60
            if to_post > within:
                held += 1
                continue
        reasons = (changed or {}).get(rid) or ["refresh"]
        if record(db, version, a, ",".join(reasons), last, checked=True, prob_step=step) is not None:
            written += 1
    return {"model_version": version, "races_analyzed": len(analyses), "snapshots_written": written,
            "unchanged_not_stored": len(analyses) - written - held, "held_for_quota": held}


def lock_due(db: Database) -> int:
    """Lock the final pre-post prediction for every race whose post time has passed."""
    now = now_iso()
    due = db.query("SELECT r.race_id, r.post_time FROM races r LEFT JOIN locks l ON l.race_id=r.race_id "
                   "WHERE l.race_id IS NULL AND r.post_time IS NOT NULL AND r.post_time <= ?", [now])
    n = 0
    for d in due:
        p = db.one("SELECT prediction_id FROM predictions WHERE race_id=? AND created_at < ? "
                   "ORDER BY created_at DESC, prediction_id DESC LIMIT 1", [d["race_id"], d["post_time"]])
        if p:
            db.insert("locks", {"race_id": d["race_id"], "prediction_id": p["prediction_id"]}, verb="INSERT OR IGNORE")
            n += 1
    return n


