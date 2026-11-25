"""Horse-specific racing observations, reconstructed from durable running lines.

A chart comment records an event, not a diagnosis or permanent temperament.
All summaries use races strictly before the target race date. Missing comments
are not counted as evidence that an event did not happen.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date

from .util import jockey_id, surface_code

PATTERNS = {
    "gate difficulty": r"\b(?:broke (?:slow|slowly|poorly)|slow (?:start|break)|off slowly|stumbled (?:at|start)|dwelt|hesitated|fractious(?: in gate)?|refused to load)\b",
    "erratic movement": r"\b(?:lugged (?:in|out)|bore (?:in|out)|ducked (?:in|out)|veered|drifted (?:in|out))\b",
    "keen early": r"\b(?:rank|keen|pulled hard|fought restraint|hard to settle)\b",
    "traffic trouble": r"\b(?:blocked|steadied|checked|boxed in|no room|tight quarters|waited for room)\b",
    "contact": r"\b(?:bumped|brushed|clipped heels|crowded|pinched back)\b",
    "pace pressure": r"\b(?:dueled|duelled|pressed (?:the )?pace|contested (?:the )?pace|pressed (?:the )?leader)\b",
    "late response": r"\b(?:rallied|closed strongly|finished strongly|surged|gained late|responded)\b",
    "weakened late": r"\b(?:tired|weakened|faded|gave way|empty late)\b",
    "wide trip": r"\b(?:wide|four wide|three wide|[2-9]w)\b",
}
FEATURES = [
    "profile_early_position", "profile_position_gain", "profile_gate_event_rate",
    "profile_traffic_event_rate", "profile_pressure_itm", "profile_same_jockey_itm",
    "profile_similar_distance_itm", "profile_same_condition_itm",
]


def _events(comment: str) -> list[str]:
    events = []
    for label, pattern in PATTERNS.items():
        for match in re.finditer(pattern, comment, re.I):
            # Ignore explicit negation immediately preceding an observation.
            if re.search(r"\b(?:no|not|never|without)\s+(?:\w+\s+){0,2}$", comment[:match.start()], re.I):
                continue
            events.append(label)
            break
    return events


def _valid_date(value, before):
    try:
        return date.fromisoformat(value) < date.fromisoformat(before)
    except (TypeError, ValueError):
        return False


def _number(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def _sample(lines, predicate):
    matching = [p for p in lines if predicate(p) and _number(p.get("finish")) and p["finish"] > 0]
    return {"starts": len(matching), "wins": sum(p["finish"] == 1 for p in matching),
            "itm": sum(p["finish"] <= 3 for p in matching)}


def build(lines: list[dict], race: dict, runner: dict) -> tuple[dict, dict]:
    before = race["race_date"]
    unique = {}
    for p in lines:
        if not _valid_date(p.get("date"), before) or not p.get("track"):
            continue
        key = (p["date"], p["track"])
        current = unique.get(key)
        # Prefer the more complete official chart when both sources cover one start.
        if current is None:
            unique[key] = dict(p)
        else:
            preferred, fallback = (p, current) if p.get("layout") == "chart" else (current, p)
            unique[key] = {**fallback, **{k: v for k, v in preferred.items() if v is not None}}
    recent = sorted(unique.values(), key=lambda p: p["date"], reverse=True)[:20]
    comments, observations, event_counts = [], [], Counter()
    positions, gains = [], []
    pressure = []
    for p in recent:
        comment = str(p.get("comment") or "").strip()
        if comment:
            comments.append(p)
            labels = _events(comment)
            event_counts.update(labels)
            if "pace pressure" in labels:
                pressure.append(p)
            if labels:
                observations.append({"date": p["date"], "track": p["track"], "race_number": p.get("race_number"),
                                     "source": "result chart" if p.get("layout") == "chart" else "past performance",
                                     "document_id": p.get("document_id"), "comment": comment, "events": labels})
        calls, size, finish = p.get("call_positions"), p.get("field_size"), p.get("finish")
        if calls and _number(size) and size > 1 and _number(calls[0]) and 1 <= calls[0] <= size:
            positions.append((calls[0] - 1) / (size - 1))
            if _number(finish) and 1 <= finish <= size:
                gains.append((calls[0] - finish) / (size - 1))
    profile = {"starts": len(recent), "comments": len(comments), "observations": observations[:8],
               "events": dict(event_counts), "pace_samples": len(positions)}
    features = {}
    if len(positions) >= 3:
        average = sum(positions) / len(positions)
        features["profile_early_position"] = average
        profile["style"] = "front-running" if average <= .25 else "stalking" if average <= .5 else "closing/held up"
        if max(positions) - min(positions) >= .65:
            profile["style"] = "variable early positioning"
    if len(gains) >= 3:
        features["profile_position_gain"] = sum(gains) / len(gains)
    if len(comments) >= 3:
        for tag, feature in (("gate difficulty", "profile_gate_event_rate"), ("traffic trouble", "profile_traffic_event_rate")):
            features[feature] = event_counts[tag] / len(comments)
    jockey = jockey_id(runner.get("jockey_name"))
    distance = race.get("distance_yards")
    surface = surface_code(race.get("surface"))
    condition = str(race.get("track_condition") or "").strip().lower()
    comparisons = {
        "pace pressure": _sample(pressure, lambda p: True),
        "today's jockey": _sample(recent, lambda p: jockey and jockey_id(p.get("jockey")) == jockey),
        "similar distance": _sample(recent, lambda p: _number(distance) and _number(p.get("distance_yards")) and abs(p["distance_yards"] - distance) <= 110),
        "today's surface": _sample(recent, lambda p: surface and surface_code(p.get("surface")) == surface),
        "today's track condition": _sample(recent, lambda p: condition and str(p.get("condition") or "").strip().lower() == condition),
        "today's track": _sample(recent, lambda p: p.get("track") == race.get("track_id")),
    }
    profile["comparisons"] = {k: v for k, v in comparisons.items() if v["starts"]}
    for label, feature in (
        ("pace pressure", "profile_pressure_itm"), ("today's jockey", "profile_same_jockey_itm"),
        ("similar distance", "profile_similar_distance_itm"), ("today's track condition", "profile_same_condition_itm"),
    ):
        sample = comparisons[label]
        if sample["starts"] >= 3:
            # Small samples are shrunk toward an uninformative ITM prior.
            features[feature] = (sample["itm"] + 1.5) / (sample["starts"] + 4)
    return profile, features


def evidence(profile: dict) -> list[str]:
    """Human-readable profile stored with each prediction snapshot."""
    out = ([f"{profile['starts']} recent recorded starts; {profile['comments']} with trip comments."]
           if profile.get("starts") else ["No verified running lines available before this race."])
    if profile.get("starts") and profile["comments"] < 3:
        out.append("Behavioral evidence is limited; individual incidents may be situational.")
    context = profile.get("current_context") or {}
    for key in ("trainer", "jockey", "sire", "dam", "equipment", "medication"):
        if context.get(key):
            out.append(f"Current {key}: {context[key]}.")
    if context.get("rest_days") is not None:
        out.append(f"Last recorded start: {context['previous_track']}, {context['rest_days']} days ago.")
    if context.get("travel_miles") is not None:
        out.append(f"Distance between recorded race venues: {context['travel_miles']} miles.")
    if context.get("workouts_30d"):
        out.append(f"{context['workouts_30d']} recorded workouts in the last 30 days; latest {context['last_work_date']}.")
    if profile.get("style"):
        out.append(f"Observed style: {profile['style']} ({profile['pace_samples']} starts with calls).")
    for label, count in sorted(profile["events"].items()):
        out.append(f"{label.capitalize()}: {count} of {profile['comments']} documented trips.")
    for label, sample in profile["comparisons"].items():
        out.append(f"{label.capitalize()}: {sample['wins']} wins, {sample['itm']} top-three finishes in {sample['starts']} recorded starts.")
    for p in profile["observations"]:
        source = f"{p['source']}, document #{p['document_id']}" if p.get("document_id") else p["source"]
        out.append(f"{p['date']} · {p['track']}" + (f" R{p['race_number']}" if p.get("race_number") else "") +
                   f" · {source}: “{p['comment']}”")
    return out
