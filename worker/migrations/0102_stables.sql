-- Horse lookups and permanent private watchlist. Existing horse IDs are preserved.
CREATE INDEX IF NOT EXISTS idx_stables_horse ON runners(horse_id, race_id);
CREATE TABLE IF NOT EXISTS stable_favorites (
  horse_id TEXT PRIMARY KEY,
  saved_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS stable_enrichment (
  horse_id TEXT PRIMARY KEY,
  wikidata_id TEXT,
  biography TEXT,
  image TEXT,
  status TEXT NOT NULL,
  retry_after TEXT NOT NULL,
  retrieved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stable_notification_reads (
  event_id TEXT PRIMARY KEY
);
-- The winner importer also creates this table; create it for fresh installations.
CREATE TABLE IF NOT EXISTS historical_winners (
  track_id TEXT NOT NULL, race_date TEXT NOT NULL, race_number INTEGER NOT NULL,
  horse_key TEXT NOT NULL, horse_name TEXT NOT NULL, data TEXT NOT NULL,
  document_id INTEGER NOT NULL, imported_at TEXT NOT NULL,
  PRIMARY KEY (track_id, race_date, race_number, horse_key)
);
CREATE INDEX IF NOT EXISTS idx_stables_archive_key ON historical_winners(horse_key);
