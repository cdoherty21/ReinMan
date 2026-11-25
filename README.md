# Rein Man — The Intelligence Behind the Odds.

A private, automated, statistically grounded handicapping system for U.S. thoroughbred
racing. Rein Man ranks every horse in every race he can get data for, separates the
**most likely winner** from the **best price**, records every selection permanently
before post, grades it against official results, and only lets a new model take over
after it beats the old one on races it has never seen.

## Status by phase

| Phase | State |
|---|---|
| 1 Data research & infrastructure | Done — see [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md). PuntersEdge supports free private U.S. cards and settlement within its credit cap. OTB results and payouts are read automatically through Firecrawl when `FIRECRAWL_API_KEY` is set, or imported from saved pages. |
| 2 Historical database | Done — D1/SQLite schema with integrity triggers; `reinman backfill` / `reinman import`. Supports free and paid data sources. |
| 3 Prediction engine | Done — exploded conditional logit + LightGBM + market blend, discounted-Harville finish model, Monte Carlo. |
| 4 Backtesting | Done — chronological walk-forward with leakage tests; results stored and displayed. |
| 5 Daily race analysis | Done — top 3, most likely, value, dark horse, confidence, play/pass, Rein Man's Take; re-predicts on scratches, jockey changes, odds moves, condition/surface changes. |
| 6 Exotic analysis | Done — Win/Place/Show, Exacta, Trifecta, Superfecta, Daily Double, Pick 3–6 with takeout-aware EV. |
| 7 Performance tracking | Done — immutable records, grading (dead heats, entries, scratches, refunds), all requested metrics and breakdowns, actual wagers kept separate. |
| 8 User interface | Done — responsive React app (Today, Race Cards, Best Opportunities, Performance, History). |
| 9 Cloudflare deployment | Ready — Worker + D1 + Cron + private auth; deploy steps in [docs/SETUP.md](docs/SETUP.md). Not deployed from this environment (needs your Cloudflare account). |
| 10 Continuous learning | Done — weekly candidate training, out-of-sample promotion gate, drift monitoring, audit trail. |

## Data: what's missing
Rein Man has **no live data source**. Every source with U.S. race cards, odds and results
either charges for automated access or forbids it, so none is connected. Data goes in
through `reinman import` (the canonical JSON format in `docs/DATA_FORMAT.md`). The free,
permitted option is Equibase's complimentary 2023 research dataset (past performances
and result charts), which trains and backtests the model; an importer will be written
for it once its file format is in hand. Daily picks need daily cards, which means
importing them until a free, permitted feed exists.

See [free-feed and OTB setup](docs/SETUP.md#free-us-racing-data) for credentials and settlement frequency.

## Daily automation
| When | What | Where |
|---|---|---|
| Every 5 min | Lock final pre-post selection for races at post | Worker cron |
| Follows the card: 4–14 min before posts (≥20 min apart), a warm-up before the first race, results sweeps | `reinman monitor`: cards, scratches, changes, odds, results → re-predict changed races → grade → stats | GitHub Actions (dispatched by Worker from today's post times) |
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
