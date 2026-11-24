-- Uploaded documents (programs, past performances, result pages, notes) and what
-- Rein Man extracted from them. Text is extracted in the browser and stored in
-- chunks (D1 rows are limited in size). The Python engine scans pending documents.

CREATE TABLE IF NOT EXISTS documents (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  uploaded_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  filename       TEXT NOT NULL,
  mime           TEXT,
  size_bytes     INTEGER,
  chars          INTEGER NOT NULL,
  race_date      TEXT,                 -- optional hint from the uploader (YYYY-MM-DD)
  track_id       TEXT,                 -- optional hint from the uploader
  research_only  INTEGER NOT NULL DEFAULT 0,  -- 1 = never used for live picks or shown on race pages
  status         TEXT NOT NULL DEFAULT 'pending',  -- pending|processed|error
  processed_at   TEXT,
  summary        TEXT,                 -- JSON summary of what was found
  error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);

CREATE TABLE IF NOT EXISTS document_chunks (
  document_id  INTEGER NOT NULL,
  seq          INTEGER NOT NULL,
  text         TEXT NOT NULL,
  PRIMARY KEY (document_id, seq)
);

CREATE TABLE IF NOT EXISTS document_notes (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id     INTEGER NOT NULL,
  race_id         TEXT,
  program_number  TEXT,
  horse_name      TEXT,
  kind            TEXT NOT NULL,        -- passage|workout|payout|scratch
  text            TEXT NOT NULL,
  data            TEXT                  -- JSON for structured facts
);
CREATE INDEX IF NOT EXISTS idx_docnotes_race ON document_notes(race_id);
CREATE INDEX IF NOT EXISTS idx_docnotes_doc ON document_notes(document_id);
