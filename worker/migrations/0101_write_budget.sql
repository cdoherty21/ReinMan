-- D1 write budget (see docs/D1_BUDGET.md).
-- 1. Each job run records the rows it wrote, as D1 reported them (meta.rows_written).
ALTER TABLE job_runs ADD COLUMN rows_written INTEGER;
-- 2. Lookup indexes on runners by horse, jockey and trainer serve no query (history is
--    read by race date; the trainer table is a full GROUP BY), yet each new runner paid
--    three extra index writes for them. Dropping an index removes no data and can be
--    reversed with CREATE INDEX at any time.
DROP INDEX IF EXISTS idx_runners_horse;
DROP INDEX IF EXISTS idx_runners_jockey;
DROP INDEX IF EXISTS idx_runners_trainer;
