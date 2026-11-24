CREATE TABLE IF NOT EXISTS document_upload_fingerprints (
  fingerprint TEXT PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE
);
