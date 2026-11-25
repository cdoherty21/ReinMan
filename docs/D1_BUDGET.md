# D1 write budget

Rein Man's database is the only Cloudflare D1 database on the account (checked
2026-11-25 with Cloudflare's own analytics). The free tier allows **100,000 rows written
per day, account-wide**, and resets at 00:00 UTC. Past that, every write fails until the
reset, so results, locks and grading would stop too.

**Target: under 50,000 rows written per day in normal operation.**

## How D1 counts writes

* Each row inserted, updated or deleted counts as 1.
* Each index that includes a written column adds 1 more. This includes the implicit index
  behind a `TEXT PRIMARY KEY` or a composite primary key.
* An `UPDATE` that changes nothing, or an `INSERT OR IGNORE` / `ON CONFLICT DO NOTHING`
  that hits an existing row, writes 0 rows.
* Batching statements saves round trips, not rows.
* Reads are separate: 5,000,000 rows read per day on the free tier, so it is fine to
  read before deciding whether to write.

## Rules for every write path

1. **Never rewrite an unchanged row.**
   * Use `db.sync(table, rows, key, existing=...)`. It inserts new rows and, for existing
     rows, sets only the columns that changed.
   * A timestamp such as `updated_at` or `fetched_at` never causes a write by itself.
   * Never use `upsert_many` (it sets every column) for data that is re-read on a schedule.
2. **Keep history meaningful, not exhaustive.**
   * Odds are stored when they move (`ingest.odds_worth_saving`):
     * within 30 minutes of post, every tote change is kept, and a race that changes is
       snapshotted whole, so line movement into the close is complete;
     * further out, only moves of 10% or more in implied probability are kept.
   * Every read is still used in full by that run's predictions, through the in-memory
     `fresh` map.
3. **A prediction snapshot is stored only when it says something new**
   (`predict.material_change`), meaning any of:
   * the card, people, news or profiles changed;
   * the pick, decision or confidence changed;
   * the wagers or watch list changed;
   * the odds basis changed;
   * a win probability moved by at least 1 point;
   * the stored view is over 10 minutes old within 16 minutes of post (so the locked pick
     is priced on a fresh read).
4. **Do not leave a blank where the source is blank.** A source omitting a field (weather,
   condition) keeps the stored value instead of overwriting it with NULL. Overwriting it
   also caused a refill loop.
5. **Log one `job_runs` row per job, with `rows_written`.** Quiet monitor steps are logged
   only when they wrote something or failed; the monitor's summary row records the rest.
   The Worker's 5-minute clock writes only when it locks a race or a dispatch fails, and
   cleans up once a day.
6. **Every index must earn its writes.** Before adding one, show the query that needs it.
   Migration 0101 dropped three runner indexes that no query used; each had cost one
   extra write per runner.
7. **Every import must be idempotent.** It re-reads what is stored and writes only the
   difference. Results use `results.race_id` as the guard, and news uses
   `INSERT OR IGNORE` on the URL.
8. **Add a test for every new write path** to `engine/tests/test_write_budget.py`: a
   second identical run must write 0 rows.

## Quota levels (`engine/reinman/quota.py`)

| Level | From | What changes |
|---|---|---|
| normal | 0% | everything runs |
| watch | 50% | everything runs; run summaries show the level |
| defer | 65% | background work waits: stats cache, news polling |
| essential | 75% | far-from-post odds are kept only on 25% moves, and predictions more than 90 minutes from post are not re-stored |
| pause_bulk | 85% | imports, backfills, maintenance, training and backtests refuse to start; the monitor run fails on purpose so GitHub emails an alert |
| emergency | 90% | only results, locks, grading, near-post odds and picks within 30 minutes of post are written |

Never stopped at any level:
* results;
* locks;
* grading;
* odds near post;
* first snapshots of races.

**Deferred work** is re-derived on the next run, not queued:
* a stale stats cache is refreshed when `grades` is newer than it;
* documents stay `pending`;
* news is polled when its 3-hour window allows.

So deferral costs no writes, and everything catches up after 00:00 UTC.

## Where the numbers come from

The source is always stated next to the number.

| Source | How it is labelled | What it is |
|---|---|---|
| `CLOUDFLARE_ANALYTICS_TOKEN` (falls back to the deploy token) | **measured, Cloudflare** | Account-wide rows written per database from Cloudflare's GraphQL analytics, the figure the limit is enforced on. It lags a few minutes, so the current run's own writes are added. |
| D1's `meta.rows_written` on every statement, totalled per job in `job_runs.rows_written` | **measured, engine** | What Rein Man itself wrote. |
| Worker allowance (1,500 rows/day) | **estimated** | Used only when analytics are unavailable. |

`reinman d1-usage` (the read-only workflow `reinman-usage`) prints all three without
writing anything. `REINMAN_QUOTA_USED=<rows>` simulates a level for tests and drills.
