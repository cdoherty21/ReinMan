"""Automatic official results from OffTrackBetting.com's public results pages, read
through Firecrawl's scrape API.

Results only: OTB supplies finish order (top four), win/place/show payoffs, exotic and
multi-race payoffs and scratches. It does not supply cards, odds or running lines.

How it stays polite (see docs/DATA_SOURCES.md):
* one page per track per day holds every race, so a track is read at most once per
  ``OTB_MIN_INTERVAL_MIN`` minutes, and only while Rein Man has a race there that has
  run and has no official result yet;
* a daily page budget (``OTB_DAILY_PAGES``) shared by every job and rerun;
* Firecrawl's basic proxy only. A 403/429 from OTB stops OTB reads for the rest of the
  day instead of retrying through other proxies;
* personal, non-commercial use, as OTB's site license allows.

A race is stored only when it can be stored safely: exactly one paid winner listed
first, no sign of a dead heat, and every finisher's program number and name agreeing
with the card Rein Man already holds. Anything else is left for another source.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from ..util import norm_name, to_float, to_iso
from ..wagertypes import normalize_wager_type
from .tracks import canonical_track_code

log = logging.getLogger("reinman.otb")

SITE = "https://www.offtrackbetting.com"
FIRECRAWL_SCRAPE = "https://api.firecrawl.dev/v2/scrape"

# OTB's numeric id and URL slug for each Rein Man track code. Confirmed from OTB's
# results pages on 2026-11-24; other tracks are learned from OTB's daily thoroughbred
# index and remembered in the otb_tracks table.
KNOWN_TRACKS: dict[str, tuple[int, str]] = {
    "PRX": (63, "parx-racing"), "FL": (30, "finger-lakes"), "ZIA": (109, "zia-park"),
    "DED": (21, "delta-downs"), "GP": (36, "gulfstream-park"), "LRL": (49, "laurel-park"),
    "MVR": (216, "mahoning-valley"), "EVD": (26, "evangeline-downs"), "MNR": (57, "mountaineer-park"),
}

_TRACK_LINK = re.compile(r"/results/(\d+)/([a-z0-9%()-]+?)(?:-\d{8})?\.html")
_MONEY = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?|\.\d+)")
_PROGRAM = re.compile(r"\d{1,2}[A-Z]?")


class OTBBlocked(RuntimeError):
    """OTB or Firecrawl refused the request; stop reading OTB for now."""


# ---------------------------------------------------------------- parsing (pure)

def results_url(otb_id: int, slug: str, race_date: str) -> str:
    return f"{SITE}/results/{otb_id}/{slug}-{race_date.replace('-', '')}.html"


def parse_track_index(links: list[str]) -> dict[str, tuple[int, str]]:
    """Map Rein Man track codes to (OTB id, slug) from links on an OTB results index."""
    found: dict[str, tuple[int, str]] = {}
    for link in links:
        m = _TRACK_LINK.search(link)
        if not m:
            continue
        slug = m.group(2)
        code = canonical_track_code(slug.replace("-", " "))
        if code and code not in found:
            found[code] = (int(m.group(1)), slug)
    return found


def _cells(row) -> list[str]:
    return [" ".join(td.stripped_strings) for td in row.find_all("td", recursive=False)]


def _race_date(meta: str) -> str | None:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", meta)
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None


def _finishers(section) -> list[dict] | None:
    table = section.select_one("table.res-table")
    if table is None:
        return None
    heads = [th.get_text(" ", strip=True).lower() for th in table.select("thead th")]
    if "win" not in heads or "horse" not in heads:
        return None  # not run yet: OTB shows the morning line instead
    col = {name: heads.index(name) for name in ("#", "horse", "win", "place", "show") if name in heads}
    out = []
    for row in table.select("tbody tr"):
        cells = _cells(row)
        if len(cells) != len(heads):
            return None
        program = cells[col["#"]].upper().strip() if "#" in col else ""
        name = cells[col["horse"]].strip()
        if not _PROGRAM.fullmatch(program) or not name:
            return None
        out.append({"program_number": program, "horse_name": name, "finish_position": len(out) + 1,
                    **{f"{m}_payoff": (to_float(cells[col[m]]) if m in col and cells[col[m]] else None)
                       for m in ("win", "place", "show")},
                    "dead_heat": False})
    return out


def _looks_settled(fin: list[dict]) -> bool:
    """Exactly one paid winner, listed first, and no sign of a dead heat."""
    if not fin or not fin[0]["win_payoff"]:
        return False
    paid = lambda m: sum(1 for f in fin if f[f"{m}_payoff"])  # noqa: E731
    return paid("win") == 1 and paid("place") <= 2 and paid("show") <= 3 and len(fin) <= 4


def _payoffs(section) -> list[dict]:
    out, seen = [], set()
    for row in section.select("table.exotics-table tbody tr"):
        cells = _cells(row)
        if len(cells) != 3:
            continue
        bet, numbers, paid = cells
        m = re.match(r"\s*(?:\$\s*(\d*\.?\d+)|(\d+)\s*cents?)\s+(.+)", bet.replace("\xa0", " "), re.I)
        amount = _MONEY.search(paid)
        if not m or not amount:
            continue
        base = float(m.group(1)) if m.group(1) else int(m.group(2)) / 100
        name = " ".join(m.group(3).split())
        wager_type = normalize_wager_type(name)
        if wager_type == "OTHER":
            continue
        selection = re.sub(r"\s+", "", numbers).upper()
        payout = to_float(amount.group(1))
        key = (wager_type, selection, base, payout)
        if key in seen:
            continue
        seen.add(key)
        out.append({"wager_type": wager_type, "wager_name": name, "base_amount": base,
                    "winning_numbers": selection, "payoff_amount": payout})
    return out


def _scratches(section) -> list[str]:
    for div in section.select(".text-muted div"):
        text = div.get_text(" ", strip=True)
        if text.lower().startswith("scratches:"):
            return [n.strip() for n in text.split(":", 1)[1].split(",") if n.strip()]
    return []


def parse_day(html: str, race_date: str) -> list[dict]:
    """Canonical results for every settled race on one OTB track-day page.

    Races for another date (OTB serves its latest day when a dated page is missing),
    races not yet run, and races that look like dead heats are left out.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for section in soup.select("section.race-section"):
        head = section.select_one(".race-head h2")
        meta = section.select_one(".race-meta")
        m = re.fullmatch(r"Race\s+(\d+)", head.get_text(" ", strip=True) if head else "")
        if not m or not meta or _race_date(meta.get_text(" ", strip=True)) != race_date:
            continue
        fin = _finishers(section)
        if fin is None:
            continue
        if not _looks_settled(fin):
            log.warning("OTB race %s on %s skipped: not a single clear winner", m.group(1), race_date)
            continue
        results.append({"race_number": int(m.group(1)), "finishers": fin,
                        "payoffs": _payoffs(section), "scratches": _scratches(section)})
    return results


