CREATE TABLE IF NOT EXISTS free_pp_urls (
  url TEXT PRIMARY KEY,
  parent_url TEXT,
  label TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  document_id INTEGER REFERENCES documents(id),
  error TEXT,
  checked_at TEXT
);
