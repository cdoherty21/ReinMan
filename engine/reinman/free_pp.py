"""Incremental collection from explicitly permitted racing document directories."""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser
from urllib.error import HTTPError

from bs4 import BeautifulSoup

from . import documents, quota
from .sources.otb_live import Firecrawl
from .util import now_iso

MAX_BYTES = 8_000_000
SEEDS = {
    "https://thefreepps.com/handicapping/sires-past-performances/": "Directory returned HTTP 403; automated access unverified",
    "https://thefreepps.com/handicapping/trainers-past-performances/": "Directory returned HTTP 403; automated access unverified",
    "https://www.brisnet.com/": "Brisnet requires written permission for automated access",
    "https://www.equibase.com/": "Equibase prohibits automated access under its published terms",
}
USER_AGENT = "ReinMan personal racing collector"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def canonical(url):
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        return None
    if p.scheme != "https" or not p.hostname or p.username or p.password or port:
        return None
    return urlunsplit(("https", p.hostname.lower(), p.path or "/", p.query, ""))


def approved(url, sources):
    p = urlsplit(url)
    return any(p.hostname == urlsplit(s).hostname and
               (p.path == urlsplit(s).path or p.path.startswith(urlsplit(s).path.rstrip("/") + "/"))
               for s in sources)


def get(url, limit=MAX_BYTES):
    with build_opener(NoRedirect()).open(Request(url, headers={"User-Agent": USER_AGENT}), timeout=25) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("File exceeds size limit")
        return data, response.headers.get_content_type()


def permits(url):
    p = urlsplit(url)
    robots = f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        data, _ = get(robots, 200_000)
    except HTTPError as e:
        return e.code == 404
    parser = RobotFileParser()
    parser.parse(data.decode("utf-8", errors="replace").splitlines())
    return parser.can_fetch(USER_AGENT, url)


def register(db, url, label, parent=None, error=None):
    db.insert("free_pp_urls", {"url": url, "label": label[:300], "parent_url": parent,
                              "status": "blocked" if error else "queued", "error": error},
              verb="INSERT OR IGNORE")


def save_document(db, url, data, mime):
    fingerprint = json.dumps([list(hashlib.sha256(data).digest()), None, None], separators=(",", ":"))
    existing = db.one("SELECT document_id FROM document_upload_fingerprints WHERE fingerprint=?", [fingerprint])
    if existing:
        return existing["document_id"]
    suffix = ".pdf" if mime == "application/pdf" else ".html"
    filename = Path(urlsplit(url).path).stem[:100] or "racing"
    doc_id = db.insert("documents", {"filename":filename + suffix, "mime":mime,
                                    "size_bytes":len(data), "chars":0, "status":"uploading"})
    try:
        # Keep originals for the existing PDF layout and HTML table scanner.
        for seq, start in enumerate(range(0, len(data), 40_000)):
            chunk = data[start:start + 40_000]
            db.execute(f"INSERT INTO document_file_chunks VALUES (?, ?, X'{chunk.hex()}')", [doc_id, seq])
        db.insert("document_upload_fingerprints", {"fingerprint":fingerprint, "document_id":doc_id})
        db.execute("UPDATE documents SET status='pending' WHERE id=?", [doc_id])
    except Exception:
        db.execute("DELETE FROM documents WHERE id=? AND status='uploading'", [doc_id])
        raise
    return doc_id


def credit_available(key):
    request = Request("https://api.firecrawl.dev/v2/team/credit-usage",
                      headers={"Authorization": f"Bearer {key}"})
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        data = json.load(response)["data"]
    return data["planCredits"] == 1000 and data["remainingCredits"] > 100


