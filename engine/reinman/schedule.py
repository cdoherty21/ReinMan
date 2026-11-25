"""When the monitor should spend one of the day's free PuntersEdge board reads.

The board gives fresh tote odds for every track at once, and a pick can only be priced on
those odds within a few minutes of the read (``predict.analyze_race``). Reads are therefore
aimed just before posts. Post times come from the database at each run, so moved posts,
delays and added or cancelled races are followed automatically.

With more post windows left in the UTC day than reads, the reads go to the busiest
windows: a read is spent only if the races posting soon are at least as many as in the
k-th busiest remaining window (k = reads left).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .db import Database
from .util import to_iso

BOARD_JOB = "puntersedge_tote_request"
DAILY_READS = 19
SOON = (2, 16)          # minutes ahead: races whose pick should be priced on this read
WINDOW = 15             # minutes per post window when pacing the remaining reads
MIN_GAP = 10            # minutes between reads
WARMUP = 90             # read cards and scratches before the first post of the day
IDLE_GAP = timedelta(hours=3)


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def board_due(db: Database, now: datetime | None = None, daily_reads: int = DAILY_READS) -> tuple[bool, str]:
    now = now or datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    used = int(db.scalar(f"SELECT COUNT(*) FROM job_runs WHERE job='{BOARD_JOB}' AND started_at>=?", [day]) or 0)
    left = daily_reads - used
    if left <= 0:
        return False, "no board reads left today"
    last = db.scalar(f"SELECT MAX(started_at) FROM job_runs WHERE job='{BOARD_JOB}'")
    since = (now - _parse(last)) if last else None
    if since is not None and since < timedelta(minutes=MIN_GAP):
        return False, f"board read {int(since.total_seconds() // 60)} min ago"

    end_of_day = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    posts = [_parse(r["post_time"]) for r in db.query(
        "SELECT post_time FROM races WHERE is_cancelled=0 AND status='scheduled' AND post_time BETWEEN ? AND ?",
        [to_iso(now - timedelta(minutes=5)), to_iso(now + timedelta(hours=24))])]
    ahead = sorted((p - now).total_seconds() / 60 for p in posts)
    soon = [m for m in ahead if SOON[0] <= m <= SOON[1]]
    if soon:
        rest = [m for m in ahead if m > 0 and now + timedelta(minutes=m) < end_of_day]
        windows: dict[int, int] = {}
        for m in rest:
            windows[int(m // WINDOW)] = windows.get(int(m // WINDOW), 0) + 1
        busiest = sorted(windows.values(), reverse=True)
        bar = busiest[left - 1] if len(busiest) > left else 1
        if len(soon) >= bar:
            return True, f"{len(soon)} race(s) post within {SOON[1]} min; {left} read(s) left today"
        return False, f"saving reads for busier windows ({len(soon)} race(s) soon, need {bar})"
    upcoming = [m for m in ahead if m > 0]
    if upcoming and upcoming[0] <= WARMUP and (since is None or since >= IDLE_GAP):
        return True, f"warm-up: first post in {round(upcoming[0])} min"
    if not ahead and (since is None or since >= IDLE_GAP):
        return True, "no races on file: looking for cards"
    return False, f"next post in {round(upcoming[0])} min" if upcoming else "no racing ahead"
