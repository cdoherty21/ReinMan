# Setup & Deployment

Everything below runs on free tiers.

## 1. Cloudflare (Workers + D1 + Cron) — free
```bash
# Run from the ReinMan repository root
npm ci
npx wrangler login
npx wrangler d1 create rein-man            # copy the database_id into worker/wrangler.jsonc
npm run db:migrate:remote                   # creates tables + integrity triggers
npx wrangler secret put REINMAN_PASSWORD -c worker/wrangler.jsonc   # your login password (long!)
npx wrangler secret put SESSION_SECRET  -c worker/wrangler.jsonc   # random 32+ chars: openssl rand -hex 32
npm run deploy                              # → https://rein-man.<your-subdomain>.workers.dev
```
**Login:** email + password, with a "Forgot password?" email. Add these Worker secrets
(Cloudflare → Workers & Pages → rein-man → Settings → Variables and Secrets, type **Secret**):

| Secret | Value |
|---|---|
| `REINMAN_EMAIL` | the email you log in with (reset emails go here) |
| `REINMAN_PASSWORD` | your first password (used until you reset it) |
| `SESSION_SECRET` | a long random string you never type (also keys the stored password hash) |
| `RESEND_API_KEY` | free API key from resend.com, for reset emails |

Make the free Resend account with the **same email** as `REINMAN_EMAIL`: without your own
domain, Resend's test sender (`onboarding@resend.dev`) only delivers to the account owner.
With a domain verified in Resend, set the `MAIL_FROM` var (e.g. `Rein Man <login@yourdomain>`).

A reset link works once, for 30 minutes. After a reset the new password is stored in D1 as a
salted HMAC keyed with `SESSION_SECRET` (never readable), and every other signed-in browser
is signed out. Changing `SESSION_SECRET` later invalidates that stored password: use
"Forgot password?" afterwards. Failed logins are throttled (8 per 15 min per IP); reset
emails are limited to 3 per hour. `AUTH_DISABLED: "true"` removes login entirely (not
recommended).

