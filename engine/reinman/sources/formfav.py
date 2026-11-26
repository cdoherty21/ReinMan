"""Free FormFav race-form enrichment for verified US thoroughbred cards."""
from __future__ import annotations

import json
import logging
import math
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .. import quota
from ..util import norm_name, now_iso, parse_iso
from .tracks import canonical_track_code

log = logging.getLogger(__name__)
BASE = "https://api.formfav.com/v1"
GROUPS = {"overall": "career", "track": "track", "distance": "distance",
          "trackDistance": "track_distance", "firstUp": "first_up", "secondUp": "second_up"}
FEATURES = [f"formfav_{group}_{metric}" for group in GROUPS.values()
            for metric in ("starts_log", "win_rate", "place_rate")]


class FormFav:
    def __init__(self, key=None):
        self.key = key or os.getenv("FORMFAV_API_KEY")
        self.last_request = None
        self.remaining = 200  # conservative ceiling per daily run
        self.stopped = False

    def get(self, path, **params):
        if not self.key or self.stopped or self.remaining <= 0:
            return {}
        if self.last_request is not None:
            time.sleep(max(0, 1.1 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        self.remaining -= 1
        query = urlencode(dict(country="us", race_code="gallops",
                               timezone="America/New_York", **params))
        request = Request(f"{BASE}/{path}?{query}", headers={"X-API-Key": self.key})
        try:
            with urlopen(request, timeout=15) as response:
                remaining = response.headers.get("X-RateLimit-Remaining-Day")
                if remaining and remaining.isdigit():
                    self.remaining = min(self.remaining, int(remaining))
                return json.load(response)
        except HTTPError as error:
            log.warning("FormFav %s HTTP %s", path, error.code)
            if error.code != 404:
                self.stopped = True
        except (URLError, TimeoutError, ValueError):
            log.warning("FormFav %s unavailable", path)
            self.stopped = True
        return {}


def snapshot(runner, race_date, observed_at):
    fields = ("age", "sex", "sire", "dam", "country", "careerPrizeMoney", "form",
              "last20Starts", "stats", "weight", "claim", "jockey", "trainer", "barrier")
    return {"source": "FormFav", "race_date": race_date, "observed_at": observed_at,
            **{field: runner[field] for field in fields if runner.get(field) is not None}}


def model_features(data):
    """Use published counts, with missing and zero-start samples kept distinct."""
    stats = data.get("stats") or {}
    values = {}
    for key, group in GROUPS.items():
        record = stats.get(key) or {}
        starts = record.get("starts")
        if type(starts) is not int or starts < 0:
            continue
        values[f"formfav_{group}_starts_log"] = math.log1p(starts)
        if starts:
            for field, metric in (("wins", "win_rate"), ("places", "place_rate")):
                count = record.get(field)
                if type(count) is int and 0 <= count <= starts:
                    values[f"formfav_{group}_{metric}"] = count / starts
    return values


def enrich(db, race_date, client):
    """Conservatively match venue, race, post time and every active horse."""
    if not client.key:
        return {"skipped": "FORMFAV_API_KEY missing"}
    races = db.query("SELECT race_id, track_id, race_number, post_time FROM races "
                     "WHERE race_date=? AND status='scheduled' AND post_time>?",
                     [race_date, now_iso()])
    if not races:
        return {"matched_races": 0, "runners_updated": 0}
    by_track = {}
    for race in races:
        by_track.setdefault((race["track_id"], race["race_number"]), []).append(race)
    meetings = client.get("form/meetings", date=race_date).get("meetings", [])
    matched = updated = 0
    for meeting in meetings:
        if meeting.get("country") != "us" or meeting.get("abandoned") or not meeting.get("slug"):
            continue
        track = canonical_track_code(meeting.get("track"))
        for card in meeting.get("races") or []:
            if client.stopped or not client.remaining or not quota.may(db, "standard"):
                return {"matched_races": matched, "runners_updated": updated, "deferred": True}
            candidates = by_track.get((track, card.get("raceNumber")), [])
            if len(candidates) != 1 or card.get("abandoned"):
                continue
            race = candidates[0]
            try:
                if abs((parse_iso(card["startTime"]) - parse_iso(race["post_time"])).total_seconds()) > 900:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            payload = client.get("form", date=race_date, track=meeting["slug"], race=card["raceNumber"])
            captured = now_iso()
            if not payload or payload.get("abandoned") or parse_iso(captured) >= parse_iso(race["post_time"]):
                continue
            if payload.get("raceNumber") != card["raceNumber"] or canonical_track_code(payload.get("track")) != track:
                continue
            local = db.query("SELECT * FROM runners WHERE race_id=?", [race["race_id"]])
            remote = {(str(r.get("number")), norm_name(r.get("name"))): r for r in payload.get("runners", [])}
            active = {(r["program_number"], norm_name(r["horse_name"])) for r in local if not r["scratched"]}
            remote_active = {key for key, r in remote.items() if not r.get("scratched")}
            if not active or active != remote_active:
                continue
            matched += 1
            for runner in local:
                match = remote.get((runner["program_number"], norm_name(runner["horse_name"])))
                if not match:
                    continue
                extra = json.loads(runner["extra"]) if runner.get("extra") else {}
                data = snapshot(match, race_date, captured)
                old = extra.get("formfav") or {}
                if {k: v for k, v in old.items() if k != "observed_at"} == {k: v for k, v in data.items() if k != "observed_at"}:
                    continue
                extra["formfav"] = data
                db.execute("UPDATE runners SET extra=? WHERE race_id=? AND program_number=?",
                           [json.dumps(extra), race["race_id"], runner["program_number"]])
                updated += 1
    return {"matched_races": matched, "runners_updated": updated}
