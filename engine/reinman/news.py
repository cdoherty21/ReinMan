"""Free syndicated racing headlines, retained with timestamps and original links."""
from __future__ import annotations

import logging
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from .db import Database
from .util import norm_name, now_iso, to_iso

log = logging.getLogger(__name__)
FEEDS = (
    ("Thoroughbred Daily News", "https://www.thoroughbreddailynews.com/feed/"),
    ("Google News racing search", "https://news.google.com/rss/search?q=thoroughbred+horse+racing+when:7d&hl=en-US&gl=US&ceid=US:en"),
)


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
        if title and url.startswith("https://") and stamp <= observed:
            rows.append({"url": url, "source": source, "title": title[:400],
                         "published_at": stamp, "first_seen_at": observed})
    return rows


def refresh(db: Database) -> dict:
    """Poll at most every three hours within existing scheduled jobs."""
    now = now_iso()
    cutoff = to_iso(datetime.now(timezone.utc) - timedelta(hours=3))
    if db.scalar("SELECT COUNT(*) FROM job_runs WHERE job='news_poll' AND started_at>=?", [cutoff]):
        return {"skipped": "recent news poll"}
    db.insert("job_runs", {"job": "news_poll", "started_at": now, "finished_at": now,
                           "status": "reserved"})
    # Keep this optional supplement compatible while a deployment's migration is pending.
    db.execute("CREATE TABLE IF NOT EXISTS racing_news (url TEXT PRIMARY KEY, source TEXT NOT NULL, "
               "title TEXT NOT NULL, published_at TEXT NOT NULL, first_seen_at TEXT NOT NULL)")
    results = {}
    feeds = FEEDS + tuple(("Configured RSS", url.strip()) for url in
                          os.getenv("REINMAN_NEWS_FEEDS", "").split(",") if url.strip().startswith("https://"))[:4]
    for source, url in feeds:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "ReinMan/1.0 RSS reader",
                                                            "Accept": "application/rss+xml, application/xml, text/xml"})
            with urllib.request.urlopen(request, timeout=12) as response:
                content = response.read(2_000_001)
            if len(content) > 2_000_000:
                raise ValueError("Feed exceeds size limit")
            rows = parse_feed(content, source, now)
            db.insert_many("racing_news", rows, verb="INSERT OR IGNORE")
            results[url] = len(rows)
        except Exception as error:
            log.warning("RSS feed %s unavailable: %s", source, error)
            results[url] = "unavailable"
    return {"feeds": results}


def recent(db: Database, asof: str) -> list[dict]:
    cutoff = to_iso(datetime.fromisoformat(asof.replace("Z", "+00:00")) - timedelta(days=7))
    try:
        return db.query("SELECT * FROM racing_news WHERE published_at>=? AND published_at<=? "
                        "AND first_seen_at<=? ORDER BY published_at DESC LIMIT 500",
                        [cutoff, asof, asof])
    except Exception as error:
        log.warning("News archive unavailable: %s", error)
        return []


def relevant(headlines: list[dict], runner: dict, pedigree: dict) -> list[dict]:
    """Match full multi-word entity names; headlines are context, not verified horse facts."""
    names = [runner.get("horse_name"), runner.get("trainer_name"), runner.get("jockey_name"),
             pedigree.get("sire"), pedigree.get("dam"), pedigree.get("dam_sire")]
    aliases = []
    for name in names:
        clean = norm_name(re.sub(r"\s*\([^)]+\)$", "", name or ""))
        if len(clean.split()) >= 2 and len(clean) >= 7:
            aliases.append(clean)
    return [row for row in headlines if any(" " + name + " " in " " + norm_name(row["title"]) + " "
                                            for name in aliases)][:2]
