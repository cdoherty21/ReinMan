# Rein Man — notes for Claude and other agents

Rein Man is a horse-racing handicapping system. Its parts:
* `engine/`: the Python engine, run by GitHub Actions;
* `worker/`: the Cloudflare Worker, D1 migrations and the 5-minute clock;
* `web/`: the React app.

Documentation is in `docs/`.

## Database efficiency is a hard requirement

The D1 database is on Cloudflare's free tier: 100,000 rows written per day,
account-wide, reset at 00:00 UTC. Rein Man must stay **under 50,000 rows written per
day** in normal operation. Read `docs/D1_BUDGET.md` before changing any code that writes.

In short:
* **Write only what changed.**
  * Use `db.sync(...)`, not `upsert_many`, for anything re-read on a schedule.
  * Timestamps alone never justify a write.
* **History is kept when it is meaningful.**
  * Odds use `ingest.odds_worth_saving`.
  * Predictions use `predict.material_change`.
  * Do not weaken these to "save every read".
  * Do not reduce what is collected or analysed to save writes. Analyses always run in
    full; only identical results are not stored again.
* **Every index costs one write per row it covers.** Add one only with the query that
  needs it.
* **New write paths must respect `quota.may(db, kind)`.**
  * Kinds: critical, essential, standard, background, bulk.
  * Bulk commands must go through `cli.bulk(...)`.
* **Every new write path needs a test in `engine/tests/test_write_budget.py`** showing
  that an identical second run writes 0 rows.
* **Jobs are logged with `cli.job(...)`**, one `job_runs` row with `rows_written`. Do not
  add per-tick or heartbeat rows.
* **Check real usage with the read-only `reinman-usage` workflow** (`reinman d1-usage`).
  It reports Cloudflare's measured numbers and per-job counts, clearly labelled.

## GitHub Actions minutes are a hard budget too

The repository is private on GitHub Free: 2,000 Actions minutes a month, each job rounded
up to a whole minute. Read `docs/ACTIONS_BUDGET.md` before adding or changing a workflow.
Use one job instead of several, gate on changed paths, schedule backups no more than daily,
and do in the Worker whatever can be done there.

## Other standing rules

* **Data fixes** are merged to `main` before they run and are recorded in
  `docs/DATA_LOG.md`.
* **Never delete historical data** (results, predictions, odds history, news). Predictions
  and locks are append-only and enforced by triggers.
* **Point-in-time features:** nothing dated on or after a race may feed its prediction.

## Skills in this repository

`.claude/skills` holds skills installed with Vercel's `skills` tool (sources pinned in
`skills-lock.json`):
* `vercel-react-best-practices` and `web-design-guidelines`: apply them to `web/`;
* `webapp-testing`: Playwright checks of the running app;
* `systematic-debugging`, `test-driven-development`, `verification-before-completion`
  and `writing-plans`: working habits.
