"""Rein Man's selections and his written take.

All numbers come from the statistical model. The text is generated
deterministically from the model's own evidence (feature contributions and the
horse's recorded history) — no language-model calls, no invented facts.
"""
from __future__ import annotations

import hashlib

import numpy as np

from .config import POLICY, Policy
from .horse_profiles import _n
from .util import fmt_odds

PHRASES = {
    "ml_logp": ("the morning line respects him", "the morning line has him well down the order"),
    "h_win_rate": ("a proven winner", "rarely gets his picture taken"),
    "h_itm_rate": ("consistently hits the board", "struggles to hit the board"),
    "h_last_won": ("won last time out", None),
    "h_last_itm": ("hit the board last time", "missed the board last time"),
    "h_last3_itm": ("in solid recent form", "recent form is poor"),
    "h_layoff": ("fresh off a layoff", "returns from a layoff"),
    "h_days_log": ("well-spaced since his last start", "racing back quickly"),
    "h_surface_itm": ("likes this surface", "hasn't shown much on this surface"),
    "h_dist_itm": ("suited to this distance", "unproven at this distance"),
    "h_track_itm": ("fires at this track", "has not handled this track"),
    "h_offtrack_itm": ("handles an off track", "questionable on an off track"),
    "h_first_surface": ("tries a new surface", "tries a new surface"),
    "class_change": ("steps up in class", "drops in class"),
    "claim_change": ("claiming price rises", "drops in claiming price"),
    "weight_change": ("picks up weight", "sheds weight"),
    "equip_change": ("equipment change", "equipment change"),
    "first_lasix": ("first-time Lasix", None),
    "j_win": ("a winning rider", "the rider wins at a low rate"),
    "j_win_30d": ("rider is hot", "rider is cold"),
    "j_track_win": ("rider excels here", None),
    "j_change_delta": ("upgrades in the irons", "rider downgrade"),
    "t_win": ("a sharp barn", "barn wins at a low rate"),
    "t_win_30d": ("barn is in form", "barn is cold"),
    "t_track_win": ("trainer does well here", None),
    "t_layoff_win": ("trainer wins off a layoff", None),
    "t_debut_win": ("trainer wins with debut runners", None),
    "jt_win": ("a productive jockey-trainer team", None),
    "sire_surface_win": ("pedigree for the surface", "pedigree questions on this surface"),
    "sire_dist_win": ("bred for the trip", None),
    "sire_offtrack_win": ("bred for the slop", None),
    "post_rel": ("drew outside", "drew inside"),
    "post_bias": ("drew a post that has been winning", "drew a post that has been losing"),
    "pp_last_speed": ("fast last-out figure", "slow last-out figure"),
    "pp_best_speed3": ("top figure fits", "figures come up short"),
    "pp_speed_trend": ("figures are improving", "figures are declining"),
    "pp_pace_adv": ("the pace sets up for him", "the pace works against him"),
    "pp_last_trouble": ("excuse last time (trouble)", None),
    "pp_beaten_last": ("beaten far last time", "close last time"),
    "pp_works_30d": ("training forwardly", None),
}

CLOSERS = [
    "The tote board will tell you what the crowd thinks. I care what the evidence says.",
    "Price is the whole game. A good horse at a bad price is a bad bet.",
    "No hunches. No hot tips. Just numbers.",
    "Win the bet, not the argument.",
    "When the edge is there, I'll take it. When it isn't, I keep my hands in my pockets.",
    "The favorite is a fact, not a law.",
    "I've watched a lot of races. Most of them weren't worth betting.",
]


def _h(key: str) -> int:
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16)


