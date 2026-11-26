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
| **Equibase research dataset (2023)** | Every past performance + result chart for 2023 Equibase-charted races | 2023 | One-off | Research terms apply | Announced as complimentary | Official | Optional: import for model bootstrapping |
| **Brisnet** | PP data files, comprehensive chart files (6 comma-delimited files per card) | Long | Daily | Purchased per card/subscription; files for personal use | Paid per card / subscription | Long-standing | **Not implemented**: I could not verify the field layout of the PP single-file format, and Rein Man won't parse a format from guesses. Brisnet documents the chart file structure in its help center; once you have a file + spec, a converter to the canonical format is a small, testable job |
| **DRF** | PPs, text charts back to 1993 (comma-delimited) | Since 1993 | Daily | Subscription | Paid | Long-standing | Not implemented (paid; same path as Brisnet) |
| **Official track websites** | Entries/results/scratches per track | Varies | Varies | Each site's own terms; formats differ per track; most results come from Equibase anyway | Free to view | Inconsistent | Not used (no uniform, permitted feed) |
| **Third-party Equibase scrapers** (Apify actors, parse.bot) | Entries/results | — | — | Unofficial; they scrape pages whose owner prohibits it | Some free tiers | Fragile | **Rejected** — they don't make the underlying access permitted |
| **Horse Racing Nation** | Free entries/results website (Equibase partnership) | — | — | Website, not an API | Free | — | Not used |

## Free sources

* [PuntersEdge](https://puntersedge.online/us-racing-odds-api) supplies the TVG
  board, snapshots of live win odds, scratches and final results with payouts.
  Private personal use fits its free tier, which has a 1,500-credit monthly cap.
  The board and each results page cost two credits. Coverage follows TVG and
  the feed does not supply speed figures, past performances or workouts.
* [OffTrackBetting.com](https://www.offtrackbetting.com/results/) results pages are
  read automatically through Firecrawl's scrape API when `FIRECRAWL_API_KEY` is set
  (`engine/reinman/sources/otb_live.py`): finish order (top four), win/place/show,
  exotic and multi-race payoffs, and scratches for every race at a track on one page.
  Results only, no cards or odds. Saved single-race pages can still be loaded with
  `reinman import-otb`. See [setup](SETUP.md#automatic-offtrackbettingcom-results).

  **Terms, checked 2026-11-24.** OTB's [robots.txt](https://www.offtrackbetting.com/robots.txt)
  allows general crawlers everywhere except `/core`, `/member` and one legacy path.
  Its [Terms & Conditions](https://www.offtrackbetting.com/terms.html) contain no clause
  about robots, scraping or automated access; section 5 licenses the service "for
  personal, non-commercial use only", which is how Rein Man uses it. One clause bars
  creating "code, software or other program that incorporates any elements of the
  Service"; it reads as aimed at copying OTB's app, but OTB could read it more broadly,
  so this source is kept small and stops at the first refusal:
  * a track's page is read only while Rein Man has a race there that has run without an
    official result, at most once per `OTB_MIN_INTERVAL_MIN` (default 60) minutes;
  * at most `OTB_DAILY_PAGES` (default 60) page reads a day across all jobs;
  * Firecrawl's **basic** proxy only — a 403 or 429 stops OTB reads for the day instead
    of retrying through other proxies;
  * Rein Man stays private and non-commercial. If that changes, stop using this source.

  OTB's [RSS results feed](https://www.offtrackbetting.com/rss.html) is not used: it
  carries headlines for major stakes only, and its terms limit it to display with links.
* **Equibase result charts you download yourself** (the free full chart PDF on
  equibase.com, for personal use) can be uploaded on the Documents page. Rein Man reads
  each race's finish order, payoffs, scratches, fractional and final times, and every
  starter's running line: position and lengths behind at each call, odds, weight,
  jockey and trip comment (`engine/reinman/charts.py`). Results are stored only when the
  card matches; running lines are kept per horse in `chart_lines` and feed that horse's
  later races (early position, pace scenario, beaten lengths, trouble). Rein Man never
  downloads charts itself: Equibase's terms prohibit automated access.
* [Equibase's 2023 dataset](https://www.jockeyclub.com/default.asp?area=10&section=Resources&story=1452)
  was announced as complimentary for research and development. Its contents
  require conversion to canonical JSON before import.

## What this means in practice

* **The free feed covers a subset of racing and data fields.** PuntersEdge follows TVG's
  coverage and has a monthly credit cap; it does not supply past-performance lines,
  speed/pace figures, running positions, beaten lengths, trouble comments or workouts.
  Rein Man builds horse, jockey, trainer, sire and post-position history from the
  results it collects every day, and labels the missing factors as *unavailable*.
* **The Racing API was removed** — its U.S. data needs a paid add-on.
* **Model quality grows with history.** Until enough graded races exist for out-of-sample
  validation, predictions come from the clearly labeled `v0-market-baseline` model.

## Sources consulted
- OffTrackBetting.com Terms & Conditions — https://www.offtrackbetting.com/terms.html ; robots.txt — https://www.offtrackbetting.com/robots.txt ; RSS terms — https://www.offtrackbetting.com/rss.html
- Equibase Terms of Use — https://www.equibase.com/about/terms.cfm
- DRF on Equibase outages and scraping — https://www.drf.com/news/equibase-website-back-almost-100-percent-following-attacks
- The Racing API docs/OpenAPI — https://api.theracingapi.com/documentation ; North America spec mirror — https://apis.io/apis/the-racing-api/the-racing-api-north-america-api/
- TJC Thoroughbred Data Hub launch — https://paulickreport.com/news/people/tjc-innovations-launches-thoroughbred-data-hub-innovation-incubator ; https://www.bloodhorse.com/horse-racing/articles/296112/tjc-unveils-thoroughbred-data-hub-innovation-incubator
- Equibase 2023 research data — https://www.drf.com/news/equibase-offering-2023-racing-data-research
- Brisnet chart file structure — https://support.brisnet.com/hc/en-us/articles/360056090772-Data-File-Structure-for-Comprehensive-Charts-File
- DRF charts — https://www1.drf.com:443/products/charts/charts.html
- NWS API — https://api.weather.gov

## The Stables enrichment

The existing daily cycle checks at most five horse identities against the
Wikidata Action API, prioritizing active horses and watchlist entries. A match
requires the same name, foaling year derived from the documented race-age
record, and independently reported sire; ambiguous candidates are left
unverified. Verified P18 media is checked using the Commons Imageinfo API.
Only supported openly licensed images with creator and source metadata are
displayed, with attribution. The pipeline stops on network errors, respects the
existing D1 background quota and schedules incomplete records for retry after
30 days. It requires no additional credentials.

Sources: [Wikidata licensing](https://www.wikidata.org/wiki/Wikidata:Licensing),
[Wikimedia API access policy](https://www.mediawiki.org/wiki/Wikimedia_APIs/Access_policy),
[API limits](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits),
[Commons image metadata](https://www.mediawiki.org/wiki/API:Imageinfo).
