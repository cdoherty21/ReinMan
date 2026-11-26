"""Bounded, conservative Wikidata/Commons enrichment for permanent horse IDs."""
from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from .util import norm_name
from . import quota

USER_AGENT = "ReinMan/1.0 (https://github.com/cdoherty21/ReinMan)"
WIKIDATA = "https://www.wikidata.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"


def request(endpoint, **params):
    url = endpoint + "?" + urllib.parse.urlencode({"format": "json", **params})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def claim(entity, key):
    for statement in entity.get("claims", {}).get(key, []):
        if statement.get("rank") == "deprecated":
            continue
        value = statement.get("mainsnak", {}).get("datavalue", {}).get("value")
        if value is not None:
            return value
    return None


def label(entity):
    return entity.get("labels", {}).get("en", {}).get("value", "")


def birth_year(entity):
    time = claim(entity, "P569")
    match = re.match(r"^\+(\d{4})-", time.get("time", "")) if isinstance(time, dict) else None
    return int(match[1]) if match else None


def matches(entity, name, year, sire, father):
    """Name alone is never adequate. Require birth year AND independently reported sire."""
    return bool(name and year and sire and norm_name(label(entity)) == norm_name(name)
                and birth_year(entity) == year and norm_name(label(father)) == norm_name(sire))


def image_metadata(info, entity_id):
    metadata = info.get("extmetadata") or {}
    def value(key):
        return html.unescape(re.sub(r"<[^>]+>", "", metadata.get(key, {}).get("value", ""))).strip()
    license_name = value("LicenseShortName")
    creator = value("Artist")
    url = info.get("thumburl")
    source = info.get("descriptionurl")
    if not re.fullmatch(r"(?:CC BY(?:-SA)? [1-4]\.0|CC0|Public domain)", license_name, re.I):
        return None
    if not creator or not url or not url.startswith("https://upload.wikimedia.org/") or not source or not source.startswith("https://commons.wikimedia.org/"):
        return None
    return {"thumbnail": url, "url": info.get("url"), "source": source, "creator": creator,
            "license": license_name, "license_url": value("LicenseUrl"), "wikidata_id": entity_id,
            "confidence": "Verified name, foaling year and sire"}


def enrich(name, year, sire):
    if not year or not sire:
        return None, None, None
    search = request(WIKIDATA, action="wbsearchentities", search=name, language="en", limit=5)
    ids = [item["id"] for item in search.get("search", [])]
    if not ids:
        return None, None, None
    entities = request(WIKIDATA, action="wbgetentities", ids="|".join(ids), props="labels|claims", languages="en").get("entities", {})
    matches_found = []
    for entity_id, entity in entities.items():
        if norm_name(label(entity)) != norm_name(name) or birth_year(entity) != year:
            continue
        father_id = claim(entity, "P22")
        if not isinstance(father_id, dict) or not father_id.get("id"):
            continue
        father = request(WIKIDATA, action="wbgetentities", ids=father_id["id"], props="labels", languages="en").get("entities", {}).get(father_id["id"], {})
        if matches(entity, name, year, sire, father):
            matches_found.append((entity_id, entity))
    if len(matches_found) != 1:
        return None, None, None
    entity_id, entity = matches_found[0]
    biography = {"name": label(entity), "foaling_year": year, "sire": sire,
                 "source": "https://www.wikidata.org/wiki/" + entity_id, "confidence": "Verified name, foaling year and sire"}
    image = None
    file = claim(entity, "P18")
    if isinstance(file, str):
        pages = request(COMMONS, action="query", titles="File:" + file, prop="imageinfo", iiprop="url|extmetadata", iiurlwidth=320).get("query", {}).get("pages", {})
        for page in pages.values():
            for info in page.get("imageinfo", []):
                image = image_metadata(info, entity_id)
                if image:
                    break
    return entity_id, biography, image


def refresh(db, limit=5):
    if not quota.may(db, "background"):
        return {"deferred": "D1 budget"}
    now = datetime.now(timezone.utc)
    stamp = now.isoformat()
    # Prioritize recently entered horses and the personal watchlist. One attempt per cooldown.
    candidates = db.query("""
      SELECT ru.horse_id, ru.horse_name, ru.sire, ru.extra, r.race_date
      FROM runners ru JOIN races r USING(race_id)
      LEFT JOIN stable_enrichment e USING(horse_id)
      LEFT JOIN stable_favorites f USING(horse_id)
      WHERE (e.horse_id IS NULL OR (e.image IS NULL AND e.retry_after<=?))
      GROUP BY ru.horse_id
      ORDER BY CASE WHEN MAX(r.race_date)>=date('now') THEN 0
                    WHEN f.horse_id IS NOT NULL THEN 1 ELSE 2 END,
               MAX(r.race_date) DESC LIMIT ?
    """, [stamp, min(5, max(0, limit))])
    acquired = 0
    for row in candidates:
        if not quota.may(db, "background"):
            break
        try:
            extra = json.loads(row["extra"] or "{}")
            identity = extra.get("horse_identity") or {}
            form = extra.get("formfav") or {}
            age = re.fullmatch(r"(\d+)[/ ]([GCFMHR])", str(identity.get("age_sex", "")).upper())
            reported_age = int(age[1]) if age else form.get("age")
            year = int(row["race_date"][:4]) - reported_age if type(reported_age) is int and 0 < reported_age < 40 else None
            entity_id, biography, image = enrich(row["horse_name"], year, row["sire"] or form.get("sire"))
        except (OSError, ValueError, KeyError):
            # A service outage stops this run instead of retrying against a throttled source.
            break
        acquired += bool(image)
        db.sync("stable_enrichment", [{"horse_id": row["horse_id"], "wikidata_id": entity_id,
                 "biography": json.dumps(biography) if biography else None,
                 "image": json.dumps(image) if image else None, "status": "verified" if entity_id else "unverified",
                 "retry_after": (now + timedelta(days=30)).isoformat(), "retrieved_at": stamp}], ["horse_id"])
    return {"attempted": len(candidates), "photographs": acquired}
