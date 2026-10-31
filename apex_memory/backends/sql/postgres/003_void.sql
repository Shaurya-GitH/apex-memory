-- apex-memory schema, Postgres, migration 3: a decision can be void (an acceptance made in error, withdrawn with a reason).
-- Matches ../sqlite/002_void.sql.

ALTER TABLE decisions DROP CONSTRAINT decisions_status_check;
ALTER TABLE decisions ADD CONSTRAINT decisions_status_check CHECK (status IN ('proposed','accepted','superseded','rejected','void'));
