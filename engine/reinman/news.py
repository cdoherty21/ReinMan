"""Free syndicated racing headlines, retained with timestamps and original links."""
from __future__ import annotations

import logging
import os
import re
import urllib.request
import urllib.error
from urllib.parse import urlencode, urlsplit
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from .db import Database
from .util import norm_name, now_iso, stable_hash, to_iso

log = logging.getLogger(__name__)


def google_feed(query: str) -> str:
    return "https://news.google.com/rss/search?" + urlencode({
        "q": query + " when:7d", "hl": "en-US", "gl": "US", "ceid": "US:en",
    })


# Official syndicated sources plus scoped discovery feeds for publications that
# do not expose a usable RSS endpoint. Google News is the transport; its <source>
# element identifies the original publisher.
PUBLICATIONS = (
    ("BloodHorse", "bloodhorse.com"),
    ("Paulick Report", "paulickreport.com"),
    ("Daily Racing Form", "drf.com"),
    ("Racing Post", "racingpost.com"),
    ("Horse Racing Nation", "horseracingnation.com"),
    ("Equibase", "equibase.com"),
    ("NYRA", "nyra.com"),
    ("Keeneland", "keeneland.com"),
    ("Churchill Downs", "churchilldowns.com"),
    ("Santa Anita", "santaanita.com"),
    ("Del Mar", "dmtc.com"),
    ("Gulfstream Park", "gulfstreampark.com"),
    ("HISA", "hisaus.org"),
)
TOPICS = (
    ("Racing news", "thoroughbred horse racing"),
    ("Training and barn notes", 'thoroughbred (workout OR breezing OR "barn notes" OR trainer)'),
    ("Horse health and scratches", "thoroughbred (injury OR scratch OR veterinarian OR recovery)"),
    ("Equipment and travel", 'thoroughbred (blinkers OR equipment OR shipping OR layoff)'),
)
FEEDS = (
    ("Thoroughbred Daily News", "https://www.thoroughbreddailynews.com/feed/"),
    ("America's Best Racing", "https://www.americasbestracing.net/rss.xml"),
    ("National Thoroughbred Racing Association", "https://www.ntra.com/feed/"),
) + tuple((f"Google News: {name}", google_feed("site:" + domain)) for name, domain in PUBLICATIONS) + tuple(
    (f"Google News: {name}", google_feed(query)) for name, query in TOPICS)



def parse_feed(xml: bytes, source: str, observed: str) -> list[dict]:
    root = ElementTree.fromstring(xml)
    if root.tag != "rss":
        raise ValueError("Expected an RSS feed")
    rows = []
    for item in root.findall("./channel/item")[:100]:
        title = " ".join((item.findtext("title") or "").split())
        url = (item.findtext("link") or "").strip()
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
            if published.tzinfo is None:
                continue
            stamp = to_iso(published)
        except (TypeError, ValueError, OverflowError):
            continue
        publisher = " ".join((item.findtext("source") or "").split()) if source.startswith("Google News") else ""
        if publisher and title.endswith(" - " + publisher):
            title = title[:-(len(publisher) + 3)].strip()
        if title and url.startswith("https://") and stamp <= observed:
            rows.append({"url": url, "source": publisher or source, "title": title[:400],
                         "published_at": stamp, "first_seen_at": observed})
    return rows


class FeedRateLimited(Exception):
    pass


def _fetch_feed(source: str, url: str, now: str) -> list[dict] | None:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "ReinMan/1.0 RSS reader",
                                                        "Accept": "application/rss+xml, application/xml, text/xml"})
        with urllib.request.urlopen(request, timeout=12) as response:
            content = response.read(2_000_001)
        if len(content) > 2_000_000:
            raise ValueError("Feed exceeds size limit")
        return parse_feed(content, source, now)
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise FeedRateLimited(source) from error
        log.warning("RSS feed %s unavailable: %s", source, error)
        return None
    except Exception as error:
        log.warning("RSS feed %s unavailable: %s", source, error)
        return None


