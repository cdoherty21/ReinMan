# Architecture

```
            ┌───────────────────────── Cloudflare (free tier) ─────────────────────────┐
 Browser ──▶│ Worker "rein-man"  ── auth (password → HMAC cookie) ── JSON API          │
 (desktop/  │   │  serves React app (static assets, run_worker_first)                  │
  mobile)   │   │  Cron */5 min: lock final pre-post selections; dispatch GH workflow  │
            │   ▼                                                                      │
            │ D1 (SQLite) ◀── one schema: worker/migrations/0001_init.sql              │
            └───▲──────────────────────────────────────────────────────────────────────┘
                │ D1 REST API
 GitHub Actions │ (free minutes)                       External data
 ┌──────────────┴───────────────────────────┐          ┌──────────────────────────────┐
 │ Python engine `reinman`                   │◀─────────│ canonical JSON (reinman import)│
 │  monitor (timed to today's post times)    │          │ NWS api.weather.gov          │
 │  daily (cards, results, grading, stats)   │          │ (no live feed connected)     │
 │  learn (weekly train + gate + backtest)   │          └──────────────────────────────┘
 └───────────────────────────────────────────┘
```

## Components

| Path | Role |
|---|---|
| `engine/reinman/sources/` | Adapter per provider → canonical cards/results. `base.py` documents the contract. Add a provider = one class. |
| `engine/reinman/ingest.py` | Upserts cards; detects scratches, jockey changes, odds moves, condition/surface changes; stores results/payoffs. |
| `engine/reinman/features.py` | Point-in-time features (≈50) from accumulated history; identical for training and live. |
| `engine/reinman/models/core.py` | Exploded (Plackett–Luce) conditional logit, LightGBM scorer, Benter-style market blend, cold-start baseline. JSON artifacts. |
| `engine/reinman/probability.py` | Discounted Harville finish-order model, exact enumeration + seeded Monte Carlo; place/show/exotic probabilities. |
| `engine/reinman/wagers.py` | EV with takeout, pari-mutuel place/show pool math, exotic and multi-race ticket construction. |
| `engine/reinman/selections.py` | Top 3, most likely, value, dark horse, confidence, play/pass, Rein Man's Take (deterministic text). |
| `engine/reinman/predict.py` | Daily analysis; append-only snapshots; locking. |
| `engine/reinman/grading.py` | Grades locked selections and wagers from official payoffs (dead heats, entries, scratches, refunds). |
| `engine/reinman/performance.py` | Records, ROI, drawdown, streaks, calibration, benchmarks, all breakdowns → `stats_cache`. |
| `engine/reinman/train.py`, `backtest.py`, `registry.py` | Chronological training, promotion gate, audit trail, walk-forward backtests. |
| `worker/src/` | API, auth, cron locking, GitHub dispatch. |
| `web/src/` | React + TypeScript UI: Today, Race Cards, Best Opportunities, Performance, History. |

## Integrity guarantees (enforced by the database, not just code)

* `predictions`, `prediction_runners`, `wagers`, `locks`, `grades`, `wager_grades`,
  `model_events`: **UPDATE and DELETE abort** (SQLite triggers, also active in D1).
* A prediction **cannot be inserted at or after post time** (trigger compares the race's
  post time to the database clock).
* A lock must reference a prediction for that race recorded **before** post time.
* Model artifacts are immutable and model rows cannot be deleted.
* Only the locked snapshot is graded; earlier snapshots stay visible in the audit trail.

## Extending

* **International racing / other breeds:** `tracks.category` and `country` exist; race ids
  are `TRACK-YYYYMMDD-N`. Add an adapter that emits the canonical format; features are
  keyed by surface/distance, so they generalize. Takeout overrides are per track.
* **New data fields:** put them in `runners.extra` via the canonical format; add a feature
  in `features.py` (it becomes NaN when absent and is reported as unavailable).

## Known limits

* The engine reads full history through D1's REST API on each run. That is fine for
  tens of thousands of races; beyond that, add an incremental local SQLite mirror cached
  between Action runs (`actions/cache`) — the `Database` abstraction already supports it.
* GitHub's own schedule can be delayed; the Worker cron dispatch is the primary clock and
  locking happens in the Worker every 5 minutes regardless of the Python job.
* Post times that are delayed on race day: Rein Man locks at the *scheduled* post time,
  which is conservative.
