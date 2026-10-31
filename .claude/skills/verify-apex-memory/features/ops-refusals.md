# Refusals in the ops domain

When the ops domain exists, the store records every refusal and error as a `tool:refused` or `tool:error` event there, with the tool, its arguments (long text and content replaced by their length) and the result. Agents also log friction there with `log_append`.

## Sub-features

- `ops-record` a refusal writes one event with `{"tool", "error"}` refs and the caller's actor.
- `ops-redact` content and long text are stored as `<N chars>`, never as text.
- `ops-friction` a `FRICTION:` log entry is found by `log_query`; `include_tool` shows the refusal records.
- `ops-unknown-pages` `log_append` naming a missing page is `unknown_pages`.

## How to get to it (user POV)

- Any refused tool call; then `log_query` on the domain that `list_domains` marks `"ops": true`.
- `log_append(domain=<ops domain>, op="FRICTION: ...", pages=[...], source=..., outcome=...)`.

## Driving it with scripts/verify

Preconditions:

- The seed ops domain `memory-ops` with no refusal records.

- **Scripted journey.** Run `uv run --extra postgres scripts/verify drive ops-refusals`. It prints `PASS ops-refusals [<backend>]`.
- **Record.** Run `scripts/verify call read '{"path": "atlas/nowhere"}'`: `not_found`. The database holds one `tool:refused` event in `memory-ops` with refs `{"tool": "read", "error": "not_found"}` and actor `verify-owner`.
- **Redact.** Create `atlas/leak` whose body is 340 characters of `SECRET-PARAGRAPH` and a broken link: `check_failed`. The newest record's body has no `SECRET-PARAGRAPH` and holds `chars>`.
- **Friction.** Run `scripts/verify call log_append '{"domain": "memory-ops", "op": "FRICTION: verify journey", "pages": ["memory-ops/log"], "source": "verify", "outcome": "logged"}'`. `log_query` with `"op": "FRICTION"` returns exactly that entry; with `"include_tool": true` it shows two `tool:refused` entries.
- **Unknown pages.** `log_append` naming `memory-ops/nope`: `unknown_pages`.

Run on both backends.

## Gotchas

- Without an ops domain nothing is recorded; the seed creates `memory-ops`.
- A refusal inside `batch` is recorded once, for the batch, not per op.
