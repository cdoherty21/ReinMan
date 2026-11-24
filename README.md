# Rein Man — The Intelligence Behind the Odds.

A private, automated, statistically grounded handicapping system for U.S. thoroughbred
racing. Rein Man ranks every horse in every race he can get data for, separates the
**most likely winner** from the **best price**, records every selection permanently
before post, grades it against official results, and only lets a new model take over
after it beats the old one on races it has never seen.

## Status by phase

| Phase | State |
|---|---|
| 1 Data research & infrastructure | Done — see [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md). PuntersEdge supports free private U.S. cards and settlement within its credit cap. OTB results and payouts can be imported from saved pages. |
| 2 Historical database | Done — D1/SQLite schema with integrity triggers; `reinman backfill` / `reinman import`. Supports free and paid data sources. |
| 3 Prediction engine | Done — exploded conditional logit + LightGBM + market blend, discounted-Harville finish model, Monte Carlo. |
| 4 Backtesting | Done — chronological walk-forward with leakage tests; results stored and displayed. |
| 5 Daily race analysis | Done — top 3, most likely, value, dark horse, confidence, play/pass, Rein Man's Take; re-predicts on scratches, jockey changes, odds moves, condition/surface changes. |
| 6 Exotic analysis | Done — Win/Place/Show, Exacta, Trifecta, Superfecta, Daily Double, Pick 3–6 with takeout-aware EV. |
| 7 Performance tracking | Done — immutable records, grading (dead heats, entries, scratches, refunds), all requested metrics and breakdowns, actual wagers kept separate. |
| 8 User interface | Done — responsive React app (Today, Race Cards, Best Opportunities, Performance, History). |
| 9 Cloudflare deployment | Ready — Worker + D1 + Cron + private auth; deploy steps in [docs/SETUP.md](docs/SETUP.md). Not deployed from this environment (needs your Cloudflare account). |
| 10 Continuous learning | Done — weekly candidate training, out-of-sample promotion gate, drift monitoring, audit trail. |

## Clearly unavailable with the current data source
Speed figures, pace figures, running positions (so **projected pace**), beaten lengths,
trouble comments and workouts are **not provided** by The Racing API's North American
endpoints. Rein Man shows them as unavailable on every race rather than guessing; they
switch on automatically when a licensed source supplies them via the canonical importer.
Lifetime history before Rein Man started collecting is limited to what backfill returns.
Exotic and multi-race EV are estimates (no live probables/will-pays) and are only
produced when live tote odds exist.

See [free-feed and OTB setup](docs/SETUP.md#free-us-racing-data) for credentials and settlement frequency.

## Daily automation
| When | What | Where |
|---|---|---|
| Every 5 min | Lock final pre-post selection for races at post | Worker cron |
| Every 15 min, 10am–2am ET | `reinman monitor`: cards, scratches, changes, odds, results → re-predict changed races → grade → stats | GitHub Actions (dispatched by Worker) |
| 6:41am ET daily | `reinman daily`: yesterday's/today's results, today's + tomorrow's cards & predictions, grading, stats | GitHub Actions |
| Mondays | `reinman train` (gate) + `reinman backtest` | GitHub Actions |

## Layout
```
./
  engine/   Python engine (`pip install -e .` → `reinman` CLI), tests/
  worker/   Cloudflare Worker (API, auth, cron) + D1 migrations
  web/      React + TypeScript app
  docs/     DATA_SOURCES, ARCHITECTURE, MODEL, GRADING, DATA_FORMAT, SETUP
```

## Tests
`cd engine && pytest -q` (40 tests: parsing, adapters, probability math, wager EV,
immutability triggers, locking, grading rules, metrics, leakage, chronological splits,
model learning, promotion gate, backtest, end-to-end) and `npm test` (formatting, auth).
Model tests use a **synthetic** race generator (`engine/tests/sim.py`) that exists only
for testing — it is never used in production.

> Profitability is not claimed. It has to be earned in the Performance page over a
> meaningful sample of real, graded races — and measured against the market.
