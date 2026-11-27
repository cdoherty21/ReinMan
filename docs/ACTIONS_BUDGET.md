# GitHub Actions minutes budget

ReinMan is a private repository on GitHub Free. It gets **2,000 Actions minutes a month**,
and GitHub bills each job rounded up to a whole minute.

## Measured: 2026-11-24 to 11-27

Source: the read-only `reinman-usage` workflow. These are measured minutes, not estimates.

| Workflow | Minutes | Jobs | Note |
|---|---|---|---|
| reinman-ci | 480 | 362 | two jobs per run, both suites every time |
| reinman-monitor | 273 | 106 | race-day loop; about 68 min/day |
| reinman-documents | 61 | 27 | 3-hourly backup scan |
| reinman-deploy | 52 | 52 | |
| others | 63 | | |
| **Total** | **929** | | about 230 min/day, about 7,000 a month at that pace |

## Changes made on 2026-11-27

* **CI is one job**, and runs only the suites whose code changed (`dorny/paths-filter`).
  Engine-only changes skip the browser tests; web-only changes skip Python.
* **The documents backup scan runs daily**, down from every 3 hours. The Worker still
  starts a scan within 5 minutes of an upload.

## Rules for new workflows

* Prefer one job over several, since each job pays setup and rounding.
* Gate on changed paths.
* Schedule backups no more often than daily. The Worker's 5-minute clock is the primary
  scheduler and costs no Actions minutes.
* Do anything that can run in the Cloudflare Worker there: cron, fetch and D1 are
  included in the free plan.
* Check `reinman-usage` after adding or changing a workflow.
