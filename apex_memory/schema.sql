-- Office AI memory store. Design: KB office-ai/memory/implementation-plan#Schema (D07, D11).
-- Applied once to an empty database; PRAGMA user_version records the schema version.

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);              -- store_id, created_at

CREATE TABLE notes (
  id INTEGER PRIMARY KEY,                    -- explicit so VACUUM INTO keeps the FTS rowids valid
  path TEXT NOT NULL UNIQUE,                 -- 'office-ai/memory/storage-design'
  domain TEXT NOT NULL,                      -- 'office-ai'
  title TEXT, status TEXT, meta JSON,        -- frontmatter; meta keeps the other keys and their order
  summary TEXT,                              -- its index line; NULL = missing from the index
  catalog_position INTEGER,                  -- order of the index catalog rows
  decision_status TEXT, impl_status TEXT, evidence TEXT, verified_at TEXT,   -- page header as fields
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT);

CREATE TABLE sections (
  id INTEGER PRIMARY KEY,                    -- stable id; links point here
  note TEXT NOT NULL REFERENCES notes(path) ON UPDATE CASCADE,
  heading TEXT,                              -- heading text as written; NULL = text before the first heading
  heading_path JSON NOT NULL,                -- '[]' for that text; a duplicate sibling gets ' [2]' etc.
  level INTEGER,
  position INTEGER NOT NULL,
  body TEXT NOT NULL DEFAULT '',             -- own text only, up to the next heading
  generated TEXT,                            -- NULL | 'catalog' | 'status:<kind>' | 'log': rendered, never patched
  version INTEGER NOT NULL DEFAULT 1,
  UNIQUE (note, heading_path));

CREATE TABLE links (
  src_section INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
  dst TEXT NOT NULL,                         -- note path; may be in a domain not in the store
  anchor TEXT NOT NULL DEFAULT '',           -- heading or ^block as written
  dst_section INTEGER REFERENCES sections(id) ON DELETE SET NULL,   -- NULL with an anchor = broken
  PRIMARY KEY (src_section, dst, anchor));
CREATE INDEX links_dst ON links(dst);

CREATE TABLE decisions (                     -- derived from the records' Status lines; never edited directly
  domain TEXT NOT NULL,
  id TEXT NOT NULL,                          -- 'D07', 'P01'
  title TEXT,
  status TEXT NOT NULL CHECK (status IN ('proposed','accepted','superseded','rejected')),
  section INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
  decided_at TEXT,
  superseded_by TEXT,
  PRIMARY KEY (domain, id));

CREATE TABLE status_items (
  id INTEGER PRIMARY KEY,
  domain TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('state','open','blocker','next')),
  text TEXT NOT NULL, refs JSON, position INTEGER, updated_at TEXT, closed_at TEXT);

CREATE TABLE sources (
  id TEXT PRIMARY KEY,
  kind TEXT CHECK (kind IN ('snapshot','live','transcript','web','owner')),
  ref TEXT, captured_at TEXT,
  verify_live INTEGER NOT NULL DEFAULT 0);   -- 1 = re-check the live system before acting on a copy

CREATE TABLE note_sources (
  note TEXT NOT NULL REFERENCES notes(path) ON UPDATE CASCADE ON DELETE CASCADE,
  source TEXT NOT NULL REFERENCES sources(id) ON UPDATE CASCADE,
  PRIMARY KEY (note, source));

CREATE TABLE preferences (                  -- owner preferences layer: read by every agent through the brief
  id INTEGER PRIMARY KEY,
  domain TEXT,                               -- NULL = applies everywhere
  text TEXT NOT NULL,
  owner_quote TEXT NOT NULL,                 -- the owner's own words, required
  recorded_by TEXT, run_id TEXT,
  recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  retired_at TEXT);

CREATE TABLE leases (                       -- a short claim on a section and its subsections
  section INTEGER PRIMARY KEY REFERENCES sections(id) ON DELETE CASCADE,
  actor TEXT, run_id TEXT NOT NULL,
  expires_at TEXT NOT NULL);                 -- UTC, same format as events.ts

CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),   -- UTC; only the importer supplies ts
  domain TEXT, actor TEXT, task_id TEXT, run_id TEXT,
  op TEXT NOT NULL,                          -- 'tool:<name>' = automatic; anything else = narrative log entry
  refs JSON, body TEXT);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;

-- Search. External-content FTS5 tables kept in sync by triggers.
CREATE VIRTUAL TABLE notes_fts USING fts5(title, summary, content='notes', content_rowid='id');
CREATE VIRTUAL TABLE sections_fts USING fts5(heading, body, content='sections', content_rowid='id');
CREATE VIRTUAL TABLE sections_exact USING fts5(body, content='sections', content_rowid='id', tokenize='trigram');
CREATE VIRTUAL TABLE events_fts USING fts5(op, body, content='events', content_rowid='id');

CREATE TRIGGER notes_ai AFTER INSERT ON notes BEGIN
  INSERT INTO notes_fts(rowid, title, summary) VALUES (new.id, new.title, new.summary);
END;
CREATE TRIGGER notes_ad AFTER DELETE ON notes BEGIN
  INSERT INTO notes_fts(notes_fts, rowid, title, summary) VALUES ('delete', old.id, old.title, old.summary);
END;
CREATE TRIGGER notes_au AFTER UPDATE OF title, summary ON notes BEGIN
  INSERT INTO notes_fts(notes_fts, rowid, title, summary) VALUES ('delete', old.id, old.title, old.summary);
  INSERT INTO notes_fts(rowid, title, summary) VALUES (new.id, new.title, new.summary);
END;

CREATE TRIGGER sections_ai AFTER INSERT ON sections BEGIN
  INSERT INTO sections_fts(rowid, heading, body) VALUES (new.id, new.heading, new.body);
  INSERT INTO sections_exact(rowid, body) VALUES (new.id, new.body);
END;
CREATE TRIGGER sections_ad AFTER DELETE ON sections BEGIN
  INSERT INTO sections_fts(sections_fts, rowid, heading, body) VALUES ('delete', old.id, old.heading, old.body);
  INSERT INTO sections_exact(sections_exact, rowid, body) VALUES ('delete', old.id, old.body);
END;
CREATE TRIGGER sections_au AFTER UPDATE OF heading, body ON sections BEGIN
  INSERT INTO sections_fts(sections_fts, rowid, heading, body) VALUES ('delete', old.id, old.heading, old.body);
  INSERT INTO sections_exact(sections_exact, rowid, body) VALUES ('delete', old.id, old.body);
  INSERT INTO sections_fts(rowid, heading, body) VALUES (new.id, new.heading, new.body);
  INSERT INTO sections_exact(rowid, body) VALUES (new.id, new.body);
END;

CREATE TRIGGER events_ai AFTER INSERT ON events BEGIN
  INSERT INTO events_fts(rowid, op, body) VALUES (new.id, new.op, new.body);
END;

PRAGMA user_version = 1;
