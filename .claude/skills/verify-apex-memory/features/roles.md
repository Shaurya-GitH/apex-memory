# Roles

The role is set when the server is registered. An owner can use every tool. A worker reads everything, writes only its own working notes under `<domain>/working/<actor>/` and appends log entries; the owner promotes a worker's note into project knowledge with `move`.

## Sub-features

- `roles-worker-write` a worker creates and appends to its own working notes and appends log entries.
- `roles-worker-refused` a worker's project note, project section edit, or note in another actor's folder is `not_allowed`; its promotion attempt is `owner_only`.
- `roles-promote` the owner's `move` out of the working folder needs a summary and header and adds the note to the index.

## How to get to it (user POV)

- Register the server with `apex-memory serve --actor <name> --role worker` (or `owner`).
- Promote with the MCP tool `move` from `<domain>/working/<actor>/<name>` to `<domain>/<name>`.

## Driving it with scripts/verify

Preconditions:

- The seed working note `atlas/working/verify-worker/draft`.

- **Scripted journey.** Run `uv run --extra postgres scripts/verify drive roles`. It prints `PASS roles [<backend>]`.
- **Worker writes.** Run `scripts/verify call create_note '{"path": "atlas/working/verify-worker/notes", "title": "Notes", "body": "Worker notes."}' --role worker`: `ok`. Append to the draft's `Draft` section: `ok`. `log_append` to `atlas` naming `atlas/api`: `ok`.
- **Worker refused.** A project note `atlas/worker-page`, an append to `atlas/api` `Auth`, and a note under `atlas/working/verify-owner/`: each `not_allowed`. `move` of its draft to `atlas/zephyr`: `owner_only`.
- **Promote.** As owner, `move` without summary: `promotion_needs_summary_and_header`. With `"summary": "The zephyr cache idea"` and the three header fields: `promoted` true; `read atlas/index` holds `| [[atlas/zephyr]] | The zephyr cache idea |`.
- **Side effects.** Write events carry `verify-worker` as actor; `memory-ops` holds four `tool:refused` events by `verify-worker`.

Run on both backends.

## Gotchas

- Roles are declared, not authenticated: one machine, one owner.
- A worker's refused calls are recorded in the ops domain under its actor name.
