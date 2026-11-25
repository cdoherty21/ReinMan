"""Small, dependency-free parsing helpers (odds, distances, times)."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_odds(text: Any) -> float | None:
    """Parse tote/morning-line odds to decimal *profit* odds (5/2 -> 2.5, 8-1 -> 8.0).

    Returns None for blanks, scratches and unparseable values. Even money is 1.0.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text) if text >= 0 else None
    s = str(text).strip().upper()
    if not s or s in {"SCR", "SCRATCHED", "-", "N/A", "NA"}:
        return None
    if s in {"EVEN", "EVN", "EV"}:
        return 1.0
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[/\-:]\s*(\d+(?:\.\d+)?)", s)
    if m:
        den = float(m.group(2))
        return float(m.group(1)) / den if den > 0 else None
    m = re.fullmatch(r"\d+(?:\.\d+)?", s)
    if m:
        return float(s)
    return None


def odds_to_prob(odds: float | None) -> float | None:
    if odds is None or odds < 0:
        return None
    return 1.0 / (odds + 1.0)


_NUM = r"(\d+)(?:\s+(\d+)/(\d+))?|(\d+)/(\d+)"


def _mixed(txt: str) -> float | None:
    txt = txt.strip()
    m = re.fullmatch(r"(\d+)\s+(\d+)/(\d+)", txt)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = re.fullmatch(r"(\d+)/(\d+)", txt)
    if m:
        return int(m.group(1)) / int(m.group(2))
    m = re.fullmatch(r"\d+(?:\.\d+)?", txt)
    if m:
        return float(txt)
    return None


def parse_distance_yards(desc: str | None, value: Any = None, unit: str | None = None) -> int | None:
    """Parse '6 Furlongs', '1 1/16 Miles', '1 Mile 70 Yards', 'About 7 1/2 Furlongs' to yards."""
    if desc:
        s = desc.lower().replace("about", "").replace("abt", "").strip()
        total = 0.0
        found = False
        for amount, kind in re.findall(r"((?:\d+\s+)?\d+(?:/\d+)?(?:\.\d+)?)\s*(miles?|furlongs?|yards?|f\b|m\b|y\b)", s):
            n = _mixed(amount)
            if n is None:
                continue
            found = True
            if kind.startswith("m"):
                total += n * 1760
            elif kind.startswith("f"):
                total += n * 220
            else:
                total += n
        if found and total > 0:
            return int(round(total))
    if value is not None and unit:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        u = unit.strip().upper()
        if u.startswith("F"):
            # some feeds express furlongs x100 (600 = 6f)
            return int(round((v / 100 if v >= 100 else v) * 220))
        if u.startswith("M"):
            return int(round((v / 100 if v >= 100 else v) * 1760))
        if u.startswith("Y"):
            return int(round(v))
    return None


def surface_code(*texts: Any) -> str | None:
    for t in texts:
        if not t:
            continue
        s = str(t).strip().lower()
        if s in {"t", "turf", "inner turf", "outer turf"} or "turf" in s or "grass" in s:
            return "T"
        if s in {"a", "s", "synthetic", "all weather", "aw"} or "synthetic" in s or "all weather" in s or "tapeta" in s or "polytrack" in s:
            return "S"
        if s in {"d", "dirt"} or "dirt" in s:
            return "D"
    return None


_TZ_ABBR = {
    "E": "America/New_York", "ET": "America/New_York", "EST": "America/New_York", "EDT": "America/New_York",
    "C": "America/Chicago", "CT": "America/Chicago", "CST": "America/Chicago", "CDT": "America/Chicago",
    "M": "America/Denver", "MT": "America/Denver", "MST": "America/Denver", "MDT": "America/Denver",
    "P": "America/Los_Angeles", "PT": "America/Los_Angeles", "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    "AZ": "America/Phoenix", "H": "Pacific/Honolulu", "HST": "Pacific/Honolulu",
}


def resolve_tz(tz: str | None, fallback: str | None = None) -> ZoneInfo:
    for cand in (tz, fallback):
        if not cand:
            continue
        name = _TZ_ABBR.get(cand.strip().upper(), cand.strip())
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001
            continue
    return ZoneInfo("America/New_York")


