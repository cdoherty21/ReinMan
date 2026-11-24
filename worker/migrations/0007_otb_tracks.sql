-- OTB results page id and slug for each Rein Man track, learned from OTB's daily
-- thoroughbred index (engine/reinman/sources/otb_live.py also creates this if missing).
CREATE TABLE IF NOT EXISTS otb_tracks (
  track_id TEXT PRIMARY KEY,
  otb_id   INTEGER NOT NULL,
  slug     TEXT NOT NULL,
  seen_at  TEXT
);
