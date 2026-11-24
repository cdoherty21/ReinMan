"""The Racing API — North America endpoints (paid regional add-on).

Endpoints (from the provider's published OpenAPI spec, v1.4.x):
  GET /v1/north-america/meets?start_date=&end_date=&limit=&skip=
  GET /v1/north-america/meets/{meet_id}/entries
  GET /v1/north-america/meets/{meet_id}/results
Auth: HTTP Basic (username/password from theracingapi.com dashboard). Rate limit 5 req/s.

What this source does NOT provide (so Rein Man does not pretend to use it):
past-performance lines, speed/pace figures, running positions, beaten lengths,
trouble comments, workouts. Rein Man builds horse/jockey/trainer history from the
results it accumulates; richer data can be added through the canonical importer.

Because the response field shapes for results ordering and odds strings are not
fully specified in the spec, run ``reinman source-check`` against a live response
after subscribing; it validates every assumption this adapter makes.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..util import (norm_name, parse_distance_yards, parse_odds, parse_post_time, person_id,
                    surface_code, to_float, to_int)
from ..wagertypes import normalize_wager_type

BASE = "https://api.theracingapi.com"


class RacingApiNA:
    name = "theracingapi_na"
    capabilities = {
        "entries": True, "morning_line": True, "live_odds": True, "win_place_show_pools": True,
        "scratches": True, "jockey_trainer": True, "equipment": True, "medication": True,
        "weight": True, "pedigree": True, "track_condition": True, "weather": True,
        "official_results": True, "exotic_payoffs": True, "fractional_times": True,
        "past_performances": False, "speed_figures": False, "pace_figures": False,
        "running_positions": False, "trouble_comments": False, "workouts": False,
        "beaten_lengths": False,
    }

    def __init__(self, username: str | None = None, password: str | None = None,
                 min_interval: float = 0.25):
        self.username = username or os.environ.get("RACINGAPI_USERNAME", "")
        self.password = password or os.environ.get("RACINGAPI_PASSWORD", "")
        self.min_interval = min_interval
        self._last = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.username and self.password)

    def _get(self, path: str, params: dict | None = None) -> Any:
        if not self.configured:
            raise RuntimeError("RACINGAPI_USERNAME / RACINGAPI_PASSWORD are not set")
        url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        auth = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        for attempt in range(5):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}",
                                                       "User-Agent": "rein-man/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"The Racing API {e.code} for {path}: "
                                   f"{e.read().decode(errors='replace')[:300]}") from e
        raise RuntimeError(f"The Racing API: retries exhausted for {path}")

    # ------------------------------------------------------------------ meets
    def meets(self, race_date: str, end_date: str | None = None) -> list[dict]:
        out, skip = [], 0
        while True:
            data = self._get("/v1/north-america/meets", {
                "start_date": race_date, "end_date": end_date or race_date, "limit": 100, "skip": skip})
            batch = data.get("meets") or []
            for m in batch:
                out.append({"meet_id": m["meet_id"], "track_id": str(m["track_id"]).upper(),
                            "track_name": m.get("track_name") or m["track_id"],
                            "race_date": m["date"][:10], "country": m.get("country") or "USA",
                            "source": self.name})
            if len(batch) < 100:
                return out
            skip += 100

    # ------------------------------------------------------------------ entries
    def card(self, meet: dict) -> dict:
        data = self._get(f"/v1/north-america/meets/{urllib.parse.quote(meet['meet_id'])}/entries")
        return parse_entries(data, meet)

    def results(self, meet: dict) -> list[dict]:
        data = self._get(f"/v1/north-america/meets/{urllib.parse.quote(meet['meet_id'])}/results")
        return parse_results(data)


# ---------------------------------------------------------------------- parsing
def _race_number(race_key: dict | None) -> tuple[int | None, str]:
    rk = race_key or {}
    return to_int(rk.get("race_number")), (rk.get("day_evening") or "D").upper()


def _pool(pools: list | None, *names: str) -> float | None:
    for p in pools or []:
        n = (p.get("pool_type_name") or "").upper()
        if any(x in n for x in names):
            return to_float(p.get("amount"))
    return None


def _scratched(ind: Any) -> bool:
    s = str(ind or "").strip().upper()
    return s not in {"", "N", "0", "FALSE", "NONE"}


def parse_entries(data: dict, meet: dict) -> dict:
    weather = data.get("weather") or {}
    races = []
    for r in data.get("races") or []:
        num, day_eve = _race_number(r.get("race_key"))
        if num is None:
            continue
        runners = []
        for rn in r.get("runners") or []:
            j, t = rn.get("jockey") or {}, rn.get("trainer") or {}
            prog = str(rn.get("program_number") or rn.get("program_number_stripped") or "").strip()
            if not prog:
                continue
            reg = rn.get("registration_number")
            runners.append({
                "program_number": prog,
                "post_position": to_int(rn.get("post_pos")),
                "horse_id": f"reg:{reg}" if reg else f"name:{norm_name(rn.get('horse_name'))}",
                "horse_name": (rn.get("horse_name") or "").strip(),
                "jockey_id": person_id("j", j.get("id"), j.get("first_name"), j.get("last_name")),
                "jockey_name": " ".join(x for x in (j.get("first_name"), j.get("last_name")) if x) or None,
                "trainer_id": person_id("t", t.get("id"), t.get("first_name"), t.get("last_name")),
                "trainer_name": " ".join(x for x in (t.get("first_name"), t.get("last_name")) if x) or None,
                "sire": rn.get("sire_name"), "dam": rn.get("dam_name"), "dam_sire": rn.get("dam_sire_name"),
                "weight": to_float(rn.get("weight")),
                "equipment": rn.get("equipment"), "medication": rn.get("medication"),
                "claiming": to_int(rn.get("claiming")),
                "ml_odds": parse_odds(rn.get("morning_line_odds")),
                "live_odds": parse_odds(rn.get("live_odds")),
                "win_pool": _pool(rn.get("horse_data_pools"), "WIN", "WN"),
                "place_pool": _pool(rn.get("horse_data_pools"), "PLACE", "PL"),
                "show_pool": _pool(rn.get("horse_data_pools"), "SHOW", "SH"),
                "coupled_type": rn.get("coupled_type"),
                "scratched": _scratched(rn.get("scratch_indicator")),
                "extra": None,
            })
        races.append({
            "race_number": num, "day_evening": day_eve,
            "post_time": parse_post_time(meet["race_date"], r.get("post_time"), r.get("post_time_long"),
                                         r.get("time_zone"), meet.get("timezone")),
            "surface": surface_code(r.get("course_type"), r.get("surface_description")),
            "distance_yards": parse_distance_yards(r.get("distance_description"), r.get("distance_value"),
                                                   r.get("distance_unit")),
            "distance_desc": r.get("distance_description"),
            "race_type": r.get("race_type"), "race_class": r.get("race_class") or r.get("race_type_description"),
            "race_name": r.get("race_name"), "grade": r.get("grade"),
            "purse": to_int(r.get("purse")), "min_claim": to_int(r.get("min_claim_price")),
            "max_claim": to_int(r.get("max_claim_price")),
            "age_restriction": r.get("age_restriction_description") or r.get("age_restriction"),
            "sex_restriction": r.get("sex_restriction_description") or r.get("sex_restriction"),
            "breed": r.get("breed"),
            "pools": [{"wager_type": normalize_wager_type(p.get("pool_code"), p.get("pool_name")),
                       "name": p.get("pool_name"), "min_wager": p.get("minimum_wager_amount"),
                       "race_list": p.get("race_list")} for p in (r.get("race_pools") or [])],
            "track_condition": r.get("track_condition"),
            "weather_desc": weather.get("current_weather_description") or weather.get("forecast_weather_description"),
            "temp_f": to_float(weather.get("forecast_high")),
            "precip_pct": to_float(weather.get("forecast_precipitation")),
            "is_cancelled": bool(r.get("is_cancelled")),
            "changes": [{"type": c.get("type"), "text": c.get("text")} for c in (r.get("changes") or []) if c.get("text")],
            "runners": runners,
        })
    return {"meet": meet, "races": races}


def _time_seconds(td: dict | None) -> float | None:
    if not td:
        return None
    if td.get("time_in_hundredths"):
        v = to_float(td["time_in_hundredths"])
        if v is not None:
            return v / 100.0 if v > 1000 else v
    m, s = td.get("minutes") or 0, td.get("seconds")
    if s is None:
        return None
    frac = (td.get("hundredths") or 0) / 100.0 if td.get("hundredths") is not None else (td.get("fifths") or 0) / 5.0
    return m * 60 + s + frac


def parse_results(data: dict) -> list[dict]:
    out = []
    for r in data.get("races") or []:
        num, _ = _race_number(r.get("race_key"))
        if num is None:
            continue
        runners = r.get("runners") or []
        finishers = []
        # Payoff tiers identify the top finishers even with dead heats:
        # win tier (has win payoff) = 1st; place-only tier = 2nd; show-only tier = 3rd.
        def tier(x: dict) -> str:
            if (x.get("win_payoff") or 0) > 0:
                return "W"
            if (x.get("place_payoff") or 0) > 0:
                return "P"
            if (x.get("show_payoff") or 0) > 0:
                return "S"
            return "-"
        counts = {k: sum(1 for x in runners if tier(x) == k) for k in "WPS"}
        base_pos = {"W": 1, "P": counts["W"] + 1, "S": counts["W"] + counts["P"] + 1}
        for i, rn in enumerate(runners):
            t = tier(rn)
            pos = base_pos[t] if t in base_pos else i + 1
            finishers.append({
                "program_number": str(rn.get("program_number") or rn.get("program_number_stripped") or "").strip() or None,
                "horse_name": (rn.get("horse_name") or "").strip(),
                "finish_position": pos, "win_payoff": rn.get("win_payoff"),
                "place_payoff": rn.get("place_payoff"), "show_payoff": rn.get("show_payoff"),
                "dead_heat": t in counts and counts[t] > 1,
            })
        also = r.get("also_ran")
        if isinstance(also, str):
            also = [a.strip() for a in also.split(",") if a.strip()]
        for a in also or []:
            name = a if isinstance(a, str) else (a or {}).get("horse_name")
            if name:
                finishers.append({"program_number": None, "horse_name": name.strip(),
                                  "finish_position": len(finishers) + 1, "win_payoff": None,
                                  "place_payoff": None, "show_payoff": None, "dead_heat": False,
                                  "also_ran": True})
        payoffs = []
        for p in r.get("payoffs") or []:
            payoffs.append({
                "wager_type": normalize_wager_type(p.get("wager_type"), p.get("wager_name")),
                "wager_name": p.get("wager_name"), "base_amount": to_float(p.get("base_amount")),
                "winning_numbers": p.get("winning_numbers"),
                "payoff_amount": to_float(p.get("payoff_amount")),
                "total_pool": to_float(p.get("total_pool")), "carryover": to_float(p.get("carryover")),
            })
        frac = r.get("fraction") or {}
        out.append({
            "race_number": num,
            "track_condition": r.get("track_condition_description"),
            "surface": surface_code(r.get("surface"), r.get("surface_description")),
            "winning_time": _time_seconds(frac.get("winning_time")),
            "fractions": [_time_seconds(frac.get(f"fraction_{i}")) for i in range(1, 6) if frac.get(f"fraction_{i}")],
            "finishers": finishers, "payoffs": payoffs,
            "scratches": [s for s in (r.get("scratches") or []) if s],
        })
    return out

