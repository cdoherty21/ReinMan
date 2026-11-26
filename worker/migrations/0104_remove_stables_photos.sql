-- Remove downloaded photograph metadata while retaining independently sourced biographies.
UPDATE stable_enrichment SET image=NULL WHERE image IS NOT NULL;