## Documents
The **Documents** page uploads PDFs (with real text), HTML, TXT, CSV or Rein Man JSON, individually or inside a ZIP. The
browser extracts the text. The hourly **reinman-documents** workflow (or `reinman documents`) scans uploads independently of the live-data feed. Equibase Pocket PPs and the Finger Lakes full program can create cards from their entries. PDFs are read with Poppler (`pdftotext`) by the engine. Post times are used when printed. The scanner links documents
to stored races by horse names, keeps each horse's passage for the race page, and extracts
workouts (standard "date track distance condition time grade rank/field" lines, which feed
the model's workout features) and payout lines. JSON in the canonical format is imported
directly. Matching files with the same upload hints are accepted once. Overlapping race programs add evidence to the existing card. Uploads request a scan immediately through the Worker’s GitHub dispatch token. The hourly document workflow provides a fallback. Click the filename to view an upload. Earlier uploads show the extracted text
because only the text was stored. The scanner can OCR images and the first 12 pages of image-only
PDFs. It reads labeled entry tables from Word, Excel, CSV, TSV, HTML, and readable text layouts.
A table needs a race number, program number and horse name. Supply the track and date in the table
or as upload hints. Available fields such as post time, surface, distance, purse, morning line,
jockey, trainer and weight populate the race page. Picks are recorded only when a verified post
time is available and the race is still upcoming.

## 2. Racing data
No live source is connected (no free, permitted U.S. feed exists — see
[DATA_SOURCES.md](DATA_SOURCES.md)). Load data you're allowed to use:
```bash
cd engine && pip install -e .
REINMAN_DB=d1 CLOUDFLARE_ACCOUNT_ID=… CLOUDFLARE_API_TOKEN=… D1_DATABASE_ID=… reinman import cards.json
```
The free Equibase 2023 research dataset (request it on equibase.com) is the way to build
the historical database for training and backtesting.

## 3. GitHub Actions — secrets (repo → Settings → Secrets and variables → Actions)
| Secret | Value |
|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | your account id |
| `D1_DATABASE_ID` | from `wrangler d1 create` |
| `CLOUDFLARE_D1_TOKEN` | API token with **Account → D1 → Edit** only |
| `CLOUDFLARE_DEPLOY_TOKEN` | token with Workers Scripts Edit + D1 Edit (for `reinman-deploy`) |
| `PUNTERSEDGE_API_KEY` | free racing feed (see [Free U.S. racing data](#free-us-racing-data)) |
| `FIRECRAWL_API_KEY` | optional: automatic OTB results (see [below](#automatic-offtrackbettingcom-results)) |

Optional repository variable `NWS_USER_AGENT`, e.g. `(rein-man, you@example.com)` — NWS
asks automated clients to identify themselves.

Workflows must be on the **default branch** for schedules and dispatch to run. Scheduled data jobs stay off until you add the repository **variable** `REINMAN_ENABLED` = `true` (Settings → Secrets and variables → Actions → Variables) — do that once the racing-data secrets are in place.

## 4. The clock (Worker → GitHub) — required
GitHub's own schedules are unreliable (often hours late or skipped), so the Worker's
5-minute cron starts the jobs: the race-day monitor hourly (10am–2am ET; the free PuntersEdge budget allows a fresh board read about every 45 minutes, and a private repo has 2,000 free Actions minutes a month), a
document scan within 5 minutes of an upload, the daily cycle at ~6:40am ET, and weekly
learning on Monday mornings. Each start is claimed in `job_runs`, so it happens once.
GitHub's schedules remain as backups, and the daily/learning jobs skip themselves if they
already ran.

Create a fine-grained GitHub token (github.com → Settings → Developer settings →
Fine-grained tokens): repository access **only** ReinMan, permission **Actions: Read and
write**, nothing else. Add it as the Worker secret `GITHUB_TOKEN` (Cloudflare → rein-man →
Settings → Variables and Secrets, type Secret). `GITHUB_REPO` is already set in
`worker/wrangler.jsonc`. Check it works on the Performance page → Automation log
(`worker_cron` entries show `dispatch 204`).

## 5. Build the historical database (Phase 2)
```bash
reinman import history.json   # licensed data in the canonical format
reinman train                 # promotes only if it beats the baseline out-of-sample
reinman backtest              # walk-forward, shown on the Performance page
```

## Local development
```bash
npm ci
printf 'REINMAN_PASSWORD=dev\nSESSION_SECRET=dev-secret\n' > worker/.dev.vars
npm run db:migrate:local && npm run dev            # http://localhost:8787
cd engine && pip install -e ".[dev]" && pytest -q   # engine tests
```
The engine can point at the local D1 file with
`REINMAN_DB=sqlite:///../worker/.wrangler/state/v3/d1/miniflare-D1DatabaseObject/<id>.sqlite`.

## Costs
| Item | Cost |
|---|---|
| Cloudflare Workers, D1, Cron, Access | $0 (free tier: 100k requests/day, 5 GB D1) |
| GitHub Actions | $0 for public repos; private repos get 2,000 free minutes/month. A monitor run takes ~1–2 min; ~4 runs/hour over 16 racing hours ≈ 2,000–3,800 min/month. On a private repo, raise `DISPATCH_EVERY_MIN` to 30 or use a self-hosted runner. |
| NWS weather | $0 |
| AI API calls | $0 — all analysis text is generated deterministically |

## Free U.S. racing data

Create a [free PuntersEdge key](https://puntersedge.online/api) and add it as
the GitHub Actions secret `PUNTERSEDGE_API_KEY` (or export it locally).
Rein Man uses this feed when The Racing API credentials are absent. Remove those
credentials if you are switching an existing installation to the free feed.
The [personal-use license](https://puntersedge.online/terms) covers private
models, backtests and dashboards.

```bash
reinman source-check
reinman ingest
reinman results --date YYYY-MM-DD
```

The scheduled jobs reserve at most 19 tote-board calls and four results pages per UTC
day, at two credits each. That's at most 1,426 credits in a 31-day month before manual
calls. The monitor spaces board requests at least 45 minutes apart within a UTC day,
and the daily job settles today's and yesterday's results. Manual commands and backfills
also use the key's allowance. The provider stops serving requests at the free plan's cap.

### Automatic OffTrackBetting.com results

Create a [Firecrawl API key](https://www.firecrawl.dev/app/api-keys) and add it as the
GitHub Actions secret `FIRECRAWL_API_KEY`. The race-day monitor and the daily job then
read OTB's results page for each track that has a finished race without an official
result, and grade Rein Man's picks from it. PuntersEdge results still come first in the
daily job; OTB fills the gaps, and during the day it is the only results source.

| Setting (repository variable or env) | Default | Meaning |
|---|---|---|
| `OTB_MIN_INTERVAL_MIN` | 60 | minutes between reads of the same track-day page |
| `OTB_DAILY_PAGES` | 60 | page reads per UTC day across every job (1 Firecrawl credit each) |

A U.S. racing day with 10–15 tracks uses roughly 40–60 credits, so about 1,500 a month.
Firecrawl's free credits cover a trial; the Hobby plan covers daily use. A race is stored
only if it has one clear winner and every finisher's number and name match the card;
dead heats and mismatches are left for PuntersEdge. If OTB refuses a request (403/429),
OTB reads stop for the rest of the UTC day; the Performance page's automation log shows
an `otb_blocked` entry. OTB track ids are learned from its daily thoroughbred index and
kept in the `otb_tracks` table. See [DATA_SOURCES](DATA_SOURCES.md) for the terms review.

### Upload Equibase result charts

On equibase.com open a day's results and save the free full chart PDF (all races), or use
`https://www.equibase.com/premium/eqbPDFChartPlus.cfm?RACE=A&BorP=P&TID=PRX&CTRY=USA&DT=MM/DD/YYYY&DAY=D&STYLE=EQB`
with the track code and date. Upload it on the Documents page. The scan reads every race:
official results for races whose card is in Rein Man (other sources' results gain the
chart's times), and a running line for every starter, used in that horse's next races.
Dead heats, disqualifications and races whose numbers or names don't match the card are
not stored as results. To re-read charts uploaded before this reader existed, run the
`reinman-documents` workflow with rescan.

### Import OffTrackBetting.com results and payouts

Open an [OTB results page](https://www.offtrackbetting.com/results/) with a single race
showing its official horse, win, place and show table and save the page as HTML. Supply the
track code, date and race number shown on that page:

```bash
reinman import-otb result.html --track AQU --date YYYY-MM-DD --race 8
reinman lock
reinman grade
reinman stats
```

The importer records the posted finish order, straight payoffs and supported exotic
payouts. It checks the horse names and program numbers against the card when one exists
and skips races already marked official. Use `--evening` for an evening card with a
separate race numbering sequence. OTB returns HTTP 403 to some data-center networks,
which is why the automatic source goes through Firecrawl rather than fetching directly.
