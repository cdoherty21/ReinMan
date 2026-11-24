# Setup & Deployment

Everything below runs on free tiers except the racing-data subscription.

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
**Recommended extra layer:** put the workers.dev hostname behind **Cloudflare Access**
(Zero Trust → Access → Applications → self-hosted, allow only your email; free for up to
50 users). Rein Man's own password + signed, HttpOnly, SameSite=Strict cookie stays on as
a second factor; login attempts are throttled (8 per 15 min per IP). Missing secrets = no
one can log in (fails closed).

## 2. Racing data — The Racing API North America add-on (~£49.99/mo)
Subscribe at theracingapi.com, then verify the adapter against live data:
```bash
cd engine && pip install -e .
RACINGAPI_USERNAME=… RACINGAPI_PASSWORD=… reinman source-check --date <a race day>
```

## 3. GitHub Actions — secrets (repo → Settings → Secrets and variables → Actions)
| Secret | Value |
|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | your account id |
| `D1_DATABASE_ID` | from `wrangler d1 create` |
| `CLOUDFLARE_D1_TOKEN` | API token with **Account → D1 → Edit** only |
| `CLOUDFLARE_DEPLOY_TOKEN` | token with Workers Scripts Edit + D1 Edit (for `reinman-deploy`) |
| `RACINGAPI_USERNAME` / `RACINGAPI_PASSWORD` | The Racing API credentials |

Optional repository variable `NWS_USER_AGENT`, e.g. `(rein-man, you@example.com)` — NWS
asks automated clients to identify themselves.

Workflows must be on the **default branch** for schedules and dispatch to run. Scheduled data jobs stay off until you add the repository **variable** `REINMAN_ENABLED` = `true` (Settings → Secrets and variables → Actions → Variables) — do that once the racing-data secrets are in place.

## 4. Reliable race-day clock (Worker → GitHub)
Create a fine-grained GitHub token with **Actions: Read and write** on this repository only:
```bash
npx wrangler secret put GITHUB_TOKEN -c worker/wrangler.jsonc
```
and set `GITHUB_REPO` (`cdoherty21/ReinMan`) and `GITHUB_REF` in `worker/wrangler.jsonc` vars.
The Worker then dispatches `reinman-monitor` every 15 minutes from 10am to 2am ET.

## 5. Build the historical database (Phase 2)
```bash
REINMAN_DB=d1 CLOUDFLARE_ACCOUNT_ID=… CLOUDFLARE_API_TOKEN=… D1_DATABASE_ID=… \
RACINGAPI_USERNAME=… RACINGAPI_PASSWORD=… reinman backfill --start 2026-06-01 --end 2026-11-23
reinman train      # trains + validates; promotes only if it beats the baseline out-of-sample
reinman backtest   # walk-forward, stored and shown on the Performance page
```
How far back the provider's North American data goes is not documented — the backfill
log shows what came back. Licensed files (e.g. Equibase's 2023 dataset converted to the
canonical format) can be loaded with `reinman import`.

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
| The Racing API NA add-on | ~£49.99/month (the only recurring cost) |
| AI API calls | $0 — all analysis text is generated deterministically |
