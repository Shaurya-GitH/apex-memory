-- apex-memory schema, SQLite, migration 2: a decision can be void (an acceptance made in error, withdrawn with a reason).
-- SQLite cannot change a CHECK constraint, so the derived decisions table is rebuilt with its rows.

CREATE TABLE decisions_v2 (
  domain TEXT NOT NULL,
  id TEXT NOT NULL,
  title TEXT,
  status TEXT NOT NULL CHECK (status IN ('proposed','accepted','superseded','rejected','void')),
  section INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
  decided_at TEXT,
  superseded_by TEXT,
  PRIMARY KEY (domain, id));
INSERT INTO decisions_v2 SELECT domain, id, title, status, section, decided_at, superseded_by FROM decisions;
DROP TABLE decisions;
ALTER TABLE decisions_v2 RENAME TO decisions;
