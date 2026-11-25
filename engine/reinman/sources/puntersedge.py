"""PuntersEdge's North American tote board and final US-TOTE settlements."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..medication import normalize
from ..util import jockey_id, norm_name, parse_iso, parse_odds, parse_post_time, surface_code, to_float, trainer_id
from ..wagertypes import normalize_wager_type
from .tracks import canonical_track_code

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
SYDNEY = ZoneInfo("Australia/Sydney")
NON_THOROUGHBRED = {"QUARTER HORSE", "QH", "HARNESS", "ARABIAN", "MIXED"}


def track_code(row: dict) -> str:
    venue = row.get("venue_canonical") or row.get("venue") or ""
    name = norm_name(venue)
    raw = str(row.get("track_code") or "").upper()
    # Use the same fallback for board and results, which may omit the TVG code.
    return canonical_track_code(venue, raw) or name.replace(" ", "_")


def eligible(row: dict, details: dict) -> bool:
    if row.get("country") != "US" or row.get("category") not in {"horse", "thoroughbred"}:
        return False
    breed = norm_name(details.get("breed") or row.get("breed"))
    conditions = norm_name(details.get("conditions"))
    race_class = norm_name(details.get("race_class"))
    return (breed not in NON_THOROUGHBRED
            and race_class not in NON_THOROUGHBRED
            and "QUARTER HORSE" not in conditions)


def meet_for(row: dict, race_date: str) -> dict:
    code = track_code(row)
    return {"meet_id": f"puntersedge:{code}:{race_date}", "track_id": code,
            "track_name": row.get("venue"), "race_date": race_date, "country": "USA"}


def board_race(row: dict, race_date: str, now: datetime) -> dict | None:
    tote = row.get("tote") or {}
    post = parse_post_time(race_date, row.get("post_time"))
    if not post or not track_code(row) or not eligible(row, tote):
        return None
    read_at = tote.get("read_at")
    fresh = (read_at and timedelta(0) <= now - parse_iso(read_at) <= timedelta(minutes=5)
             and tote.get("status") == "open" and not tote.get("final")
             and parse_iso(post) > now)
    runners, changes = [], []
    for interest in tote.get("runners") or []:
        horses = interest.get("horses") or [interest]
        for horse in horses:
            program = horse.get("program") or horse.get("program_number")
            if program is None and len(horses) == 1:
                program = interest.get("program") or interest.get("number")
            name = horse.get("name")
            if not name or program is None:
                # A partial coupled entry would misrepresent the field.
                return None
            program = str(program).upper()
            scratched = horse.get("scratched") or interest.get("scratched")
            live = fresh and not scratched
            jockey, trainer = horse.get("jockey"), horse.get("trainer")
            runners.append({
                "program_number": program, "horse_id": f"name:{norm_name(name)}", "horse_name": name,
                "jockey_id": jockey_id(jockey), "jockey_name": jockey,
                "trainer_id": trainer_id(trainer), "trainer_name": trainer,
                "post_position": horse.get("post_position"), "weight": horse.get("weight_lb"),
                "medication": normalize(horse.get("medication")),
                "extra": {"medication_report": {"code": normalize(horse.get("medication")), "source": "PuntersEdge"}}
                         if "medication" in horse and horse.get("medication") is not None else None,
                "sire": horse.get("sire"),
                "dam": horse.get("dam"), "dam_sire": horse.get("dam_sire"), "scratched": scratched,
                "ml_odds": parse_odds(interest.get("morning_line")),
                "live_odds": parse_odds(interest.get("win_odds")) if live else None,
                **{pool: interest.get(pool) if live and len(horses) == 1 else None
                   for pool in ("win_pool", "place_pool", "show_pool")},
            })
            if scratched and (reason := horse.get("scratch_reason") or interest.get("scratch_reason")):
                changes.append({"type": "Scratch", "text": f"#{program} {name}: {reason}"})
    if not runners or len({runner["program_number"] for runner in runners}) != len(runners):
        return None
    metres = to_float(tote.get("distance_m"))
    return {
        "race_number": row["race_number"], "post_time": post, "surface": surface_code(tote.get("surface")),
        "distance_yards": round(metres / 0.9144) if metres else None,
        "race_class": tote.get("race_class"), "race_type": tote.get("race_class"), "purse": tote.get("purse"),
        "max_claim": tote.get("claiming_price"), "age_restriction": tote.get("age_restriction"),
        "track_condition": tote.get("track_condition"), "runners": runners, "changes": changes,
        "is_cancelled": tote.get("status") in {"cancelled", "abandoned"},
        "pools": [{"wager_type": normalize_wager_type(p.get("name"), p.get("code"))}
                  for p in tote.get("pools") or []],
    }


def final_result(row: dict) -> dict | None:
    dividends = row.get("dividends") or {}
    if (not track_code(row) or not eligible(row, row) or row.get("status") != "final"
            or (row.get("availability") or {}).get("dividends") == "pending"
            or not any(d.get("product") == "US-TOTE" for d in dividends.get("straight") or [])):
        return None
    placings = row.get("placings") or []
    placed = {(p["number"], p["position"]): p for p in placings}
    field = row.get("runners") or placings
    positions = Counter(f["position"] for f in field if f.get("position"))
    finishers = []
    for runner in field:
        if not runner.get("position") or not runner.get("name"):
            continue
        placing = placed.get((runner["number"], runner["position"]), {})
        finisher = {"program_number": str(runner.get("program") or runner["number"]).upper(),
                    "horse_name": runner["name"], "finish_position": runner["position"],
                    "dead_heat": bool(runner.get("dead_heat") or positions[runner["position"]] > 1)}
        for market in ("win", "place", "show"):
            price = to_float(placing.get(f"{market}_price"))
            finisher[f"{market}_payoff"] = round(price * 2, 2) if price is not None else None
        finishers.append(finisher)

    payoffs = []
    seen = set()
    for entry in (dividends.get("exotics") or []) + (dividends.get("multi_race") or []):
        if entry.get("product") != "US-TOTE" or not entry.get("base") or entry.get("payout") is None:
            continue
        name = entry.get("type")
        selection = (entry.get("selection") or "").replace(",", "-")
        wager_type = normalize_wager_type(name, entry.get("code"))
        key = (wager_type, name, selection, entry["base"], entry["payout"])
        if key not in seen:
            seen.add(key)
            payoffs.append({"wager_type": wager_type, "wager_name": name,
                            "base_amount": entry["base"], "winning_numbers": selection,
                            "payoff_amount": entry["payout"]})
    scratches = {r["name"] for r in field if r.get("name")
                 and r.get("status") in {"scratched", "late_scratching"}}
    scratches.update(r["name"] for r in row.get("deductions") or [] if r.get("name"))
    if not positions[1]:
        return None
    return {"race_number": row["race_number"], "surface": surface_code(row.get("surface")),
            "track_condition": row.get("track_condition"), "finishers": finishers,
            "payoffs": payoffs, "scratches": sorted(scratches)}


class PuntersEdge:
    name = "puntersedge"
    capabilities = dict.fromkeys(
        ("entries", "morning_line", "live_odds", "win_place_show_pools", "scratches",
         "jockey_trainer", "medication", "weight", "pedigree", "track_condition",
         "official_results", "exotic_payoffs"), True)

    def __init__(self, reserve=None):
        self.key = os.environ.get("PUNTERSEDGE_API_KEY", "").strip()
        self.reserve = reserve
        self.cards = {}
        self.settlements = {}
        self.pages = {}

    @property
    def configured(self) -> bool:
        return bool(self.key)

    def _get(self, endpoint: str, params: dict, empty):
        if self.reserve and not self.reserve(endpoint):
            log.warning("PuntersEdge automatic %s budget reached", endpoint)
            return empty
        query = urllib.parse.urlencode({"country": "US", **params})
        request = urllib.request.Request(f"https://api.puntersedge.online/v1/racing/{endpoint}?{query}",
                                         headers={"X-API-Key": self.key, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 402:
                log.warning("PuntersEdge monthly credits exhausted")
                return empty
            raise

    def meets(self, race_date: str) -> list[dict]:
        if race_date not in self.cards:
            cards = {}
            now = datetime.now(ET)
            day = date.fromisoformat(race_date)
            if day >= date(2026, 10, 27) and day <= now.date() + timedelta(days=1):
                payload = self._get("tote", {"date": race_date}, {"races": []})
                for row in payload["races"]:
                    if row["race_date"] != race_date:
                        continue
                    race = board_race(row, race_date, now)
                    if race:
                        meet = meet_for(row, race_date)
                        cards.setdefault(meet["meet_id"], {"meet": meet, "races": []})["races"].append(race)
            self.cards[race_date] = cards
        return [card["meet"] for card in self.cards[race_date].values()]

    def card(self, meet: dict) -> dict:
        return self.cards[meet["race_date"]][meet["meet_id"]]

    def _results_on_sydney_date(self, race_date: str) -> list[dict]:
        if race_date not in self.pages:
            rows = []
            while True:
                page = self._get("results", {"date": race_date, "category": "horse", "status": "final",
                                             "limit": 200, "offset": len(rows)}, [])
                rows.extend(page)
                if len(page) < 200:
                    break
            self.pages[race_date] = rows
        return self.pages[race_date]

    def result_meets(self, race_date: str) -> list[dict]:
        if race_date not in self.settlements:
            groups = defaultdict(dict)
            day = date.fromisoformat(race_date)
            start = datetime.combine(day, datetime.min.time(), ET)
            end = datetime.combine(day + timedelta(days=1), datetime.min.time(), ET)
            first = start.astimezone(SYDNEY).date()
            last = (end - timedelta(microseconds=1)).astimezone(SYDNEY).date()
            if day >= date(2026, 8, 1) and day <= datetime.now(ET).date():
                for sydney_date in (first, last) if first != last else (first,):
                    for row in self._results_on_sydney_date(sydney_date.isoformat()):
                        if not row.get("start_time") or not start <= parse_iso(row["start_time"]) < end:
                            continue
                        result = final_result(row)
                        if result:
                            meet = meet_for(row, race_date)
                            group = groups[meet["meet_id"]]
                            group["meet"] = meet
                            group.setdefault("races", {})[result["race_number"]] = result
            self.settlements[race_date] = groups
        return [group["meet"] for group in self.settlements[race_date].values()]

    def results(self, meet: dict) -> list[dict]:
        self.result_meets(meet["race_date"])
        return list(self.settlements[meet["race_date"]].get(meet["meet_id"], {}).get("races", {}).values())