def runner_factors(i: int, contrib: np.ndarray | None, evidence: dict, X: np.ndarray) -> list[str]:
    """Evidence lines for runner i: strongest model contributions + recorded history."""
    lines: list[str] = []
    if contrib is not None:
        from .models.core import LOGIT_FEATURES
        c = contrib[i]
        order = np.argsort(-np.abs(c))
        for j in order[:6]:
            if abs(c[j]) < 0.05:
                break
            name = LOGIT_FEATURES[j]
            pos, neg = PHRASES.get(name, (None, None))
            phrase = pos if c[j] > 0 else neg
            if phrase:
                lines.append(("+ " if c[j] > 0 else "− ") + phrase)
    ev = evidence or {}
    if ev.get("starts"):
        lines.append(f"• record in tracked races: {_n(ev['starts'], 'start')}, {_n(ev['wins'], 'win')}, {ev['itm']} in the money")
        if ev.get("days") is not None:
            lines.append(f"• {_n(ev['days'], 'day')} since last tracked start")
    else:
        lines.append("• no prior starts in Rein Man's database")
    if ev.get("archive_wins"):
        lines.append("• historical winner archive at this track: " + ", ".join(
            f"{role} {_n(count, 'recorded win')}" for role, count in ev["archive_wins"].items()))
    from .horse_profiles import evidence as profile_evidence
    if "horse_profile" in ev:
        lines.extend("Horse profile · " + line for line in profile_evidence(ev["horse_profile"]))
    if ev.get("additional_pp_starts"):
        lines.append(f"• {_n(ev['additional_pp_starts'], 'additional start')} from uploaded past performances or charts")
    if ev.get("pedigree"):
        pedigree = ev["pedigree"]
        lines.append("• lineage: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in pedigree.items()))
    if ev.get("travel_miles") is not None:
        days = f"{_n(ev['rest_days'], 'day')} since that start"
        lines.append(f"• same track as last start ({ev['previous_track']}); {days}" if not ev["travel_miles"] else
                     f"• {ev['travel_miles']} miles from last race venue ({ev['previous_track']}); {days}")
    if ev.get("workouts_30d"):
        lines.append(f"• {_n(ev['workouts_30d'], 'recorded workout')} in last 30 days; latest {ev['last_work_date']}")
    if ev.get("beyer_figures"):
        lines.append("• recent Beyer figures (latest first): " + ", ".join(map(str, ev["beyer_figures"])))
    for person in ("trainer", "jockey"):
        snapshot = ev.get(person + "_snapshot")
        if snapshot:
            lines.append(f"• {person} published season record: {snapshot['win_pct']}% wins from {_n(snapshot['starts'], 'start')}")
        record = ev.get(person + "_30d")
        if record and record["starts"]:
            lines.append(f"• {person}: {_n(record['wins'], 'win')} in {_n(record['starts'], 'tracked start')} in last 30 days")
    return lines


def confidence(p: np.ndarray, comps: dict, completeness: float, policy: Policy = POLICY) -> tuple[str, float]:
    s = np.sort(p)[::-1]
    p1, p2 = s[0], s[1] if len(s) > 1 else 0.0
    n = len(p)
    strength = min(1.0, (p1 - 1.0 / n) / 0.35)
    gap = min(1.0, (p1 - p2) / 0.20)
    agree = 1.0
    if "logit" in comps and "gbm" in comps:
        agree = 1.0 - min(1.0, float(np.abs(comps["logit"] - comps["gbm"]).sum()) / 0.6)
    score = float(np.clip(0.4 * strength + 0.25 * gap + 0.2 * completeness + 0.15 * agree, 0, 1))
    level = "High" if score >= policy.high_conf_score else "Medium" if score >= policy.med_conf_score else "Low"
    return level, score


def select(race_key: str, progs: list[str], names: list[str], p: np.ndarray, comps: dict,
           odds: np.ndarray, odds_src: list[str | None], factors: list[list[str]], wagers: list[dict],
           completeness: float, model_kind: str, policy: Policy = POLICY, watch: list[dict] | None = None) -> dict:
    order = list(np.argsort(-p))
    top = [progs[i] for i in order[:3]]
    q = None
    if np.all(np.isfinite(odds)):
        q = 1 / (odds + 1)
        q = q / q.sum()
    # value: best win EV among plausible contenders
    value = None
    best = 0.0
    for i in range(len(p)):
        if np.isfinite(odds[i]) and p[i] >= policy.win_min_prob:
            ev = p[i] * (odds[i] + 1) - 1
            if ev > best:
                best, value = ev, i
    # dark horse: long price, model materially above market, with supporting evidence
    dark = None
    if q is not None:
        cands = []
        for i in range(len(p)):
            if i == order[0] or not np.isfinite(odds[i]) or odds[i] < policy.dark_min_odds:
                continue
            ratio = p[i] / max(q[i], 1e-6)
            support = any(f.startswith("+ ") for f in factors[i])
            if ratio >= policy.dark_min_ratio and p[i] >= policy.dark_min_prob and support:
                cands.append((ratio, i))
        if cands:
            dark = max(cands)[1]
    level, score = confidence(p, comps, completeness, policy)
    decision = "play" if wagers else "pass"
    take = write_take(race_key, progs, names, p, odds, odds_src, order, value, dark, factors, wagers,
                      level, model_kind, watch or [])
    return {"top1": top[0], "top2": top[1] if len(top) > 1 else None, "top3": top[2] if len(top) > 2 else None,
            "most_likely": progs[order[0]], "value_pick": progs[value] if value is not None else None,
            "dark_horse": progs[dark] if dark is not None else None, "confidence": level,
            "confidence_score": score, "decision": decision, "take": take}


def write_take(race_key, progs, names, p, odds, odds_src, order, value, dark, factors, wagers, level,
               model_kind, watch=()) -> str:
    i0 = order[0]
    pos = [f[2:] for f in factors[i0] if f.startswith("+ ")][:2]
    why = " and ".join(pos) if pos else "the numbers simply line up best"
    o0 = fmt_odds(odds[i0]) if np.isfinite(odds[i0]) else "no posted odds"
    parts = [f"#{progs[i0]} {names[i0]} is the most likely winner at {p[i0]:.0%} (fair odds "
             f"{fmt_odds(max(1 / p[i0] - 1, 0.05))}, {o0} {'on the tote' if odds_src[i0] == 'live' else 'morning line' if odds_src[i0] else ''}) — {why}."]
    if len(order) > 2:
        parts.append(f"#{progs[order[1]]} {names[order[1]]} and #{progs[order[2]]} {names[order[2]]} "
                     f"round out the top three.")
    if value is not None and value != i0:
        parts.append(f"The value lies with #{progs[value]} {names[value]}: {p[value]:.0%} by my numbers at "
                     f"{fmt_odds(odds[value])}{'' if odds_src[value] == 'live' else ' (morning line)'}.")
    elif value == i0:
        parts.append("The most likely winner is also the best price on the board. That doesn't happen often.")
    else:
        parts.append("Nobody is offering a price worth taking.")
    if dark is not None:
        reasons = [f[2:] for f in factors[dark] if f.startswith("+ ")][:2]
        parts.append(f"Dark horse: #{progs[dark]} {names[dark]} at {fmt_odds(odds[dark])} — {', '.join(reasons)}.")
    if wagers:
        kinds = ", ".join(sorted({w['wager_type'].replace('_', ' ').title() for w in wagers}))
        parts.append(f"I'm playing: {kinds}. Confidence {level.lower()}.")
    else:
        parts.append(f"I'll pass on the betting here. Confidence {level.lower()}.")
    if watch:
        spots = "; ".join(f"#{w['selection']} {w['horse_name'] if 'horse_name' in w else ''} at "
                          f"{fmt_odds(w['min_odds'])} or better".replace("  ", " ") for w in watch)
        parts.append(f"Watch the tote: {spots}. I only have the morning line here, so check the board "
                     "before post.")
    if model_kind == "market_baseline":
        parts.append("(Cold-start mode: these numbers are the market baseline until my trained model "
                     "clears out-of-sample validation.)")
    parts.append(CLOSERS[_h(race_key) % len(CLOSERS)])
    return " ".join(parts)


def fair_odds(p: float) -> float:
    return max(1.0 / max(p, 1e-6) - 1.0, 0.0)



