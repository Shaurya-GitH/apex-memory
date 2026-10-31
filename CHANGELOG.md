# Changelog

## 0.2.0 — 2026-10-31

Fixes from 16 days of real use (12 to 28 October 2026): about 60 refusals and 17 agent reports.

- `replace_own_text` with the own text unchanged does not change the page. Use it with `summary` on `heading=[]` to
  change only the summary.
- `patch_section` `append` does not need `if_version`. The store does not compare it. `replace`, `prepend` and
  `replace_own_text` without `if_version` get the refusal `version_required`.
- New tool `batch`: a list of writes in one transaction, all or nothing. The check runs one time, after the last
  write, so two notes can link to each other. A refusal gives `op_index`.
- Smarter checks. The store counts the copies of each paragraph: a write that removes one copy is permitted. When a
  domain starts to use Status lines, older records without one are old problems; only a new record is checked. A note
  whose only section is the text before the first heading can get headings with `append` or `replace`.
- New `patch_section` operation `replace_own_text`: replaces only the text before the first subsection.
- New tools `create_domain` (owner only) and `list_domains` (all roles). Each domain has a description: the summary of
  its index. `check` shows an index without a summary. The server instructions start with `list_domains`.
- The agent selects domains by the subject of the work. If no domain agrees, the agent asks the owner one time if
  it must create one. A "never" answer is a preference without a domain. `list_domains` gives these preferences as
  `preferences`. A worker reports a missing domain to the session of the owner.
- `set_decision_status` can change `accepted` to `void`, with a `reason`. The brief shows void decisions on their own
  line. Schema migrations: SQLite 002, Postgres 003.
- `status_item` action `update` changes the text, and as an option the kind, of an open item.
- `read` and other tools that find a heading give `candidates` when they cannot find it, also for a heading without
  its date. An unknown path gives the nearest note paths. `read` without a path gives `path_required`.
- On Postgres, a `regex` pattern that Postgres refuses, for example a repetition count above 255, gets `bad_regex`.
- Search: `bm25` ranks hits that have more of the query words higher and has a relevance floor. `exact` finds full
  words only. Snippets start and stop at the end of a word.
- Safe upgrades. Before a store migrates, the store writes a verified backup (`pre-migration-...db`) beside a SQLite
  store, or in `~/.apex-memory/backups` for Postgres, or in `APEX_MEMORY_BACKUP_DIR`. If the backup fails, the store
  does not migrate. The migration runs under the writer lock: of two servers that start together, only one migrates.
  The ops domain gets a `MIGRATION` log entry with the path of the backup.
- New CLI command `apex-memory status` (read-only) and option `--version`. An old package that opens a newer schema
  tells you to upgrade the package and restart the MCP client. `backup --to` defaults to `APEX_MEMORY_BACKUP_DIR`.
- Write events record the shape of each write (characters, lines, headings, sections created, changed and removed,
  note created), not the new text.

## 0.1.0 — 2026-10-30

First release, extracted from a private project.

- MCP server (`apex-memory serve`) with the tools brief, search, read, links, log_query, patch_section,
  patch_header, create_note, move, status_item, set_decision_status, log_append, preference, source, lease and check.
  Owner and worker roles, working notes, leases, owner preferences, and automatic records of refusals and errors in an
  ops domain (`--ops-domain`, default `memory-ops`).
- Every write is one transaction that runs the checks and rolls back on any new problem; the log is append-only.
- Databases behind one small `Backend` interface, chosen by URL (`--url` or `APEX_MEMORY_URL`): SQLite (default,
  `~/.apex-memory/memory.db`) and Postgres (`pip install 'apex-memory[postgres]'`).
- CLI: `init`, `serve`, `import` (a folder of markdown notes, or a whole store such as a SQLite file or a backup),
  `verify`, `brief`, `check`, `backup`.
- `search` without a scope searches every domain.
