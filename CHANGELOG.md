# Changelog

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
