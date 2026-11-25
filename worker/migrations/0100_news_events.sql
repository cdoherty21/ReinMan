-- Text of news items and posts (for event extraction) and the events linked to runners
-- (engine/reinman/news.py, news_events.py; the engine also creates these if missing).
CREATE TABLE IF NOT EXISTS news_bodies (url TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_events (
  race_id        TEXT NOT NULL,
  program_number TEXT NOT NULL,
  horse_name     TEXT,
  role           TEXT NOT NULL,     -- horse|jockey|trainer
  entity         TEXT,
  kind           TEXT NOT NULL,     -- scratch|jockey_change|equipment|barn_trouble|health
  source         TEXT,
  title          TEXT,
  url            TEXT NOT NULL,
  published_at   TEXT,
  first_seen_at  TEXT NOT NULL,
  sentence       TEXT,
  PRIMARY KEY (race_id, program_number, kind, url)
);
