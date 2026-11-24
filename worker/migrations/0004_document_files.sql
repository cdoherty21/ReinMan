-- Originals are stored separately from extracted text. Earlier uploads keep their text view.
CREATE TABLE IF NOT EXISTS document_file_chunks (
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  seq         INTEGER NOT NULL,
  bytes       BLOB NOT NULL,
  PRIMARY KEY (document_id, seq)
);

-- Reapply facts from documents that were previously excluded from the race reader.
DELETE FROM document_notes
WHERE document_id IN (
  SELECT id FROM documents WHERE research_only = 1 AND status = 'processed'
    AND COALESCE(json_extract(summary, '$.canonical_import'), 0) = 0
);
UPDATE documents SET status = 'pending', processed_at = NULL, summary = NULL, error = NULL
WHERE research_only = 1 AND status = 'processed'
  AND COALESCE(json_extract(summary, '$.canonical_import'), 0) = 0;
UPDATE documents SET research_only = 0 WHERE research_only = 1;
