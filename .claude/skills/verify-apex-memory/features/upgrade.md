# Upgrade

A user who installed apex-memory with `uv tool install git+...` upgrades with one command, `uv tool upgrade apex-memory`, and restarts the MCP client. The first open of the store by the new server writes a verified safety backup, migrates under the writer lock, logs the migration in the ops domain, and keeps every page as it was. `apex-memory status` shows the state, and the old version can restore the backup.

## Sub-features

- `up-install` v0.1.0 installs from a git remote into a scratch uv tool folder and writes knowledge (CLI import, MCP create_note, patch_section, log_append).
- `up-upgrade` `uv tool upgrade apex-memory` moves the tool to the branch's HEAD commit; `--version` shows the new version.
- `up-status` `apex-memory status` before the first open shows the pending migration and changes nothing; after it, `up to date` and the safety backup as the last backup.
- `up-pages` after the restart every page reads the same through MCP.
- `up-backup` one `pre-migration-*-v<old>-to-v<new>.db` in the default folder; the ops domain has one `MIGRATION` entry naming it.
- `up-restore` the new version and the reinstalled v0.1.0 both import the backup into a new store with the pre-upgrade pages.
- `up-old-refuses` v0.1.0 refuses the migrated store and says to upgrade.

## How to get to it (user POV)

- `uv tool install "git+https://github.com/Shaurya-GitH/apex-memory"`, later `uv tool upgrade apex-memory`, then restart the MCP client.
- `apex-memory status` and `apex-memory --version` in a terminal.
- Roll back: `uv tool install "git+...@v0.1.0"`, then `apex-memory import <pre-migration backup> --url <new store>`.

## Driving it with scripts/verify

Preconditions:

- The checkout has the tag `v0.1.0` and every change is committed: the journey upgrades to `git rev-parse HEAD`, not to the working tree.
- `uv` is on `PATH` and can reach PyPI (or its cache) for the dependencies.
- `scripts/verify doctor` prints `OK`.

- **Scripted journey.** Run `uv run --extra postgres scripts/verify drive upgrade`. It prints `PASS upgrade [<backend>]`. It takes about a minute.
- **Install v0.1.0.** The journey clones the checkout to `.verify/<run>/work/upgrade-<n>/remote.git`, points its `main` at `v0.1.0`, and runs `uv tool install "apex-memory[postgres] @ git+file://<remote>"` with `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` in the journey's work folder. `apex-memory --help` has no `status`.
- **Old knowledge.** The old CLI imports the folders `garden` and `memory-ops` into a new empty store; an old MCP server creates `garden/compost`, appends to `Raised beds` and logs `PLAN COMPOST`. The journey reads every non-ops page through MCP.
- **Upgrade.** `main` moves to HEAD and `uv tool upgrade apex-memory` runs: exit 0 and the output names the HEAD commit. `apex-memory --version` prints `apex-memory <checkout version>`.
- **Status before.** `apex-memory status --url <store>`: `1 pending migration` and `last backup: none`; the schema version is unchanged.
- **Restart.** A new MCP server opens the store: every page reads the same; `list_domains` works; the schema version equals the number of migration files.
- **Backup.** SQLite: `.verify/<run>/db/backups/`; Postgres: `<run HOME>/.apex-memory/backups/`. Exactly one `pre-migration-*-v<old>-to-v<new>.db`; `memory-ops` has one `MIGRATION: schema <old> to <new>` event whose refs give `from`, `to` and that path; `status` shows `up to date` and `last backup: <that path>`.
- **Restore.** `apex-memory import <backup> --url <new store>` with the new version, then with v0.1.0 reinstalled from the tag: both exit 0 and both stores read the pre-upgrade pages.
- **Old refuses.** v0.1.0 `apex-memory brief garden --url <migrated store>` exits non-zero and its message says to upgrade.

Run on both backends.

## Gotchas

- Uncommitted changes are not in the upgrade: commit first, or the journey proves the previous commit.
- The upgrade starts from v0.1.0, whose Postgres schema is version 2 and SQLite schema version 1; the backup name carries these numbers.
- v0.1.0's refusal text for a newer schema is fixed in that release ("upgrade it"); the message that names the MCP client restart starts with 0.2.0.
- pip needs `--force-reinstall` to move to a new commit with the same version number; the journey covers uv only.
- The Postgres backup is a SQLite file; restore it with `apex-memory import` into any backend.
