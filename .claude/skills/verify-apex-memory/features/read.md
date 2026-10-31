# Read

`read` returns a whole note or one section with its subsections, with ids, versions and the outline, in exactly the form that `patch_section` replace accepts. A missed heading or path returns the closest candidates.

## Sub-features

- `read-note` a whole note with its header fields and outline.
- `read-section` one section by heading or by `section_id`, with its version.
- `read-candidates-heading` a heading without its date suffix returns the dated heading as the first candidate.
- `read-candidates-path` an unknown path returns the near note paths.
- `read-path-required` a call without path or section id is refused with a hint.

## How to get to it (user POV)

- Call the MCP tool `read` with `path`, `path` and `heading`, or `section_id`.

## Driving it with scripts/verify

Preconditions:

- The seed note `atlas/api` with sections `Atlas API`, `Rate limits (2026-10-01)` and `Auth`.

- **Scripted journey.** Run `uv run --extra postgres scripts/verify drive read`. It prints `PASS read [<backend>]`.
- **Whole note.** Run `scripts/verify call read '{"path": "atlas/api"}'`. `content` holds `## Rate limits (2026-10-01)` and `Tokens expire`; `outline` heading paths are `[]`, `["Atlas API"]`, `["Atlas API", "Rate limits (2026-10-01)"]`, `["Atlas API", "Auth"]`; `decision_status` is `agreed`.
- **Section.** Run with `{"path": "atlas/api", "heading": "Auth"}`. `content` is `"\nTokens expire after 24 hours.\n"`, `version` 1. Reading its `section_id` returns the same content.
- **Heading candidates.** Run with `{"path": "atlas/api", "heading": "Rate limits"}`. `error` is `not_found`; the first candidate's `heading_path` is `["Atlas API", "Rate limits (2026-10-01)"]`.
- **Path candidates.** Run with `{"path": "atlas/apii"}`. `error` is `not_found`; `candidates` contains `atlas/api`.
- **No path.** Run `scripts/verify call read`. `error` is `path_required`.

Run on both backends.

## Gotchas

- Section content has headings relative to the section; a direct child is `#`.
- `heading=[]` is the text before the first heading, not the first section.
- A misspelled heading through `call` still records a `tool:refused` event in `memory-ops`.
