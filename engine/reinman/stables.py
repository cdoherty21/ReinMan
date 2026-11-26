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


def enrich(name, year, sire, dam=None):
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
    source = "https://www.wikidata.org/wiki/" + entity_id
    biography = {"name": label(entity), "foaling_year": year, "sire": sire,
                 "source": source, "confidence": "Verified name, foaling year and sire"}
    born = claim(entity, "P569")
    if isinstance(born, dict) and born.get("precision", 0) >= 11:
        biography["foaling_date"] = born.get("time", "").lstrip("+").split("T")[0]
    related = {name: value["id"] for name, prop in
               (("sire", "P22"), ("dam", "P25"), ("reported_sex", "P21"), ("reported_country", "P17"))
               if isinstance((value := claim(entity, prop)), dict) and value.get("id")}
    relatives = request(WIKIDATA, action="wbgetentities", ids="|".join(set(related.values())),
                        props="labels|claims", languages="en").get("entities", {}) if related else {}
    for name, identity in related.items():
        if text := label(relatives.get(identity, {})):
            biography[name] = text
    if dam and biography.get("dam") and norm_name(dam) != norm_name(biography["dam"]):
        return None, None, None
    ancestors = {}
    for parent in ("sire", "dam"):
        relative = relatives.get(related.get(parent), {})
        for relation, prop in (("sire", "P22"), ("dam", "P25")):
            value = claim(relative, prop)
            if isinstance(value, dict) and value.get("id"):
                ancestors[parent+"_"+relation] = value["id"]
    if ancestors:
        entries = request(WIKIDATA, action="wbgetentities", ids="|".join(set(ancestors.values())),
                          props="labels", languages="en").get("entities", {})
        for name, identity in ancestors.items():
            if text := label(entries.get(identity, {})):
                biography[name] = text
    biography["fact_sources"] = {name: source for name in biography if name not in ("source", "confidence")}

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
      SELECT ru.horse_id, ru.horse_name, ru.sire, ru.dam, ru.extra, r.race_date
      FROM runners ru JOIN races r USING(race_id)
      LEFT JOIN stable_enrichment e USING(horse_id)
      LEFT JOIN stable_favorites f USING(horse_id)
      WHERE (e.horse_id IS NULL OR (e.image IS NULL AND e.retry_after<=?))
      AND (ru.sire IS NOT NULL OR json_extract(CASE WHEN json_valid(ru.extra) THEN ru.extra ELSE '{}' END,'$.formfav.sire') IS NOT NULL)
      AND (json_extract(CASE WHEN json_valid(ru.extra) THEN ru.extra ELSE '{}' END,'$.horse_identity.age_sex') IS NOT NULL
        OR json_extract(CASE WHEN json_valid(ru.extra) THEN ru.extra ELSE '{}' END,'$.formfav.age') IS NOT NULL)
      GROUP BY ru.horse_id
      ORDER BY CASE WHEN MAX(r.race_date)>=date('now') THEN 0
                    WHEN f.horse_id IS NOT NULL THEN 1 ELSE 2 END,
               MAX(r.race_date) DESC LIMIT ?
    """, [stamp, min(5, max(0, limit))])
    acquired = 0
    attempted = 0
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
            entity_id, biography, image = enrich(row["horse_name"], year, row["sire"] or form.get("sire"), row["dam"] or form.get("dam"))
        except (OSError, ValueError, KeyError):
            # A service outage stops this run instead of retrying against a throttled source.
            break
        attempted += 1
        acquired += bool(image)
        db.sync("stable_enrichment", [{"horse_id": row["horse_id"], "wikidata_id": entity_id,
                 "biography": json.dumps(biography) if biography else None,
                 "image": json.dumps(image) if image else None, "status": "verified" if entity_id else "unverified",
                 "retry_after": (now + timedelta(days=30)).isoformat(), "retrieved_at": stamp}], ["horse_id"])
    return {"attempted": attempted, "photographs": acquired}


def photo_catalog():
    """One bounded public query finds Wikimedia photographs across individual horses."""
    query = """SELECT DISTINCT ?horse ?name ?birth ?sireLabel ?image WHERE {
      VALUES ?type { wd:Q726 wd:Q2442470 wd:Q26401003 }
      ?horse wdt:P31 ?type; wdt:P18 ?image; wdt:P569 ?birth; wdt:P22 ?sire; rdfs:label ?name.
      FILTER(LANG(?name)="en")
      ?sire rdfs:label ?sireLabel. FILTER(LANG(?sireLabel)="en")
    } LIMIT 12000"""
    req = urllib.request.Request("https://query.wikidata.org/sparql?" +
        urllib.parse.urlencode({"query": query, "format": "json"}),
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)["results"]["bindings"]


def catalog_identity(row, entries, historical=False):
    name = norm_name(row["horse_name"])
    sire = norm_name(row.get("sire"))
    if not name or not sire:
        return None
    matches = {}
    for item in entries:
        try:
            year = int(item["birth"]["value"][:4])
            if norm_name(item["name"]["value"]) != name or norm_name(item["sireLabel"]["value"]) != sire:
                continue
            if historical:
                age = int(row["race_date"][:4])-year
                if not 2 <= age <= 18:
                    continue
            elif year != row.get("year"):
                continue
            matches[item["horse"]["value"]] = item
        except (KeyError, ValueError, TypeError):
            continue
    return next(iter(matches.values())) if len(matches)==1 else None


def refresh_photos(db, limit=40):
    """Match the full local directory to a public photo catalog, then verify licenses."""
    if not quota.may(db, "background"):
        return {"deferred": "D1 budget"}
    try:
        entries = photo_catalog()
    except (OSError, ValueError, KeyError):
        return {"deferred": "Wikidata catalog unavailable"}
    by_name = {}
    for item in entries:
        by_name.setdefault(norm_name(item.get("name", {}).get("value")), []).append(item)
    runners = db.query("""SELECT ru.horse_id,ru.horse_name,
      COALESCE(ru.sire,json_extract(CASE WHEN json_valid(ru.extra) THEN ru.extra ELSE '{}' END,'$.formfav.sire')) AS sire,
      json_extract(CASE WHEN json_valid(ru.extra) THEN ru.extra ELSE '{}' END,'$.horse_identity.age_sex') AS age_sex,
      json_extract(CASE WHEN json_valid(ru.extra) THEN ru.extra ELSE '{}' END,'$.formfav.age') AS age,
      MAX(r.race_date) AS race_date
      FROM runners ru JOIN races r USING(race_id) LEFT JOIN stable_enrichment e USING(horse_id)
      WHERE e.image IS NULL AND (ru.sire IS NOT NULL OR ru.extra IS NOT NULL)
      GROUP BY ru.horse_id,ru.horse_name,sire,age_sex,age ORDER BY race_date DESC""")
    archive = db.query("""SELECT 'archive:'||w.horse_key AS horse_id,w.horse_name,
      json_extract(CASE WHEN json_valid(w.data) THEN w.data ELSE '{}' END,'$.winnerssire') AS sire,
      MAX(w.race_date) AS race_date FROM historical_winners w LEFT JOIN stable_enrichment e ON e.horse_id='archive:'||w.horse_key
      WHERE e.image IS NULL GROUP BY w.horse_key,w.horse_name,sire ORDER BY race_date DESC""")
    matches = {}
    conflicts = set()
    archive_sires = {}
    for row in archive:
        if row.get("sire"):
            archive_sires.setdefault(row["horse_id"],set()).add(norm_name(row["sire"]))
    conflicts.update(key for key,sires in archive_sires.items() if len(sires)>1)
    for historical, records in ((False,runners),(True,archive)):
        for row in records:
            if not historical:
                try:
                    age = re.fullmatch(r"(\d+)[/ ]([GCFMHR])", str(row.get("age_sex") or "").upper())
                    years = int(age[1]) if age else int(row.get("age") or 0)
                    row["year"] = int(row["race_date"][:4])-years if 0 < years < 40 else None
                except (ValueError, TypeError, AttributeError):
                    continue
            match = catalog_identity(row,by_name.get(norm_name(row["horse_name"]),[]),historical)
            if not match:
                continue
            key = row["horse_id"]
            if key in matches and matches[key][0]["horse"]["value"] != match["horse"]["value"]:
                conflicts.add(key)
            matches[key] = (match,historical)
    acquired = attempted = 0
    now = datetime.now(timezone.utc)
    for key,(item,historical) in matches.items():
        if key in conflicts or attempted >= min(40,max(0,limit)) or not quota.may(db,"background"):
            continue
        entity = item["horse"]["value"].rsplit("/",1)[-1]
        filename = urllib.parse.unquote(item["image"]["value"].rsplit("/",1)[-1])
        attempted += 1
        try:
            pages = request(COMMONS,action="query",titles="File:"+filename,prop="imageinfo",iiprop="url|extmetadata",iiurlwidth=320).get("query",{}).get("pages",{})
            image = next((img for p in pages.values() for info in p.get("imageinfo",[]) if (img:=image_metadata(info,entity))),None)
        except (OSError,ValueError,KeyError):
            break
        if not image:
            continue
        confidence = "Verified name, reported sire and race-date consistency" if historical else "Verified name, foaling year and sire"
        image["confidence"] = confidence
        old = db.one("SELECT biography FROM stable_enrichment WHERE horse_id=?", [key])
        biography = old.get("biography") if old else None
        if not biography:
            biography = json.dumps({"name":item["name"]["value"],"sire":item["sireLabel"]["value"],
                "foaling_year":int(item["birth"]["value"][:4]),"source":"https://www.wikidata.org/wiki/"+entity,"confidence":confidence})
        db.sync("stable_enrichment",[{"horse_id":key,"wikidata_id":entity,"biography":biography,"image":json.dumps(image),
            "status":"verified","retry_after":(now+timedelta(days=30)).isoformat(),"retrieved_at":now.isoformat()}],["horse_id"])
        acquired += 1
    return {"catalog_records":len(entries),"matched":len(set(matches)-conflicts),"attempted":attempted,"photographs":acquired}
