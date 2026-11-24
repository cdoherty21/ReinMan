"""Historical walk-forward backtesting.

For each calendar month M: fit on every race before M (validation = the most recent
15% of that history), then predict M blind. Features are point-in-time, so the
model never sees a result before predicting it. Betting simulations use only odds
knowable before the race (the morning line) for decisions; payouts are official.
A separate line reports bets at closing tote odds — labeled as not attainable in
practice because final odds are only known after betting closes.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

import numpy as np

from .config import POLICY
from .db import Database
from .features import build_dataset
from .grading import entry
from .models import MarketBaseline
from .train import fit_candidate, market_odds


def _payoffs(db: Database) -> dict:
    out = defaultdict(dict)
    for r in db.query("SELECT race_id, program_number, win_payoff FROM result_finishers WHERE win_payoff IS NOT NULL"):
        if r["program_number"]:
            out[r["race_id"]][entry(r["program_number"])] = float(r["win_payoff"])
    return out


def run(db: Database, start_month: str | None = None, min_train: int = 1000) -> dict:
    samples = build_dataset(db)
    if not samples:
        return {"status": "no data"}
    pay = _payoffs(db)
    months = sorted({s.race_date[:7] for s in samples})
    agg = defaultdict(list)
    bets = defaultdict(lambda: {"n": 0, "wagered": 0.0, "returned": 0.0})
    folds = []
    for m in months:
        if start_month and m < start_month:
            continue
        hist = [s for s in samples if s.race_date[:7] < m]
        test = [s for s in samples if s.race_date[:7] == m]
        if len(hist) < min_train or not test:
            continue
        cut = hist[int(len(hist) * 0.85)].race_date
        train = [s for s in hist if s.race_date < cut]
        valid = [s for s in hist if s.race_date >= cut]
        model, _ = fit_candidate(train, valid)
        base = MarketBaseline()
        fold = {"month": m, "train": len(train), "test": len(test)}
        for s in test:
            win = s.finish == 1
            progs = [entry(r["program_number"]) for r in s.runners]
            ml = np.array([r["ml_odds"] if r["ml_odds"] is not None else np.nan for r in s.runners], float)
            close = np.array([r["live_odds"] if r["live_odds"] is not None else np.nan for r in s.runners], float)
            p, _ = model.predict(s.X, ml if np.all(np.isfinite(ml)) else None)  # pre-race info only
            pb, _ = base.predict(s.X, ml if np.all(np.isfinite(ml)) else None)
            agg["ll_model"].append(-math.log(max(float(p[win].sum()), 1e-12)))
            agg["ll_ml"].append(-math.log(max(float(pb[win].sum()), 1e-12)))
            agg["top_model"].append(float(win[int(np.argmax(p))]))
            if np.all(np.isfinite(ml)):
                agg["top_ml_fav"].append(float(win[int(np.argmin(ml))]))
            if np.all(np.isfinite(close)):
                pc = 1 / (close + 1)
                pc /= pc.sum()
                agg["ll_close"].append(-math.log(max(float(pc[win].sum()), 1e-12)))
                agg["ll_model_paired_close"].append(agg["ll_model"][-1])
                agg["top_close_fav"].append(float(win[int(np.argmin(close))]))
            payoffs = pay.get(s.race_id, {})
            i = int(np.argmax(p))
            _bet(bets["top_pick_flat"], progs[i], payoffs)
            for j in range(len(p)):
                if np.isfinite(ml[j]) and p[j] >= POLICY.win_min_prob and ml[j] <= POLICY.win_max_odds \
                        and p[j] * (ml[j] + 1) - 1 >= POLICY.win_min_edge_ml:
                    _bet(bets["value_vs_morning_line"], progs[j], payoffs)
                if np.isfinite(close[j]) and p[j] >= POLICY.win_min_prob and close[j] <= POLICY.win_max_odds \
                        and p[j] * (close[j] + 1) - 1 >= POLICY.win_min_edge_live:
                    _bet(bets["value_at_closing_odds_unattainable"], progs[j], payoffs)
        folds.append(fold)
    mean = {k: (float(np.mean(v)) if v else None) for k, v in agg.items()}
    for b in bets.values():
        b["roi"] = (b["returned"] - b["wagered"]) / b["wagered"] if b["wagered"] else None
    metrics = {"folds": folds, "races": len(agg["ll_model"]), "mean": mean, "bets": dict(bets),
               "policy": POLICY.to_dict()}
    if folds:
        db.insert("backtests", {"model_kind": "ensemble-walk-forward",
                                "test_window": f"{folds[0]['month']}..{folds[-1]['month']}",
                                "metrics": json.dumps(metrics)})
    return metrics


def _bet(acc: dict, prog: str, payoffs: dict) -> None:
    acc["n"] += 1
    acc["wagered"] += 2.0
    acc["returned"] += payoffs.get(prog, 0.0)
