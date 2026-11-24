# Data Sources — Research & Decisions (Phase 1)

Researched November 2026. Rein Man only uses sources that permit the intended
automated, personal use. It never scrapes sites whose terms forbid it, never
bypasses paywalls, and never fabricates missing data.

## Summary

| Source | Data | History | Update freq. | Access / restrictions | Cost | Reliability | Status in Rein Man |
|---|---|---|---|---|---|---|---|
| **The Racing API — North America add-on** | Meets, entries (ML & live odds, W/P/S pool amounts, scratches, jockey/trainer, equipment, medication, weight, sire/dam/damsire, track condition, weather, wager pools offered), results (order of finish, W/P/S payoffs, exotic payoffs incl. multi-race, fractions, scratches) | Results endpoint by meet; depth of North American history not published — verify with `reinman backfill` | Near real time (has `mtp`, live odds) | REST + HTTP Basic auth, 5 req/s; licensed for API use | **£49.99/month** add-on (on any plan, incl. Free) | Commercial API with published OpenAPI spec | **Removed** — U.S. data is not free (needs the paid North America add-on); the adapter was deleted at the owner's request |
| **National Weather Service** (api.weather.gov) | Hourly forecast: temperature, precipitation probability, description | No archive (forecast/observations only) | Hourly | Public, free, no key; asks for a User-Agent | Free | Government service, occasional outages | **Implemented** (`sources/nws.py`) — supplements missing weather |
| **Canonical JSON import** | Anything you're licensed to use, incl. rich past performances (speed/pace figures, call positions, trouble, workouts) | Whatever the file holds | Manual / scripted | Your license terms | Depends | — | **Implemented** (`sources/canonical.py`, `docs/DATA_FORMAT.md`) |
| **Equibase** (equibase.com) | Entries, results, charts, PPs | Decades | Real time | Terms of Use prohibit robots, spiders, scrapers or any automated access; they've treated scraping as a breach and blocked it | Free to view; data products paid/licensed | Official industry database | **Not used** (ToS). Licensed data can be imported via the canonical format |
| **TJC Thoroughbred Data Hub** (hub.jockeyclub.com, launched Nov 2026) | REST API (and MCP) for North American racing, pedigree and auction data, sourced from The Jockey Club registry and Equibase | Extensive | Real time | Licensed; 100 req/min default | Paid licensing (published price list; bigger datasets by quote) | Official | **Not implemented yet.** Strongest candidate for a second adapter once you choose a plan; the adapter interface is ready |
| **Equibase research dataset (2023)** | Every past performance + result chart for 2023 Equibase-charted races | 2023 | One-off | Licensed | Paid | Official | Optional: import for model bootstrapping |
| **Brisnet** | PP data files, comprehensive chart files (6 comma-delimited files per card) | Long | Daily | Purchased per card/subscription; files for personal use | Paid per card / subscription | Long-standing | **Not implemented**: I could not verify the field layout of the PP single-file format, and Rein Man won't parse a format from guesses. Brisnet documents the chart file structure in its help center; once you have a file + spec, a converter to the canonical format is a small, testable job |
| **DRF** | PPs, text charts back to 1993 (comma-delimited) | Since 1993 | Daily | Subscription | Paid | Long-standing | Not implemented (paid; same path as Brisnet) |
| **Official track websites** | Entries/results/scratches per track | Varies | Varies | Each site's own terms; formats differ per track; most results come from Equibase anyway | Free to view | Inconsistent | Not used (no uniform, permitted feed) |
| **Third-party Equibase scrapers** (Apify actors, parse.bot) | Entries/results | — | — | Unofficial; they scrape pages whose owner prohibits it | Some free tiers | Fragile | **Rejected** — they don't make the underlying access permitted |
| **Horse Racing Nation** | Free entries/results website (Equibase partnership) | — | — | Website, not an API | Free | — | Not used |

## What this means in practice

* **There is no free, permitted, comprehensive source of U.S. thoroughbred entries,
  odds, past performances and results.** Every free website that has them either forbids
  automated collection or blocks it; Rein Man does not scrape around that.
* **Rein Man therefore has no live feed.** Data is loaded with `reinman import`.
* **Free and permitted:** Equibase's complimentary 2023 dataset (full-year past
  performances + result charts) for training and backtesting, and NWS weather.
* If a free, permitted feed appears, adding it is one adapter class in `engine/reinman/sources/`.

## Sources consulted
- Equibase Terms of Use — https://www.equibase.com/about/terms.cfm
- DRF on Equibase outages and scraping — https://www.drf.com/news/equibase-website-back-almost-100-percent-following-attacks
- The Racing API docs/OpenAPI — https://api.theracingapi.com/documentation ; North America spec mirror — https://apis.io/apis/the-racing-api/the-racing-api-north-america-api/
- TJC Thoroughbred Data Hub launch — https://paulickreport.com/news/people/tjc-innovations-launches-thoroughbred-data-hub-innovation-incubator ; https://www.bloodhorse.com/horse-racing/articles/296112/tjc-unveils-thoroughbred-data-hub-innovation-incubator
- Equibase 2023 research data — https://www.drf.com/news/equibase-offering-2023-racing-data-research
- Brisnet chart file structure — https://support.brisnet.com/hc/en-us/articles/360056090772-Data-File-Structure-for-Comprehensive-Charts-File
- DRF charts — https://www1.drf.com:443/products/charts/charts.html
- NWS API — https://api.weather.gov
