# apex-memory verification map

This directory is the maintained source for verifying the user-facing behaviour of apex-memory. Read this index before driving the store, then use the matching feature file as the recipe. Each file has a scripted journey: `scripts/verify drive <feature id>` runs exactly the bullets in its `Driving it with scripts/verify` section.

## Baseline preconditions

- A run launched with `uv run --extra postgres scripts/verify launch --backend sqlite` or `--backend postgres`.
- `uv run --extra postgres scripts/verify doctor` prints `OK` for that run.
- The seed store holds domains `atlas`, `website` and `memory-ops` (ops), the notes `atlas/api`, `atlas/roadmap`, `website/launch`, decisions D01, D02 (accepted) and P01 (proposed), the open status item `Write the API docs`, the log entry `SEED` and the working note `atlas/working/verify-worker/draft`.
- Never drive a store that this run did not create.

## Driving conventions

- `drive` gives each journey a fresh clone of the seed and two MCP servers on it: `verify-owner` (role owner) and `verify-worker` (role worker).
- Manual steps use `scripts/verify call <tool> '<json args>' [--role worker]`, always prefixed with `uv run --extra postgres`. They act on the run's `manual` store, which keeps changes between calls.
- CLI steps run `apex-memory <command> --url <store>` with the run's `HOME`.
- Treat tool names, argument names and refusal codes as literal.

## Proof and skip reporting

- Capture the call and the resulting state: every journey writes `transcript.jsonl` and `result.json` under `.verify/<run>/evidence/<feature>-<n>/`.
- A mutation is proven by a second view (`read`, `brief`, `list_domains`, `log_query`) and by rows read straight from the database.
- A refusal is proven by its `error` code, by the absence of its side effect, and (with the ops domain) by its `tool:refused` record.
- Run a feature on both backends when its file says so; report each backend separately.
- Report an unreachable step with the command and the unmet precondition. Do not report a step as verified through another path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behaviour. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behaviour.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with scripts/verify` starts with `Preconditions:` and uses labelled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

Keep implementation details out of the map. Name only user paths, stable handles, required state, commands and observable proof.

## Features

- [Domain discovery and the ask-once flow](./domain-discovery.md): `list_domains`, descriptions, global preferences, create on yes, record never, worker gap report.
- [Brief](./brief.md): the start-of-task read for owner and worker, unknown domain, CLI brief.
- [Search](./search.md): bm25, exact, regex, scope, working-note visibility, bad modes and patterns.
- [Read](./read.md): whole note, one section, candidates for a missed heading or path.
- [Patch a section](./patch-section.md): replace, append, prepend, replace_own_text, versions, summary.
- [Create a note](./create-note.md): project and working notes, catalog line, links, refusals.
- [Batch](./batch.md): cross-linked notes in one transaction, all or nothing.
- [Decisions](./decisions.md): accept, supersede, void with reason, append-only records.
- [Status items](./status-items.md): add, update, close, brief and status page.
- [Roles](./roles.md): worker limits, owner promotion of a working note.
- [Refusals in the ops domain](./ops-refusals.md): automatic records, redaction, friction log.
- [Store administration](./store-admin.md): CLI check, markdown import and verify, backup, move a store.
- [Upgrade](./upgrade.md): v0.1.0 to this branch with `uv tool upgrade`, safety backup, status, restore and roll back.

Not mapped yet: `links`, `log_query` filters beyond op, `lease`, `source`, `patch_header`.
