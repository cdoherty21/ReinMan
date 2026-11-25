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
from .medication import label as medication_label

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


def _n(count, singular: str, plural: str | None = None) -> str:
    """'1 win', '2 wins', '1 top-three finish', '3 top-three finishes'."""
    count = count or 0
    word = singular if count == 1 else (plural or (singular + "es" if singular.endswith(("sh", "ch", "s")) else singular + "s"))
    return f"{count} {word}"


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
            priority = {"tracked_result": 0, "program": 1, "chart": 2}
            preferred, fallback = (p, current) if priority.get(p.get("layout"), 1) >= priority.get(current.get("layout"), 1) else (current, p)
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
            if comment:
                observations.append({"date": p["date"], "track": p["track"], "race_number": p.get("race_number"),
                                     "source": "result chart" if p.get("layout") == "chart" else "past performance",
                                     "document_id": p.get("document_id"), "comment": comment, "events": labels})
        calls, size, finish = p.get("call_positions"), p.get("field_size"), p.get("finish")
        if calls and _number(size) and size > 1 and _number(calls[0]) and 1 <= calls[0] <= size:
            positions.append((calls[0] - 1) / (size - 1))
            if _number(finish) and 1 <= finish <= size:
                gains.append((calls[0] - finish) / (size - 1))
    profile = {"starts": len(recent), "comments": len(comments), "observations": observations[:8],
               "events": dict(event_counts), "pace_samples": len(positions),
               "record": _sample(recent, lambda p: True),
               "recent_results": [{"date": p["date"], "track": p["track"], "finish": p.get("finish"),
                                   "field_size": p.get("field_size"), "surface": p.get("surface"),
                                   "distance_yards": p.get("distance_yards"), "layout": p.get("layout")}
                                  for p in recent[:6] if _number(p.get("finish"))]}
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
    jockey = runner.get("jockey_id") or jockey_id(runner.get("jockey_name"))
    distance = race.get("distance_yards")
    surface = surface_code(race.get("surface"))
    condition = str(race.get("track_condition") or "").strip().lower()
    comparisons = {
        "pace pressure": _sample(pressure, lambda p: True),
        "today's jockey": _sample(recent, lambda p: jockey and (p.get("jockey_id") or jockey_id(p.get("jockey"))) == jockey),
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
    """Sectioned evidence stored in the immutable prediction snapshot."""
    context = profile.get("current_context") or {}
    record = profile.get("record") or {}
    if not profile.get("starts") and not context:
        return ["No verified running lines available before this race."]
    out = []
    def add(section, text):
        out.append(f"{section} | {text}")

    identity = context.get("identity") or {}
    age_sex = identity.get("age_sex")
    if age_sex:
        match = re.fullmatch(r"(\d+)[/ ]([GCFMHR])", str(age_sex).upper())
        if match:
            sex = {"G": "gelding", "C": "colt", "F": "filly", "M": "mare", "H": "horse", "R": "ridgling"}[match[2]]
            add("Identity", f"{match[1]}-year-old {sex}")
        else:
            add("Identity", f"Reported age/sex: {age_sex}")
    for key in ("owner", "breeder"):
        if identity.get(key):
            add("Identity", f"{key.title()}: {identity[key]}")
    for key in ("sire", "dam", "dam_sire"):
        if context.get(key):
            add("Pedigree", f"{key.replace('_', ' ').title()}: {context[key]}")
    if context.get("research_source"):
        add("Sources", f"Race card — {context['research_source']}")

    if profile.get("starts"):
        add("Recent form", f"{_n(record.get('starts'), 'recorded finish')} · {_n(record.get('wins'), 'win')} · {_n(record.get('itm'), 'top-three finish')}")
        for result in profile.get("recent_results", []):
            field = f" of {result['field_size']}" if result.get("field_size") else ""
            add("Recent form", f"{result['date']} · {result['track']} · finished {result['finish']}{field}")
    else:
        add("Recent form", "Complete prior race finishes are unavailable.")
    figures = context.get("reported_figures") or context.get("beyer_figures") or []
    if figures:
        add("Recent form", "Reported last three Beyer figures (newest first): " + " → ".join("—" if v is None else str(v) for v in figures))
        if len(figures) >= 2 and all(isinstance(v, (int, float)) and v > 0 for v in figures):
            delta = figures[0] - figures[-1]
            add("Recent form", f"Latest versus oldest listed figure: {delta:+g} points.")
        if context.get("beyer_source"):
            add("Sources", f"Published speed figures and connection statistics — {context['beyer_source']}")

    add("Racing behavior", f"{_n(profile.get('comments'), 'documented trip comment')}; {_n(profile.get('pace_samples'), 'start')} with running calls.")
    if profile.get("style"):
        add("Racing behavior", f"Observed style: {profile['style']}.")
    if profile.get("comments", 0) < 3:
        add("Racing behavior", "Insufficient trip evidence to establish a recurring temperament or gate tendency.")
    for tag, count in sorted(profile.get("events", {}).items()):
        add("Racing behavior", f"{tag.capitalize()}: observed in {count} of {_n(profile['comments'], 'documented trip')}.")
    for p in profile.get("observations", []):
        source = f"{p['source']}, document #{p['document_id']}" if p.get("document_id") else p["source"]
        add("Trip history", f"{p['date']} · {p['track']}" + (f" R{p['race_number']}" if p.get("race_number") else "") +
            f" · {source}: “{p['comment']}”")

    race_context = [context.get("track"), {"D": "Dirt", "T": "Turf", "S": "Synthetic"}.get(context.get("surface")),
                    f"{context['distance_yards'] / 220:g} furlongs" if context.get("distance_yards") else None,
                    context.get("condition"), context.get("race_class")]
    if any(race_context):
        add("Today's race", " · ".join(str(value) for value in race_context if value))
    if context.get("post_position"):
        add("Today's race", f"Post {context['post_position']} of {context.get('field_size', '?')} declared runners.")
    if context.get("claiming"):
        add("Today's race", f"Claiming price: ${context['claiming']:,.0f}.")
    for tag, sample in profile.get("comparisons", {}).items():
        add("Race fit", f"{tag.capitalize()}: {_n(sample['wins'], 'win')} and {_n(sample['itm'], 'top-three finish')} in {_n(sample['starts'], 'recorded start')}.")
    if not profile.get("comparisons"):
        add("Race fit", "No comparable recorded finishes to establish track, distance or surface preference.")
    for role in ("trainer", "jockey"):
        if context.get(role):
            add("Connections", f"{role.title()}: {context[role]}")
        stats = context.get(role + "_record")
        if stats:
            add("Connections", f"{role.title()} published season record: {stats['win_pct']}% wins from {_n(stats['starts'], 'start')}" + (f"; {stats['itm_pct']}% top-three finishes." if stats.get("itm_pct") is not None else "."))
    for role, count in (context.get("tracked_wins") or {}).items():
        add("Connections", f"Historical winner archive: {role} recorded {_n(count, 'win')} at this track.")
    if context.get("weight"):
        add("Preparation", f"Assigned weight: {context['weight']:g} lb.")
    if context.get("equipment"):
        add("Preparation", f"Reported equipment: {context['equipment']}.")
    if identity.get("equipment_change"):
        add("Preparation", f"Reported equipment change: {identity['equipment_change']}.")
    if context:
        report = context.get("medication_report") or {}
        med = medication_label(context.get("medication"))
        add("Preparation", f"Medication: {med}" + (f" — {report['source']}" if report.get("source") else "."))
    if context.get("rest_days") is not None:
        add("Preparation", f"Last recorded start: {context['previous_track']}, {_n(context['rest_days'], 'day')} ago.")
    if context.get("travel_miles") is not None:
        add("Preparation", "Same track as its last recorded start." if not context["travel_miles"] else
            f"Shipped about {_n(context['travel_miles'], 'mile')} from its last recorded start.")
    if context.get("workouts_30d"):
        add("Preparation", f"{_n(context['workouts_30d'], 'recorded workout')} in the past 30 days; latest {context['last_work_date']}.")
    else:
        add("Preparation", "Recent workout details unavailable.")
    return out