def collect(db, sources=None):
    """Only URLs with documented permission are enabled in the operator allowlist."""
    if not quota.may(db, "background"):
        return {"deferred": "D1 quota"}
    sources = sources if sources is not None else json.loads(os.getenv("FREE_PP_APPROVED_SOURCES") or "{}")
    sources = {u: reason for raw, reason in sources.items()
               if (u := canonical(raw)) and isinstance(reason, str) and reason.strip()}
    for url, error in SEEDS.items():
        register(db, url, "Source review", error=error)
    for url, reason in sources.items():
        register(db, url, reason)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    rows = db.query("SELECT * FROM free_pp_urls WHERE status='queued' OR "
                    "(status IN ('directory','downloaded','error') AND checked_at<?) "
                    "ORDER BY CASE WHEN status='queued' THEN 0 ELSE 1 END, checked_at LIMIT 20", [cutoff])
    credits = 0
    names = db.query("SELECT DISTINCT rn.horse_name FROM runners rn JOIN races r ON r.race_id=rn.race_id "
                     "WHERE r.race_date BETWEEN date('now') AND date('now','+1 day')")
    active = [row["horse_name"].lower() for row in names if len(row["horse_name"]) >= 4]
    rows.sort(key=lambda row: not any(name in row["label"].lower() for name in active))
    for row in rows:
        if not quota.may(db, "background"):
            break
        url = row["url"]
        if not approved(url, sources):
            continue
        state, error, doc_id = "error", None, row["document_id"]
        try:
            if not permits(url):
                raise PermissionError("robots.txt disallows collection")
            data, mime = get(url)
            if data.startswith(b"%PDF-"):
                doc_id = save_document(db, url, data, "application/pdf")
                state = "downloaded"
            elif mime == "text/html":
                soup = BeautifulSoup(data, "html.parser")
                for a in soup.find_all("a", href=True, limit=2000):
                    if not quota.may(db, "background"):
                        break
                    link = canonical(urljoin(url, a["href"]))
                    if link and approved(link, sources):
                        register(db, link, a.get_text(" ", strip=True), url)
                # Only use Firecrawl for accessible permitted HTML, and consume
                # at most five basic page credits per run. No PDF parsing or LLM extraction.
                key = os.getenv("FIRECRAWL_API_KEY", "").strip()
                if key and credits < 5 and credit_available(key):
                    credits += 1
                    scraped = Firecrawl(key).scrape(url, formats=["html"], parsers=[])
                    html = scraped.get("html")
                    if html and len(html.encode()) < MAX_BYTES:
                        data = html.encode()
                if soup.find("table"):
                    doc_id = save_document(db, url, data, "text/html")
                    state = "downloaded"
                else:
                    state = "directory"
            else:
                raise ValueError("Unsupported document format")
        except (PermissionError, HTTPError) as exc:
            state, error = "blocked", str(exc)[:400]
        except Exception as exc:
            error = str(exc)[:400]
        db.sync("free_pp_urls", [{"url":url,"label":row["label"],"status":state,
                                 "error":error,"document_id":doc_id,"checked_at":now_iso()}],
                ["url"], ignore=())
        time.sleep(1)
    result = documents.process_queue(db) if any(r["status"] != "blocked" for r in rows) else {}
    archive_dir = os.getenv("FREE_PP_ARCHIVE_DIR")
    if archive_dir:
        export_archives(db, Path(archive_dir))
    records = db.scalar("SELECT COUNT(*) FROM chart_lines WHERE document_id IN (SELECT document_id FROM free_pp_urls)") or 0
    records += db.scalar("SELECT COUNT(*) FROM historical_winners WHERE document_id IN (SELECT document_id FROM free_pp_urls)") or 0
    return {"horse_records":records, "firecrawl_credits_reserved":credits, "sources":db.query("SELECT status, COUNT(*) AS total FROM free_pp_urls GROUP BY status"),
            "import":result}


def export_archives(db, directory):
    """Export only originals already lawfully collected; split below 60 MB."""
    directory.mkdir(parents=True, exist_ok=True)
    rows = db.query("SELECT DISTINCT d.id,d.filename,d.size_bytes FROM documents d JOIN free_pp_urls u "
                    "ON u.document_id=d.id WHERE d.size_bytes < ? ORDER BY d.id", [MAX_BYTES])
    archive, total, part = None, 0, 0
    try:
        for row in rows:
            data = documents._original_bytes(db, row["id"])
            if not data or len(data) >= MAX_BYTES:
                continue
            if archive is None or total + len(data) > 55_000_000:
                if archive:
                    archive.close()
                part += 1
                archive = ZipFile(directory / f"free-pp-{part}.zip", "w", ZIP_DEFLATED)
                total = 0
            archive.writestr(f'{row["id"]}-{Path(row["filename"]).name}', data)
            total += len(data) + 1024
    finally:
        if archive:
            archive.close()
