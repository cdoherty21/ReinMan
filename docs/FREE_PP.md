# Free Past Performance Collector

The collector runs daily (10:35 UTC) and after a successful production deployment, with the same serialized D1 writer group as other data jobs. It uses the existing document scanner and does not buy Firecrawl credits.

Source review: both requested TheFreePPs directory URLs returned HTTP 403 during the 2026-11-26 review. Equibase prohibits automated access and Brisnet requires express written permission. The default collector records these sources as blocked; it makes no automated requests to them.

For a separately verified, permitted source, configure the repository variable `FREE_PP_APPROVED_SOURCES` as a JSON object mapping HTTPS URL prefixes to the documented permission and source label. Only links within explicitly approved prefixes are eligible. Review permission for automated download, local retention, processing and optional private archive export before adding a source. The collector checks robots.txt and rejects redirects and HTTP access refusals, without proxy escalation. Firecrawl is only used following successful ordinary HTTPS access and a verified free-plan balance above 100 credits, at most five basic scrapes per run. Direct PDFs cost zero Firecrawl credits.

The Documents page reports URL and import counts and source errors. The manual workflow's archive option exports originals in ZIP parts below 60 MB, each original below 8 MB. The existing document readers determine supported PP and chart fields; unrecognized layouts require new validated parsers. No historical records are fabricated for inaccessible sources.
