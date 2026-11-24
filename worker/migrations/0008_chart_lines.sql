-- Running lines read from uploaded Equibase result charts (engine/reinman/charts.py),
-- one per horse per charted race. Keyed by normalized horse name so a line feeds that
-- horse's later races; the engine also creates this table if it is missing.
CREATE TABLE IF NOT EXISTS chart_lines (
  horse_key    TEXT NOT NULL,
  race_date    TEXT NOT NULL,
  track_id     TEXT NOT NULL,
  race_number  INTEGER NOT NULL,
  horse_name   TEXT NOT NULL,
  data         TEXT NOT NULL,
  document_id  INTEGER,
  added_at     TEXT,
  PRIMARY KEY (horse_key, race_date, track_id, race_number)
);