def refresh(db: Database) -> dict:
    """Poll every three hours, including an immediate poll for changed feed settings."""
    now = now_iso()
    cutoff = to_iso(datetime.now(timezone.utc) - timedelta(hours=3))
    feeds = FEEDS + tuple(("Configured RSS", url.strip()) for url in
                          os.getenv("REINMAN_NEWS_FEEDS", "").split(",") if url.strip().startswith("https://"))[:4]
    signature = stable_hash(feeds)
    if db.scalar("SELECT COUNT(*) FROM job_runs WHERE job='news_poll' AND started_at>=? AND details=?",
                 [cutoff, signature]):
        return {"skipped": "recent news poll"}
    db.insert("job_runs", {"job": "news_poll", "started_at": now, "finished_at": now,
                           "status": "reserved", "details": signature})
    db.execute("CREATE TABLE IF NOT EXISTS racing_news (url TEXT PRIMARY KEY, source TEXT NOT NULL, "
               "title TEXT NOT NULL, published_at TEXT NOT NULL, first_seen_at TEXT NOT NULL)")
    results = {}
    unique = {}
    throttled = set()
    for source, url in feeds:
        host = urlsplit(url).hostname
        if host in throttled:
            results[source] = "rate limited"
            continue
        try:
            rows = _fetch_feed(source, url, now)
        except FeedRateLimited:
            throttled.add(host)
            results[source] = "rate limited"
            log.warning("News feed host %s rate limited; deferring remaining requests", host)
            continue
        results[source] = "unavailable" if rows is None else len(rows)
        for row in rows or []:
            unique.setdefault(row["url"], row)
    db.insert_many("racing_news", unique.values(), verb="INSERT OR IGNORE")
    return {"feeds": results, "unique_headlines": len(unique)}


def recent(db: Database, asof: str) -> list[dict]:
    cutoff = to_iso(datetime.fromisoformat(asof.replace("Z", "+00:00")) - timedelta(days=7))
    try:
        return db.query("SELECT * FROM racing_news WHERE published_at>=? AND published_at<=? "
                        "AND first_seen_at<=? ORDER BY published_at DESC LIMIT 2000",
                        [cutoff, asof, asof])
    except Exception as error:
        log.warning("News archive unavailable: %s", error)
        return []


_RACING_CONTEXT = re.compile(r"\b(?:stakes|derby|oaks|breeders cup|workout|breeze[ds]?|breezing|jogging|jockey|trainer|gallop|scratch(?:ed|es)?|racehorse|racing|layoff)\b", re.I)
_PRIORITY = re.compile(r"\b(?:scratch(?:ed|es)?|injur(?:y|ed)|abscess|workout|breeze[ds]?|breezing|gallop|jogging|layoff|blinkers|equipment|shipping|trainer|jockey|recovery|returns?)\b", re.I)


def relevant(headlines: list[dict], runner: dict, pedigree: dict) -> list[dict]:
    """Prefer direct horse mentions and actionable topics; show evidence as headlines."""
    aliases = []
    for role, name in (
        ("horse", runner.get("horse_name")), ("trainer", runner.get("trainer_name")),
        ("jockey", runner.get("jockey_name")), ("sire", pedigree.get("sire")),
        ("dam", pedigree.get("dam")), ("dam_sire", pedigree.get("dam_sire")),
    ):
        raw = re.sub(r"\s*\([^)]+\)$", "", name or "")
        clean = norm_name(raw)
        if len(clean.split()) >= 2 and len(clean) >= 7:
            aliases.append((role, clean))
            if "," in raw and role in {"trainer", "jockey"}:
                last, rest = raw.split(",", 1)
                aliases.append((role, norm_name(rest + " " + last)))
        elif role == "horse" and len(clean) >= 6:
            aliases.append((role, clean))
    scores = []
    seen = set()
    for row in sorted(headlines, key=lambda r: r.get("published_at", ""), reverse=True):
        title = norm_name(row["title"])
        roles = {role for role, alias in aliases if f" {alias} " in f" {title} "
                 and (" " in alias or _RACING_CONTEXT.search(title))}
        if not roles:
            continue
        key = title
        if key in seen:
            continue
        seen.add(key)
        score = (5 if "horse" in roles else 3 if roles & {"trainer", "jockey"} else 1)
        score += 2 if _PRIORITY.search(title) else 0
        scores.append((score, row))
    scores.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in scores[:4]]
