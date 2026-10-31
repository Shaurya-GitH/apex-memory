---
name: verify-apex-memory
description: Drive apex-memory the way an agent does (the MCP stdio server, the apex-memory CLI, on SQLite and on Postgres) and capture evidence that a change works. Use it to prove a store, tool, refusal, CLI or upgrade change end to end, before you call it done, or when asked to "verify", "e2e" or "prove" an apex-memory change.
---

# Verify apex-memory

The harness is `scripts/verify` (Python, in this repo). It starts the real `apex-memory serve` over stdio, talks to it with the MCP Python client the way an agent's MCP client does, runs the real `apex-memory` CLI, and reads the database directly to prove side effects. The feature map in [features/](features/README.md) is the maintained list of user-facing features and their journeys. Keep it current with `/pstack:maintain-verification-skill`.

Surfaces, in order of importance: the MCP stdio server (primary), the `apex-memory` CLI, and the two backends (a SQLite file; Postgres 17 in a throwaway Docker container).

Isolation: each run has its own folder `.verify/<run>/` with its own `HOME`, its own databases and (Postgres) its own container on a free port. The harness strips `APEX_MEMORY_*` variables, always passes `--url`, and refuses any SQLite path under `~/.apex-memory` or `~/.office-ai`. Never point it at a real store. Two runs can exist side by side; commands act on the latest launched run unless you give `--run <name>`.

Run every command from the repository root. Always use `uv run --extra postgres`: a plain `uv run` syncs the environment and removes `psycopg`.

## Launch

```sh
uv run --extra postgres scripts/verify launch --backend sqlite            # or --backend postgres; optional --run <name>
```

Ready means the last line is `READY run=<name> backend=<backend> store=<url> home=<dir>`. Launch:

1. Creates `.verify/<run>/{home,db,work,evidence}` and writes `.verify/<run>/state.json` and `.verify/latest`.
2. Postgres only: `docker run -d --rm --name apex-memory-verify-<run> --label apex-memory-verify=<run> -p 127.0.0.1:<free port>:5432 postgres:17`, then waits until a TCP connection works (the image's init server listens on a socket only, so a TCP connection means the final server), then `CREATE DATABASE seed`.
3. Runs migrations with the real CLI: `apex-memory init --url <seed url>`.
4. Seeds the baseline: domains `atlas`, `website` (with descriptions that name their limits) and the ops domain `memory-ops`; notes `atlas/api` (sections `Rate limits (2026-10-01)` with commit `9f3c2d1`, `Auth`), `atlas/roadmap` (links `[[atlas/api#Auth]]`), `website/launch`; decisions D01, D02 (accepted) and P01 (proposed) on `atlas/decisions`; open status item `Write the API docs`; log entry `SEED`; the worker's working note `atlas/working/verify-worker/draft` (word `zephyr`). Launch fails if any seeded domain has a check problem.

The seed store is a template: no journey writes to it. `drive` clones it per journey (SQLite: a file copy; Postgres: `CREATE DATABASE <name> TEMPLATE seed`), so every journey starts from the baseline.

## Doctor

```sh
uv run --extra postgres scripts/verify doctor
```

Read-only. Prints `OK version <v> · store <id> · <url> · HOME <dir>` or one `BAD <reason>` line per problem (exit 1). It checks that `apex_memory` and the `apex-memory` command come from this checkout, the package version matches the launch, `HOME` is the scratch home, the Postgres container is running with this run's label and port, the seed store id is the one launch created, its schema version equals the number of migration files, and an MCP server on the seed lists exactly the store's `TOOLS` and names this store in its instructions. Run it first whenever anything looks off.

## Drive

```sh
uv run --extra postgres scripts/verify features                 # the mapped feature ids
uv run --extra postgres scripts/verify drive <feature>          # one journey, e.g. drive domain-discovery
uv run --extra postgres scripts/verify all                      # every journey; exit 1 if any fails
uv run --extra postgres scripts/verify call <tool> '<json args>' [--role worker]
```

- `drive` starts two MCP servers on a fresh clone: `apex-memory serve --actor verify-owner --role owner` and `--actor verify-worker --role worker`. It runs the journey in the feature's file, then prints `PASS|FAIL <feature> [<backend>] n/m checks · evidence <dir>` and each failed check with its detail.
- `call` is for manual driving: one tool call over MCP on the run's `manual` store (a clone of the seed made on first use; it keeps your changes across calls). Example: `scripts/verify call read '{"path": "atlas/api", "heading": "Auth"}'`.
- A refusal comes back as a normal tool result `{"ok": false, "error": "<code>", ...}`; the journeys assert the code, not the text.
- To drive a new behaviour, add a journey function decorated with `@feature` in `scripts/verify` and a matching file in `features/`.

## Evidence

Each drive writes `.verify/<run>/evidence/<feature>-<n>/`:

- `transcript.jsonl`: every MCP call (role, tool, arguments, full result), every CLI command (arguments, exit code, stdout, stderr) and every direct database query (SQL and rows), each tagged with its step.
- `result.json`: feature, backend, store URL (password masked), commit, pass or fail, and every check with its step.

`all` also writes `.verify/<run>/evidence/all-<timestamp>.json`. Proof standards: drive the real MCP and CLI paths, never `Store` methods; prove each mutation with a second view (a `read`, `brief` or `list_domains`) and with rows read straight from the database (notes, links, decisions, status_items, preferences, events, including `tool:refused` records in the ops domain); a refusal is proven by its code and by the absence of its side effects. Do not weaken a journey to make it pass: if the seed lacks a precondition, the journey fails and the reason goes in that feature's Gotchas.

## Cleanup

```sh
uv run --extra postgres scripts/verify cleanup                  # or --run <name>
```

Removes only what the run started: its container (`docker rm -f apex-memory-verify-<run>`, by exact name from `state.json`), and `home/`, `db/` and `work/`. It keeps `.verify/<run>/evidence/` and `state.json` and prints `CLEANED run=<name>; evidence kept at <dir>`. Never kill processes or containers by pattern. Run cleanup after failed attempts too.

## Helpers

- `scripts/verify` (executable): `launch`, `doctor`, `features`, `drive`, `all`, `call`, `cleanup`. `uv run --extra postgres scripts/verify --help` lists them.
- `.verify/` is in `.gitignore`.
