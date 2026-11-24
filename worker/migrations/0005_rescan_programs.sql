-- Programs that could not link under the previous scanner can now supply their own cards.
DELETE FROM document_notes WHERE document_id IN (
  SELECT id FROM documents WHERE status = 'processed'
    AND COALESCE(json_array_length(summary, '$.races_linked'), 0) = 0
    AND COALESCE(json_extract(summary, '$.canonical_import'), 0) = 0
    AND (lower(filename) LIKE '%.pdf' OR lower(filename) LIKE '%4x9pp%')
);
UPDATE documents SET status='pending', processed_at=NULL, summary=NULL, error=NULL
WHERE status='waiting' OR (status='processed'
  AND COALESCE(json_array_length(summary, '$.races_linked'), 0) = 0
  AND COALESCE(json_extract(summary, '$.canonical_import'), 0) = 0
  AND (lower(filename) LIKE '%.pdf' OR lower(filename) LIKE '%4x9pp%'));
