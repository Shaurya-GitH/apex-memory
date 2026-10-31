# Store administration

The `apex-memory` CLI checks a domain, imports a folder of markdown notes and proves it renders back identically, writes a verified backup, and copies a whole store (a backup) into a new store, which is how a store moves from SQLite to Postgres.

## Sub-features

- `admin-check` `apex-memory check <domain>` exits 0 with `no problems found`.
- `admin-import` `apex-memory import <folder>` imports a domain; `verify <folder>` proves it renders back; a second import is `domain_exists`.
- `admin-backup` `apex-memory backup --to <dir>` writes a SQLite file.
- `admin-move` `apex-memory import <backup> --url <new store>` copies every row and the store identity; a second copy, or a copy into a store that holds knowledge, is refused.

## How to get to it (user POV)

- Run `apex-memory check|import|verify|backup|brief ... --url <store>` in a terminal.

## Driving it with scripts/verify

Preconditions:

- The seed store with no check problems; `docker` for the Postgres run.

- **Scripted journey.** Run `uv run --extra postgres scripts/verify drive store-admin`. It prints `PASS store-admin [<backend>]`.
- **Check.** `apex-memory check atlas --url <store>`: exit 0, `atlas: no problems found`.
- **Import.** The journey writes `garden/index.md` and `garden/beds.md` in `.verify/<run>/work/<journey>/`, then runs `apex-memory import <folder>`: exit 0 and `"domain": "garden"`. `apex-memory verify <folder>`: exit 0 and `every page renders back identically`. A second import exits non-zero with `domain_exists`. MCP `read garden/beds` holds `Three beds`.
- **Backup.** `apex-memory backup --to <work>/backups --keep 3`: exit 0 and stdout is the path of an existing file.
- **Move.** On a new empty store of the run's backend, `apex-memory import <backup> --url <new>`: exit 0 with row counts. `apex-memory brief atlas` and `brief garden` are the same on both stores from the second line on; notes, sections, links, decisions and preferences have the same counts; the store id is the same. A second copy into it is refused with `copy into a new, empty store`; a copy into another store that holds the garden import is refused with `already holds knowledge`.

Run on both backends: on Postgres the move is SQLite backup to Postgres, the README's move procedure.

## Gotchas

- `import --fresh` deletes only SQLite files whose name contains `test`; the harness never uses it.
- A store copy keeps the store id, so the copy refuses a second copy as "into itself" before it checks for knowledge.
- The CLI acts as actor `cli`, role owner.