# ---------------------------------------------------------------- fetching

class Firecrawl:
    def __init__(self, key: str):
        self.key = key

    def scrape(self, url: str, **options) -> dict:
        body = {"url": url, "proxy": "basic", "maxAge": 0, "timeout": 60000, **options}
        request = urllib.request.Request(
            FIRECRAWL_SCRAPE, data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in (401, 402, 403, 429):
                raise OTBBlocked(f"Firecrawl returned HTTP {error.code}") from error
            raise
        data = payload.get("data") or {}
        status = (data.get("metadata") or {}).get("statusCode")
        if status in (403, 429):
            raise OTBBlocked(f"OTB returned HTTP {status}")
        if not payload.get("success") or (status and status >= 400):
            raise RuntimeError(f"Firecrawl scrape of {url} failed (status {status})")
        return data


# ---------------------------------------------------------------- source adapter

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class OTBResults:
    """Results-only source; needs FIRECRAWL_API_KEY and the database."""
    name = "otb"
    capabilities = dict.fromkeys(("official_results", "exotic_payoffs", "scratches"), True)

    PAGE_JOB = "otb_page_request"
    BLOCK_JOB = "otb_blocked"
    INDEX_JOB = "otb_index_request"

    def __init__(self, db=None, client: Firecrawl | None = None, now=None):
        key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
        self.db = db
        self.client = client or (Firecrawl(key) if key else None)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.daily_pages = _env_int("OTB_DAILY_PAGES", 60)
        self.interval = timedelta(minutes=_env_int("OTB_MIN_INTERVAL_MIN", 60))
        self.settle = timedelta(minutes=10)
        self.pages: dict[tuple[str, str], list[dict]] = {}
        self._tracks: dict[str, tuple[int, str]] | None = None

    @property
    def configured(self) -> bool:
        return self.client is not None and self.db is not None

    # cards come from other sources
    def meets(self, race_date: str) -> list[dict]:
        return []

    def card(self, meet: dict) -> dict:
        raise KeyError("OTB supplies results only")

    # -- bookkeeping in job_runs / otb_tracks
    def _today(self) -> str:
        return self.now().strftime("%Y-%m-%d")

    def _log(self, job: str, details: str) -> None:
        stamp = to_iso(self.now())
        self.db.insert("job_runs", {"job": job, "started_at": stamp, "finished_at": stamp,
                                    "status": "ok", "details": details})

    def _count_today(self, job: str) -> int:
        return int(self.db.scalar("SELECT COUNT(*) FROM job_runs WHERE job=? AND started_at>=?",
                                  [job, self._today()]) or 0)

    def blocked_today(self) -> bool:
        return self._count_today(self.BLOCK_JOB) > 0

    def _last_read(self, track_id: str, race_date: str) -> str | None:
        return self.db.scalar("SELECT MAX(started_at) FROM job_runs WHERE job=? AND details=?",
                              [self.PAGE_JOB, f"{track_id} {race_date}"])

    def _track_map(self) -> dict[str, tuple[int, str]]:
        if self._tracks is None:
            self.db.execute("CREATE TABLE IF NOT EXISTS otb_tracks (track_id TEXT PRIMARY KEY, "
                            "otb_id INTEGER NOT NULL, slug TEXT NOT NULL, seen_at TEXT)")
            self._tracks = dict(KNOWN_TRACKS)
            for row in self.db.query("SELECT track_id, otb_id, slug FROM otb_tracks"):
                self._tracks.setdefault(row["track_id"], (int(row["otb_id"]), row["slug"]))
        return self._tracks

    def _discover(self, missing: set[str]) -> None:
        """Read OTB's thoroughbred index (once a day) to learn ids for missing tracks."""
        if not missing or self._count_today(self.INDEX_JOB) or not self._spend():
            return
        self._log(self.INDEX_JOB, ",".join(sorted(missing)))
        data = self.client.scrape(f"{SITE}/results/horse-racing.html", formats=["links"])
        found = parse_track_index(data.get("links") or [])
        rows = [{"track_id": code, "otb_id": oid, "slug": slug, "seen_at": to_iso(self.now())}
                for code, (oid, slug) in found.items() if code not in KNOWN_TRACKS]
        if rows:
            self.db.upsert_many("otb_tracks", rows, key=["track_id"])
        for code, value in found.items():
            self._tracks.setdefault(code, value)

    def _spend(self) -> bool:
        # The free Firecrawl plan permits 1,000 credits per month. Reserve
        # headroom for manual requests; both index and results cost credits.
        month = self.now().strftime("%Y-%m-01")
        used = self.db.scalar("SELECT COUNT(*) FROM job_runs WHERE job IN (?,?) AND started_at>=?",
                              [self.PAGE_JOB, self.INDEX_JOB, month]) or 0
        if used >= 900:
            log.warning("OTB monthly free-tier budget reached")
            return False
        if self._count_today(self.PAGE_JOB) + self._count_today(self.INDEX_JOB) >= self.daily_pages:
            log.warning("OTB daily page budget (%s) reached", self.daily_pages)
            return False
        return True

    # -- the adapter interface
    def due_tracks(self, race_date: str) -> list[str]:
        """Tracks with a race that has run, has no official result, and wasn't read recently."""
        cutoff = (self.now() - self.settle).strftime("%Y-%m-%dT%H:%M:%S")
        rows = self.db.query(
            "SELECT DISTINCT track_id FROM races WHERE race_date=? AND status NOT IN ('official','cancelled') "
            "AND NOT is_cancelled AND post_time IS NOT NULL AND post_time <= ? ORDER BY track_id",
            [race_date, cutoff])
        due = []
        for row in rows:
            last = self._last_read(row["track_id"], race_date)
            if last and self.now() - datetime.fromisoformat(last.replace("Z", "+00:00")).replace(
                    tzinfo=timezone.utc) < self.interval:
                continue
            due.append(row["track_id"])
        return due

    def result_meets(self, race_date: str) -> list[dict]:
        if not self.configured or self.blocked_today():
            return []
        due = self.due_tracks(race_date)
        tracks = self._track_map()
        try:
            self._discover({t for t in due if t not in tracks})
        except OTBBlocked as error:
            self._block(error)
            return []
        except Exception as error:  # noqa: BLE001 — discovery is best effort
            log.warning("OTB track index unavailable: %s", error)
        return [{"meet_id": f"otb-{t}-{race_date}", "track_id": t, "race_date": race_date}
                for t in due if t in tracks]

    def _block(self, error: Exception) -> None:
        log.warning("Stopping OTB reads for today: %s", error)
        self._log(self.BLOCK_JOB, str(error)[:500])

    def results(self, meet: dict) -> list[dict]:
        track, race_date = meet["track_id"], meet["race_date"]
        key = (track, race_date)
        if key in self.pages:
            return self.pages[key]
        self.pages[key] = []
        if self.blocked_today() or not self._spend():
            return []
        otb_id, slug = self._track_map()[track]
        self._log(self.PAGE_JOB, f"{track} {race_date}")
        try:
            data = self.client.scrape(results_url(otb_id, slug, race_date), formats=["html"],
                                      onlyMainContent=False, includeTags=["#results_content"])
        except OTBBlocked as error:
            self._block(error)
            return []
        self.pages[key] = [r for r in parse_day(data.get("html") or "", race_date)
                           if self._matches_card(track, race_date, r)]
        return self.pages[key]

    def _matches_card(self, track: str, race_date: str, result: dict) -> bool:
        from ..ingest import race_id_for
        rid = race_id_for(track, race_date, result["race_number"])
        runners = {r["program_number"].upper(): norm_name(r["horse_name"])
                   for r in self.db.query("SELECT program_number, horse_name FROM runners WHERE race_id=?", [rid])}
        if not runners:
            return False
        for f in result["finishers"]:
            if runners.get(f["program_number"]) != norm_name(f["horse_name"]):
                log.warning("OTB result for %s skipped: #%s %s does not match the card",
                            rid, f["program_number"], f["horse_name"])
                return False
        return True

