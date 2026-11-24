"""Wager evaluation: expected value with takeout, pool math and ticket construction.

Rein Man never forces a bet. A wager is recommended only when its estimated edge
clears the policy threshold for that wager type. Every recommendation records its
estimated probability, estimated return and EV so it can be graded later.

EV notes
* Win: uses live tote odds when available (already net of takeout), otherwise the
  morning line with a stricter threshold.
* Place/Show: only with per-horse pool amounts; payouts follow the pari-mutuel
  formula (net pool minus the money on the placing horses, split, plus stake),
  averaged over simulated finishing orders. Minimum $2.10 payout on $2.
* Exotics and multi-race: the payout of a combination is estimated from the
  market-implied probability of that combination (same finish-order model applied
  to market probabilities) and the pool's takeout. These are estimates; actual
  probables/will-pays are not available from the current data source.
"""
from __future__ import annotations

import itertools
import math

import numpy as np

from .config import POLICY, Policy
from .probability import DEFAULT_LAMBDAS, exotic_table, ordered_prob, simulate
from .wagertypes import MIN_BASE, takeout


def win_ev(p: float, odds: float | None) -> float | None:
    if odds is None:
        return None
    return p * (odds + 1.0) - 1.0


def kelly_fraction(p: float, odds: float) -> float:
    return max(0.0, (p * (odds + 1) - 1) / odds) if odds > 0 else 0.0


def pool_place_show_ev(p: np.ndarray, pools: np.ndarray, pool_total: float, places: int, t: float,
                       key: str, lambdas=DEFAULT_LAMBDAS) -> np.ndarray:
    """EV per $1 for place (places=2) or show (places=3) on each runner."""
    n = len(p)
    sims = simulate(p, 20000, key + f":ps{places}", lambdas)
    net = pool_total * (1 - t)
    ev = np.zeros(n)
    top = sims[:, :places]
    for i in range(n):
        hit = (top == i).any(axis=1)
        if not hit.any() or pools[i] <= 0:
            ev[i] = -1.0
            continue
        others_amt = pools[top[hit]].sum(axis=1)
        profit = (net - others_amt) / places
        per_dollar = np.maximum(1.0 + profit / pools[i], 1.05)
        ev[i] = hit.mean() * per_dollar.mean() - 1.0
    return ev


def _fmt(order, progs):
    return "-".join(progs[i] for i in order)


def exotic_candidates(p: np.ndarray, q_market: np.ndarray, progs: list[str], wager_type: str,
                      track_id: str, policy: Policy = POLICY, lambdas=DEFAULT_LAMBDAS) -> dict | None:
    positions = {"EXACTA": 2, "TRIFECTA": 3, "SUPERFECTA": 4}[wager_type]
    if len(p) < positions + 1 or (wager_type == "SUPERFECTA" and len(p) < 7):
        return None
    t = takeout(track_id, wager_type)
    # limit enumeration to plausible contenders for speed
    order_idx = list(np.argsort(-p))[: min(len(p), 9 if positions < 4 else 8)]
    model = exotic_table(p, positions, lambdas) if len(p) <= 9 else None
    market = exotic_table(q_market, positions, lambdas) if len(p) <= 9 else None
    edge_min = {"EXACTA": policy.exacta_min_edge, "TRIFECTA": policy.trifecta_min_edge,
                "SUPERFECTA": policy.superfecta_min_edge}[wager_type]
    pmin = policy.exotic_min_combo_prob[wager_type]
    rows = []
    combos = (model.items() if model is not None else
              ((o, None) for o in itertools.permutations(order_idx, positions)))
    for order, pm in combos:
        if pm is None:
            pm = ordered_prob(p, order, lambdas)
        if pm < pmin:
            continue
        qm = market[order] if market is not None else ordered_prob(q_market, order, lambdas)
        est_payout = (1 - t) / max(qm, 1e-9)          # per $1
        ev = pm * est_payout - 1.0
        if ev >= edge_min:
            rows.append((ev, order, pm, est_payout))
    if not rows:
        return None
    rows.sort(key=lambda r: -r[0] * r[2])  # favor edge weighted by hit probability
    rows = rows[: policy.max_combos[wager_type]]
    base = MIN_BASE[wager_type]
    prob = sum(r[2] for r in rows)
    exp_return = sum(r[2] * r[3] * base for r in rows)
    cost = base * len(rows)
    return {
        "wager_type": wager_type, "structure": "straight",
        "selection": " ".join(_fmt(r[1], progs) for r in rows),
        "combos": len(rows), "base_amount": base, "cost": round(cost, 2), "est_prob": prob,
        "est_return": round(exp_return, 2), "ev": (exp_return - cost) / cost,
        "rationale": f"{len(rows)} combination(s) priced above the estimated market payout "
                     f"(takeout {t:.0%} applied)",
    }


