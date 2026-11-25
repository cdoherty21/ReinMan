"""Database access. Two interchangeable backends with the same schema:

* SQLite file (local development, tests, backtests)
* Cloudflare D1 over its HTTP API (production; the Worker reads the same DB)
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "worker" / "migrations"


def schema_sql() -> str:
    parts = [p.read_text() for p in sorted(SCHEMA_DIR.glob("*.sql"))]
    return "\n".join(parts)


class Database:
    max_params = 100  # D1 bound-parameter limit per statement
    # Rows written so far by this connection. D1: Cloudflare's own meta.rows_written
    # (index writes included, the number the free tier is billed on). SQLite: rows
    # changed (a test stand-in; it does not count index writes).
    rows_written = 0

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        raise NotImplementedError

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int | None:
        """Execute a statement; returns last inserted row id when applicable."""
        raise NotImplementedError

    def executescript(self, sql: str) -> None:
        raise NotImplementedError

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        r = self.one(sql, params)
        return next(iter(r.values())) if r else None

    def insert(self, table: str, row: dict, verb: str = "INSERT") -> int | None:
        cols = list(row.keys())
        sql = f"{verb} INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})"
        return self.execute(sql, [_coerce(row[c]) for c in cols])

    def insert_many(self, table: str, rows: Iterable[dict], verb: str = "INSERT",
                    conflict: str = "") -> int:
        rows = list(rows)
        if not rows:
            return 0
        cols = list(rows[0].keys())
        per = max(1, self.max_params // len(cols))
        n = 0
        for i in range(0, len(rows), per):
            chunk = rows[i:i + per]
            ph = ",".join("(" + ",".join("?" for _ in cols) + ")" for _ in chunk)
            params = [_coerce(r.get(c)) for r in chunk for c in cols]
            self.execute(f"{verb} INTO {table} ({','.join(cols)}) VALUES {ph} {conflict}", params)
            n += len(chunk)
        return n

    def upsert_many(self, table: str, rows: Iterable[dict], key: Sequence[str],
                    preserve: Sequence[str] = ()) -> int:
        rows = list(rows)
        if not rows:
            return 0
        cols = list(rows[0].keys())
        upd = [c for c in cols if c not in key and c not in preserve]
        conflict = f"ON CONFLICT({','.join(key)}) DO " + (
            "UPDATE SET " + ",".join(f"{c}=excluded.{c}" for c in upd) if upd else "NOTHING")
        return self.insert_many(table, rows, conflict=conflict)

    def sync(self, table: str, rows: Iterable[dict], key: Sequence[str], existing: dict | None = None,
             ignore: Sequence[str] = ("updated_at",)) -> dict:
        """Write only what changed. New rows are inserted; for existing rows only the
        columns whose values differ are updated (so unchanged rows cost no writes and an
        indexed column is only rewritten when it really changed). ``ignore`` columns
        never trigger a write on their own but are refreshed when the row is written.
        ``existing`` maps key tuples to stored rows; when omitted they are read here."""
        rows = list(rows)
        if not rows:
            return {"inserted": 0, "updated": 0, "unchanged": 0}
        keyof = lambda r: tuple(r[k] for k in key)  # noqa: E731
        if existing is None:
            existing = {}
            cols = " AND ".join(f"{k}=?" for k in key)
            for r in rows:
                old = self.one(f"SELECT * FROM {table} WHERE {cols}", [r[k] for k in key])
                if old:
                    existing[keyof(r)] = old
        new, groups, same = [], {}, 0
        for r in rows:
            old = existing.get(keyof(r))
            if old is None:
                new.append(r)
                continue
            diff = tuple(c for c in r if c not in key and c not in ignore and not same_value(r[c], old.get(c)))
            if not diff:
                same += 1
                continue
            cols = diff + tuple(c for c in ignore if c in r)
            groups.setdefault(cols, []).append(r)
        if new:
            self.insert_many(table, new)
        for cols, group in groups.items():
            # The full row is offered (NOT NULL checks run before the conflict) but only
            # the changed columns are set.
            self.insert_many(table, [{c: r.get(c) for c in group[0]} for r in group],
                             conflict=f"ON CONFLICT({','.join(key)}) DO UPDATE SET " +
                             ",".join(f"{c}=excluded.{c}" for c in cols))
        return {"inserted": len(new), "updated": sum(len(g) for g in groups.values()), "unchanged": same}

    def migrate(self) -> None:
        self.executescript(schema_sql())


def same_value(a: Any, b: Any) -> bool:
    """Equality as stored: JSON text compared as data, numbers with float tolerance."""
    a, b = _coerce(a), b
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) and isinstance(b, str):
        if a == b:
            return True
        if a[:1] in "{[" and b[:1] in "{[":
            try:
                return json.loads(a) == json.loads(b)
            except ValueError:
                return False
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))
    return a == b


def _coerce(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    if isinstance(v, bool):
        return int(v)
    if hasattr(v, "item"):  # numpy scalar
        return v.item()
    return v


class SQLiteDatabase(Database):
    max_params = 999

    def __init__(self, path: str | os.PathLike = ":memory:"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL") if self.path != ":memory:" else None

    def query(self, sql, params=()):
        before = self.conn.total_changes
        rows = [dict(r) for r in self.conn.execute(sql, list(params)).fetchall()]
        self.rows_written += self.conn.total_changes - before
        return rows

    def execute(self, sql, params=()):
        before = self.conn.total_changes
        cur = self.conn.execute(sql, list(params))
        self.rows_written += self.conn.total_changes - before
        return cur.lastrowid

    def executescript(self, sql):
        self.conn.executescript(sql)


class D1Database(Database):
    """Cloudflare D1 via REST: POST /accounts/{acct}/d1/database/{db}/query."""

    def __init__(self, account_id: str, database_id: str, api_token: str):
        self.url = (f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
                    f"/d1/database/{database_id}/query")
        self.token = api_token

    def _post(self, body: dict) -> list[dict]:
        data = json.dumps(body).encode()
        last: Exception | None = None
        for attempt in range(5):
            req = urllib.request.Request(self.url, data=data, method="POST", headers={
                "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    payload = json.loads(r.read())
                if not payload.get("success"):
                    raise RuntimeError(f"D1 error: {payload.get('errors')}")
                for res in payload["result"] or []:
                    self.rows_written += int((res.get("meta") or {}).get("rows_written") or 0)
                return payload["result"]
            except urllib.error.HTTPError as e:
                msg = e.read().decode(errors="replace")[:500]
                if e.code in (429, 500, 502, 503, 504):
                    last = RuntimeError(f"D1 HTTP {e.code}: {msg}")
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"D1 HTTP {e.code}: {msg}") from e
            except urllib.error.URLError as e:
                last = e
                time.sleep(2 ** attempt)
        raise RuntimeError(f"D1 request failed: {last}")

    def query(self, sql, params=()):
        res = self._post({"sql": sql, "params": [_coerce(p) for p in params]})
        return res[0].get("results", []) if res else []

    def execute(self, sql, params=()):
        res = self._post({"sql": sql, "params": [_coerce(p) for p in params]})
        return (res[0].get("meta") or {}).get("last_row_id") if res else None

    def executescript(self, sql):
        # D1 accepts multiple statements in one call when no params are bound.
        self._post({"sql": sql})


def connect(url: str | None = None) -> Database:
    """REINMAN_DB=sqlite:///path.db | d1 (uses CLOUDFLARE_* env vars)."""
    url = url or os.environ.get("REINMAN_DB", "sqlite:///reinman.db")
    if url == "d1":
        return D1Database(os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["D1_DATABASE_ID"],
                          os.environ["CLOUDFLARE_API_TOKEN"])
    if url.startswith("sqlite:///"):
        return SQLiteDatabase(url[len("sqlite:///"):] or ":memory:")
    if url == ":memory:":
        return SQLiteDatabase(":memory:")
    raise ValueError(f"Unsupported REINMAN_DB: {url}")
