"""Model registry and audit trail. Versions are never deleted; artifacts are immutable."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .db import Database
from .models import MarketBaseline, load_model

BASELINE_VERSION = "v0-market-baseline"


def log_event(db: Database, version: str | None, event: str, details: dict | None = None) -> None:
    db.insert("model_events", {"model_version": version, "event": event, "details": details or {}})


def ensure_baseline(db: Database) -> None:
    if not db.one("SELECT model_version FROM models WHERE model_version=?", [BASELINE_VERSION]):
        db.insert("models", {"model_version": BASELINE_VERSION, "kind": "market_baseline", "status": "champion",
                             "artifact": json.dumps(MarketBaseline().to_json()),
                             "notes": "Cold-start baseline: normalized market / morning-line probabilities."})
        log_event(db, BASELINE_VERSION, "registered", {"reason": "cold start"})


def champion(db: Database):
    ensure_baseline(db)
    row = db.one("SELECT * FROM models WHERE status='champion' ORDER BY created_at DESC LIMIT 1")
    return row["model_version"], load_model(row["artifact"]), row


def new_version(kind: str) -> str:
    return f"v{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{kind}"


def register(db: Database, version: str, kind: str, artifact: dict, metrics: dict, window: str,
             status: str = "candidate", notes: str = "") -> None:
    db.insert("models", {"model_version": version, "kind": kind, "status": status, "artifact": json.dumps(artifact),
                         "metrics": metrics, "train_window": window, "notes": notes})
    log_event(db, version, "registered", {"status": status, "window": window})


def promote(db: Database, version: str, evidence: dict) -> None:
    prev = db.query("SELECT model_version FROM models WHERE status='champion'")
    for p in prev:
        db.execute("UPDATE models SET status='retired' WHERE model_version=?", [p["model_version"]])
        log_event(db, p["model_version"], "retired", {"replaced_by": version})
    db.execute("UPDATE models SET status='champion' WHERE model_version=?", [version])
    log_event(db, version, "promoted", evidence)


def reject(db: Database, version: str, evidence: dict) -> None:
    db.execute("UPDATE models SET status='rejected' WHERE model_version=?", [version])
    log_event(db, version, "rejected", evidence)