def parse_post_time(race_date: str, post_time: Any, post_time_long: Any = None,
                    tz: str | None = None, track_tz: str | None = None) -> str | None:
    """Return UTC ISO post time. Accepts epoch (s or ms), ISO strings, or '1:15 PM' local."""
    for v in (post_time_long, post_time):
        if v is None or v == "":
            continue
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit() and len(v.strip()) >= 9):
            n = float(v)
            if n > 1e12:
                n /= 1000.0
            return to_iso(datetime.fromtimestamp(n, UTC))
        s = str(v).strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=resolve_tz(tz, track_tz))
            return to_iso(dt)
        except ValueError:
            pass
        m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?\s*([AaPp][Mm])?", s)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            ap = (m.group(3) or "").upper()
            if ap == "PM" and h < 12:
                h += 12
            if ap == "AM" and h == 12:
                h = 0
            if not ap and h < 10:
                h += 12  # racing post times without AM/PM are afternoon/evening
            d = datetime.fromisoformat(race_date)
            local = datetime(d.year, d.month, d.day, h, mi, tzinfo=resolve_tz(tz, track_tz))
            return to_iso(local)
    return None


def norm_name(name: str | None) -> str:
    if not name:
        return ""
    s = re.sub(r"\(.*?\)", "", name)  # drop country suffix e.g. (IRE)
    s = re.sub(r"[^A-Za-z0-9 ]", "", s).upper()
    return re.sub(r"\s+", " ", s).strip()


_SUFFIX = re.compile(r"\b(?:JR|SR|II|III|IV)\b\.?", re.I)
_PARTICLES = {"DE", "DEL", "DA", "DI", "LA", "LE", "VAN", "VON", "ST", "SAN"}


def person_key(name: str | None) -> str:
    """One key for a jockey or trainer however a source writes the name:
    "Hernandez, Andy", "Andy Hernandez", "A. Hernandez" and "Hernandez A" all give
    "HERNANDEZ A". Surname (its last word, with particles such as "De La") plus first
    initial: two riders sharing both would be merged, which is rare at one circuit."""
    if not name:
        return ""
    s = _SUFFIX.sub(" ", re.sub(r"\(.*?\)", "", name)).replace("-", " ").replace(".", " ")
    if "," in s:
        last, first = s.split(",", 1)
    else:
        words = s.split()
        if len(words) >= 2 and all(len(w) == 1 for w in words[1:]):
            last, first = words[0], " ".join(words[1:])          # "Berrios J C"
        elif len(words) >= 2:
            first, last = words[0], " ".join(words[1:])          # "A Hernandez", "Andy De La Cruz"
        else:
            last, first = s, ""
    surname = norm_name(last).split()
    keep = surname[-1:]
    for word in reversed(surname[:-1]):
        if word not in _PARTICLES:
            break
        keep.insert(0, word)
    initial = norm_name(first)[:1]
    return " ".join(keep + ([initial] if initial else [])).strip()


def jockey_id(name: str | None) -> str | None:
    key = person_key(name)
    return f"jn:{key}" if key else None


def trainer_id(name: str | None) -> str | None:
    key = person_key(name)
    return f"tn:{key}" if key else None


def person_id(prefix: str, given_id: Any, first: str | None, last: str | None) -> str | None:
    if given_id:
        return f"{prefix}:{given_id}"
    full = norm_name(f"{first or ''} {last or ''}")
    return f"{prefix}n:{full}" if full else None


def stable_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(",", "").replace("$", "")))
    except ValueError:
        return None


def to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except ValueError:
        return None


def days_between(a: str, b: str) -> int:
    return (datetime.fromisoformat(b[:10]) - datetime.fromisoformat(a[:10])).days


def date_range(start: str, end: str):
    d = datetime.fromisoformat(start)
    e = datetime.fromisoformat(end)
    while d <= e:
        yield d.date().isoformat()
        d += timedelta(days=1)


_TOTE_LADDER = [0.2, 0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.4, 1.5, 1.6, 1.8, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5,
                5.0, 6.0, 7.0, 8.0, 9.0, 10.0]


def min_tote_odds(dec: float) -> float:
    """Smallest standard U.S. tote price at or above ``dec`` (odds-to-1), so a "bet only at
    this price or better" threshold is never shown below the true break point."""
    for step in _TOTE_LADDER:
        if step >= dec - 1e-9:
            return step
    return float(math.ceil(dec - 1e-9))


def fmt_odds(dec: float | None) -> str:
    """Decimal profit odds -> conventional tote style ('5-2', '8-1')."""
    if dec is None:
        return "—"
    if dec < 0.1:
        return "1-9"
    if dec >= 10:
        return f"{round(dec)}-1"
    f = Fraction(dec).limit_denominator(2 if dec >= 5 else 5)
    if f.denominator == 1:
        return f"{f.numerator}-1"
    return f"{f.numerator}-{f.denominator}"


def us_date(value, short: bool = False) -> str:
    """'2026-11-23' -> '11/23/2026' (or '11/23/26'); anything else is returned unchanged."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not m:
        return str(value or "")
    year = m.group(1)[2:] if short else m.group(1)
    return f"{int(m.group(2))}/{int(m.group(3))}/{year}"
