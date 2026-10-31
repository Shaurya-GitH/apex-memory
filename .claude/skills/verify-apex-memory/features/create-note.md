# Create a note

`create_note` adds a project note (with summary and page header, listed in the index catalog) or a working note (neither, kept out of the index). It never overwrites, and a note that adds a check problem such as a broken link is rolled back.

## Sub-features

- `cn-project` a project note gets its catalog line and its links.
- `cn-required` missing summary or header fields are refused.
- `cn-exists` a second create of the same path is `exists`.
- `cn-check` a broken link is `check_failed` naming the link; nothing is written.
- `cn-working` a working note needs no summary and stays out of the index.

## How to get to it (user POV)

- Call the MCP tool `create_note` with `path`, `title`, `summary`, `body`, `decision_status`, `impl_status`, `evidence`.

## Driving it with scripts/verify

Preconditions:

- The seed domain `atlas` with no check problems.

- **Scripted journey.** Run `uv run --extra postgres scripts/verify drive create-note`. It prints `PASS create-note [<backend>]`.
- **Project note.** Run `scripts/verify call create_note '{"path": "atlas/limits-faq", "title": "Limits FAQ", "summary": "Answers about rate limits", "body": "# Limits FAQ\n\nSee [[atlas/api#Auth]] for tokens.\n", "decision_status": "agreed", "impl_status": "planned", "evidence": "2026-10-31 — verify"}'`. `read atlas/index` shows `| [[atlas/limits-faq]] | Answers about rate limits |`; the links table holds `atlas/api` with anchor `#Auth`.
- **Required fields.** Without `summary`: `summary_required`. Without the three header fields: `header_required`.
- **Exists.** Repeat the first call. `error` is `exists`.
- **Broken link.** Create `atlas/broken` with body `See [[atlas/nowhere]].`. `error` is `check_failed`; `problems` holds `broken link [[atlas/nowhere]]`; no refused path exists in the notes table.
- **Working note.** Run with `{"path": "atlas/working/verify-owner/scratch", "title": "Scratch", "body": "Loose thoughts."}`. `ok` is true; the index has no `working` line; `check atlas` returns no problems.

Run on both backends.

## Gotchas

- A path under `<domain>/working/` must have the actor folder: `<domain>/working/<actor>/<name>`.
- Two notes that link to each other cannot be created one at a time; use `batch`.