def race_wagers(p: np.ndarray, progs: list[str], odds: np.ndarray, odds_src: list[str | None],
                pools: dict | None, track_id: str, race_key: str, offered: set[str] | None,
                policy: Policy = POLICY, lambdas=DEFAULT_LAMBDAS) -> list[dict]:
    """Single-race wager recommendations. ``offered`` = wager types offered (None = unknown)."""
    out: list[dict] = []
    n = len(p)

    def ok(wt):
        return offered is None or wt in offered

    # ---- win
    cands = []
    for i in range(n):
        o = odds[i]
        if not np.isfinite(o) or o > policy.win_max_odds or p[i] < policy.win_min_prob:
            continue
        ev = win_ev(p[i], o)
        need = policy.win_min_edge_live if odds_src[i] == "live" else policy.win_min_edge_ml
        if ev is not None and ev >= need:
            cands.append((ev, i))
    for ev, i in sorted(cands, reverse=True)[:2]:
        out.append({"wager_type": "WIN", "structure": "straight", "selection": progs[i], "combos": 1,
                    "base_amount": 2.0, "cost": 2.0, "est_prob": float(p[i]),
                    "est_return": round(float(p[i] * (odds[i] + 1) * 2), 2), "ev": float(ev),
                    "rationale": f"{p[i]:.0%} to win vs {1/(odds[i]+1):.0%} implied by "
                                 f"{'tote' if odds_src[i] == 'live' else 'morning line'} odds; "
                                 f"Kelly fraction {kelly_fraction(p[i], odds[i]):.1%}"})
    # ---- place / show from pools
    if pools and ok("PLACE"):
        for wt, places, key in (("PLACE", 2, "place"), ("SHOW", 3, "show")):
            amt = pools.get(key)
            if amt is None or not np.all(np.isfinite(amt)) or amt.sum() <= 0 or n < places + 2:
                continue
            ev = pool_place_show_ev(p, amt, float(amt.sum()), places, takeout(track_id, wt), race_key, lambdas)
            i = int(np.argmax(ev))
            if ev[i] >= policy.ps_min_edge:
                out.append({"wager_type": wt, "structure": "straight", "selection": progs[i], "combos": 1,
                            "base_amount": 2.0, "cost": 2.0, "est_prob": None, "est_return": round(2 * (1 + ev[i]), 2),
                            "ev": float(ev[i]), "rationale": f"{wt.lower()} pool overlay from current pool amounts"})
    # ---- exotics: payouts are estimated from the market, so require live tote odds for every
    # runner. Morning lines are too noisy to price combinations (confirmed in testing).
    if np.all(np.isfinite(odds)) and all(src == "live" for src in odds_src):
        q = 1 / (odds + 1)
        q = q / q.sum()
        for wt in ("EXACTA", "TRIFECTA", "SUPERFECTA"):
            if not ok(wt):
                continue
            c = exotic_candidates(p, q, progs, wt, track_id, policy, lambdas)
            if c:
                out.append(c)
    # place/show est_prob from simulation for recording
    for w in out:
        if w["est_prob"] is None:
            sims = simulate(p, 5000, race_key + ":rec", lambdas)
            i = progs.index(w["selection"])
            places = 2 if w["wager_type"] == "PLACE" else 3
            w["est_prob"] = float((sims[:, :places] == i).any(axis=1).mean())
    return out


def multi_race_ticket(legs: list[dict], wager_type: str, track_id: str, policy: Policy = POLICY) -> dict | None:
    """Build a Daily Double / Pick-N ticket across consecutive races.

    Each leg: {race_id, progs, p (model), q (market or None)}.
    """
    t = takeout(track_id, wager_type)
    sel, cover_p, cover_q = [], [], []
    for leg in legs:
        p, q = leg["p"], leg["q"]
        if q is None:
            return None
        order = list(np.argsort(-p))
        chosen, cum = [], 0.0
        for i in order:
            if cum >= policy.multi_leg_coverage or len(chosen) >= policy.multi_max_per_leg:
                break
            chosen.append(i)
            cum += p[i]
        # add a single value horse if it has a strong edge and room remains
        ratio = p / np.maximum(q, 1e-6)
        for i in np.argsort(-ratio):
            if i not in chosen and ratio[i] >= 1.5 and p[i] >= 0.08 and len(chosen) < policy.multi_max_per_leg:
                chosen.append(int(i))
                break
        sel.append(chosen)
        cover_p.append(p)
        cover_q.append(q)
    base = MIN_BASE[wager_type]
    combos = math.prod(len(c) for c in sel)
    cost = combos * base
    if cost > policy.multi_max_cost:
        return None
    prob, exp_ret = 0.0, 0.0
    for combo in itertools.product(*sel):
        pm = math.prod(float(cover_p[k][i]) for k, i in enumerate(combo))
        qm = math.prod(float(cover_q[k][i]) for k, i in enumerate(combo))
        prob += pm
        exp_ret += pm * base * (1 - t) / max(qm, 1e-12)
    ev = (exp_ret - cost) / cost
    if ev < policy.multi_min_edge:
        return None
    return {"wager_type": wager_type, "structure": "ticket",
            "selection": "/".join(",".join(legs[k]["progs"][i] for i in sorted(c)) for k, c in enumerate(sel)),
            "combos": combos, "base_amount": base, "cost": round(cost, 2), "est_prob": prob,
            "est_return": round(exp_ret, 2), "ev": ev, "legs": [leg["race_id"] for leg in legs],
            "rationale": f"covers {prob:.0%} of outcomes; estimated payouts from market-implied "
                         f"leg probabilities with {t:.0%} takeout"}
