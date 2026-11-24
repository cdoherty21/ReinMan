"""National Weather Service (api.weather.gov) — free, public, no key required.

NWS asks automated clients to send a User-Agent identifying the application. It
provides forecasts and recent observations only (no historical archive), so Rein
Man records weather at prediction time; past weather comes from what was stored.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime

from ..util import parse_iso

UA = os.environ.get("NWS_USER_AGENT", "(rein-man personal handicapping app)")


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/geo+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def hourly_forecast_at(lat: float, lon: float, when_utc: str) -> dict | None:
    """Forecast period covering ``when_utc``: temp_f, precip_pct, description."""
    pts = _get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
    url = pts["properties"].get("forecastHourly")
    if not url:
        return None
    periods = _get(url)["properties"].get("periods", [])
    target = parse_iso(when_utc)
    for p in periods:
        start = datetime.fromisoformat(p["startTime"])
        end = datetime.fromisoformat(p["endTime"])
        if start <= target < end:
            temp = p.get("temperature")
            if p.get("temperatureUnit") == "C" and temp is not None:
                temp = temp * 9 / 5 + 32
            pop = (p.get("probabilityOfPrecipitation") or {}).get("value")
            return {"temp_f": temp, "precip_pct": pop, "weather_desc": p.get("shortForecast")}
    return None
