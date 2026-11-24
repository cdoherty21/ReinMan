"""Grade locked predictions and their hypothetical wagers against official results.

Rules applied (documented in docs/GRADING.md):
* Only the locked (final pre-post) prediction of each race is graded. Earlier
  snapshots are kept for the audit trail but never counted.
* Payouts come from official result payoffs, scaled from the payoff's base amount
  to the ticket's base amount. Dead heats are handled because official payoffs
  already reflect split pools; a dead-heated finisher counts as finishing in that
  position.
* Coupled entries (1, 1A) are one betting interest.
* A selection scratched after lock: straight bets on it are refunded; exotic and
  multi-race combinations containing it are refunded per combination. (Tracks
  differ on multi-race scratch rules — some substitute the post-time favorite;
  that information is not in the data, so Rein Man refunds and notes it.)
* Cancelled race / no contest: all wagers refunded; the pick is graded no_contest.
* Winning combination with no official payoff available after the grace period:
  recorded as ``unpriced`` with the stake returned and a note, never silently dropped.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone

from .db import Database
from .util import norm_name

GRACE_DAYS = 2


def entry(prog: str | None) -> str:
    if prog is None:
        return ""
    s = str(prog).strip().upper()
    return re.sub(r"[A-Z]+$", "", s) or s


def _tokens(numbers: str) -> list[list[str]]:
    """'3-5-1' -> [['3'],['5'],['1']]; '3/5' -> [['3'],['5']]; dead-heat '3/5-1' kept per slot."""
    s = (numbers or "").strip()
    if not s:
        return []
    sep = "-" if "-" in s else "/" if "/" in s else ","
    return [[entry(x) for x in re.split(r"[,/]" if sep == "-" else r",", part) if x.strip()]
            for part in s.split(sep)]


def _combo_matches(combo: list[str], numbers: str) -> bool:
    toks = _tokens(numbers)
    if len(toks) != len(combo):
        return False
    return all(entry(c) in t for c, t in zip(combo, toks))


def _payout(payoffs: list[dict], wtype: str, combo: list[str], base: float) -> float | None:
    for p in payoffs:
        if p["wager_type"] == wtype and p.get("payoff_amount") and _combo_matches(combo, p.get("winning_numbers") or ""):
            pb = p.get("base_amount") or 2.0
            return float(p["payoff_amount"]) * base / pb
    return None


class RaceResult:
    def __init__(self, db: Database, race_id: str):
        self.race_id = race_id
        self.race = db.one("SELECT * FROM races WHERE race_id=?", [race_id])
        self.official = db.one("SELECT * FROM results WHERE race_id=?", [race_id]) is not None
        self.cancelled = bool(self.race and (self.race["is_cancelled"] or self.race["status"] == "cancelled"))
        self.fin = db.query("SELECT * FROM result_finishers WHERE race_id=?", [race_id])
        self.payoffs = db.query("SELECT * FROM result_payoffs WHERE race_id=?", [race_id])
        runners = db.query("SELECT program_number, horse_name, scratched FROM runners WHERE race_id=?", [race_id])
        sc_names = {norm_name(r["horse_name"]) for r in db.query(
            "SELECT horse_name FROM result_scratches WHERE race_id=?", [race_id])}
        self.scratched = {entry(r["program_number"]) for r in runners
                          if r["scratched"] or norm_name(r["horse_name"]) in sc_names}
        # an entry is only "scratched" if every horse in it was scratched
        live_entries = {entry(r["program_number"]) for r in runners
                        if not (r["scratched"] or norm_name(r["horse_name"]) in sc_names)}
        self.scratched -= live_entries
        self.pos: dict[str, int] = {}
        for f in self.fin:
            if f["program_number"]:
                e = entry(f["program_number"])
                self.pos[e] = min(self.pos.get(e, 99), f["finish_position"])
        self.winners = {e for e, p in self.pos.items() if p == 1}

    def payoff_field(self, e: str, field: str) -> float | None:
        for f in self.fin:
            if entry(f["program_number"]) == e and f.get(field):
                return float(f[field])
        return None


def grade_wager(w: dict, results: dict[str, RaceResult], now: datetime) -> dict | None:
    legs = json.loads(w["legs"]) if isinstance(w["legs"], str) else w["legs"]
    rr = [results[x] for x in legs]
    if any(r.cancelled for r in rr):
        return {"status": "refunded", "payout": w["cost"], "refund": w["cost"], "notes": "race cancelled"}
    if not all(r.official for r in rr):
        return None
    wt, base = w["wager_type"], float(w["base_amount"])
    settle = rr[-1]
    if wt in ("WIN", "PLACE", "SHOW"):
        r = rr[0]
        e = entry(w["selection"])
        if e in r.scratched:
            return {"status": "refunded", "payout": w["cost"], "refund": w["cost"], "notes": "scratched"}
        need = {"WIN": 1, "PLACE": 2, "SHOW": 3}[wt]
        fld = {"WIN": "win_payoff", "PLACE": "place_payoff", "SHOW": "show_payoff"}[wt]
        if r.pos.get(e, 99) <= need:
            pay = r.payoff_field(e, fld)
            if pay is None:
                return _unpriced(w, settle, now)
            return {"status": "won", "payout": round(pay * base / 2.0, 2), "refund": 0.0, "notes": None}
        return {"status": "lost", "payout": 0.0, "refund": 0.0, "notes": None}
    combos = _expand(w)
    payout = refund = 0.0
    unpriced = False
    for combo in combos:
        if wt in ("EXACTA", "TRIFECTA", "SUPERFECTA"):
            if any(entry(c) in settle.scratched for c in combo):
                refund += base
                continue
            hit = all(settle.pos.get(entry(c), 99) == k + 1 or _dh_ok(settle, entry(c), k + 1)
                      for k, c in enumerate(combo))
        else:
            if any(entry(c) in rr[k].scratched for k, c in enumerate(combo)):
                refund += base
                continue
            hit = all(entry(c) in rr[k].winners for k, c in enumerate(combo))
        if hit:
            pay = _payout(settle.payoffs, wt, combo, base)
            if pay is None:
                unpriced = True
            else:
                payout += pay
    if unpriced and payout == 0:
        return _unpriced(w, settle, now)
    total = round(payout + refund, 2)
    status = "won" if payout > 0 else "refunded" if refund >= w["cost"] - 1e-9 else \
        "partial_refund" if refund > 0 else "lost"
    return {"status": status, "payout": total, "refund": round(refund, 2),
            "notes": "multi-race scratch refunded (substitution rules not available)" if refund and wt not in
            ("EXACTA", "TRIFECTA", "SUPERFECTA") else None}


def _dh_ok(r: RaceResult, e: str, k: int) -> bool:
    """Dead heat: two runners share position k; either may fill slot k or k+1."""
    p = r.pos.get(e)
    if p is None:
        return False
    tied = [x for x, q in r.pos.items() if q == p]
    return len(tied) > 1 and p <= k < p + len(tied)


def _unpriced(w, settle, now) -> dict | None:
    off = datetime.fromisoformat(settle.race["post_time"].replace("Z", "+00:00")) if settle.race else now
    if now - off < timedelta(days=GRACE_DAYS):
        return None
    return {"status": "unpriced", "payout": w["cost"], "refund": w["cost"],
            "notes": "winning combination but no official payoff available; stake returned"}


def _expand(w: dict) -> list[list[str]]:
    sel = w["selection"].strip()
    if w["structure"] == "ticket":
        import itertools
        legs = [x.split(",") for x in sel.split("/")]
        return [list(c) for c in itertools.product(*legs)]
    return [c.split("-") for c in sel.split()]


def grade_all(db: Database) -> dict:
    now = datetime.now(timezone.utc)
    pending = db.query("SELECT l.race_id, l.prediction_id FROM locks l LEFT JOIN grades g ON g.race_id=l.race_id "
                       "WHERE g.race_id IS NULL")
    cache: dict[str, RaceResult] = {}

    def res(rid):
        if rid not in cache:
            cache[rid] = RaceResult(db, rid)
        return cache[rid]

    graded = 0
    for lk in pending:
        r = res(lk["race_id"])
        if not (r.official or r.cancelled):
            continue
        pr = db.query("SELECT * FROM prediction_runners WHERE prediction_id=? ORDER BY rank", [lk["prediction_id"]])
        pred = db.one("SELECT * FROM predictions WHERE prediction_id=?", [lk["prediction_id"]])
        row = {"race_id": lk["race_id"], "prediction_id": lk["prediction_id"]}
        if r.cancelled or not r.winners:
            row.update(status="no_contest")
        else:
            fin = {x["program_number"]: r.pos.get(entry(x["program_number"])) for x in pr}
            row["top1_finish"] = fin.get(pred["top1"])
            row["top2_finish"] = fin.get(pred["top2"]) if pred["top2"] else None
            row["top3_finish"] = fin.get(pred["top3"]) if pred["top3"] else None
            wr = [x["rank"] for x in pr if entry(x["program_number"]) in r.winners]
            row["winner_rank"] = min(wr) if wr else None
            t1 = next(x for x in pr if x["program_number"] == pred["top1"])
            row["top1_odds"] = t1["odds_used"]
            if row["top1_finish"] == 1:
                row["top1_win_payoff"] = r.payoff_field(entry(pred["top1"]), "win_payoff")
            live = [x for x in pr if entry(x["program_number"]) not in r.scratched]
            tot = sum(x["win_prob"] for x in live) or 1.0
            pw = sum(x["win_prob"] for x in live if entry(x["program_number"]) in r.winners) / tot
            row["log_loss"] = -math.log(max(pw, 1e-9)) if wr else None
            row["brier"] = sum((x["win_prob"] / tot - (1.0 if entry(x["program_number"]) in r.winners else 0.0)) ** 2
                               for x in live)
            row["status"] = "top1_scratched" if entry(pred["top1"]) in r.scratched else "graded"
            row["details"] = {"winners": sorted(r.winners), "scratched": sorted(r.scratched)}
        db.insert("grades", row)
        graded += 1
    # wagers from locked predictions
    wq = db.query("SELECT w.* FROM wagers w JOIN locks l ON l.prediction_id=w.prediction_id "
                  "LEFT JOIN wager_grades g ON g.wager_id=w.wager_id WHERE g.wager_id IS NULL")
    wg = 0
    for w in wq:
        legs = json.loads(w["legs"])
        results = {x: res(x) for x in legs}
        g = grade_wager(w, results, now)
        if g:
            db.insert("wager_grades", {"wager_id": w["wager_id"], **g})
            wg += 1
    return {"predictions_graded": graded, "wagers_graded": wg}

