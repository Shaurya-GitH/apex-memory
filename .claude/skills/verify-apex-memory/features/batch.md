# Batch

`batch` runs several writes in one transaction. The checks run once against the final state, so notes that link to each other can be created together. A refusal rolls back every op and names the failing op.

## Sub-features

- `batch-pair` two cross-linked notes and a log entry naming both succeed together.
- `batch-single` the same first note alone is `check_failed`.
- `batch-rollback` a refused op rolls back earlier ops and returns `op_index` and `tool`.
- `batch-bad` a tool that batch does not accept is `bad_batch`.
- `batch-ops-record` each refused batch is recorded once in the ops domain.

## How to get to it (user POV)

- Call the MCP tool `batch` with `ops`: a list of `{"tool": name, "args": {...}}`.

## Driving it with scripts/verify

Preconditions:

- The seed domain `atlas`; no notes `atlas/a`, `atlas/b`, `atlas/c`.

- **Scripted journey.** Run `uv run --extra postgres scripts/verify drive batch`. It prints `PASS batch [<backend>]`.
- **Single write.** Create `atlas/a` alone with body `Pairs with [[atlas/b]].`. `error` is `check_failed`.
- **Pair.** Run `scripts/verify call batch` with ops `create_note atlas/a` (links `[[atlas/b]]`), `create_note atlas/b` (links `[[atlas/a]]`) and `log_append` op `LINKED PAIR` naming both pages. `results` has 3 entries; the database holds both notes and one `LINKED PAIR` event.
- **Rollback.** Run a batch of `create_note atlas/c` (complete) and `create_note atlas/d` without summary. `error` is `summary_required`, `op_index` 1, `tool` `create_note`; `atlas/c` does not exist.
- **Bad tool.** Run with `[{"tool": "move", "args": {}}]`. `error` is `bad_batch`.
- **Ops records.** `memory-ops` holds two `tool:refused` events whose refs name `batch`.

Run on both backends.

## Gotchas

- `log_append` ops run last, so they can name pages that other ops in the same batch create.
- Role rules apply per op: a worker's batch with a project note is refused as a whole.
