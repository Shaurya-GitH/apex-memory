# Patch a section

`patch_section` edits one section with four operations: replace (what `read` returned), append (no version needed), prepend and replace_own_text (only the text before the first subsection). Each write is all or nothing, version-checked except append, and can update the note's index line.

## Sub-features

- `ps-replace` replace with the read content plus an edit bumps the version.
- `ps-conflict` a stale version is refused with the current content and version.
- `ps-append` append needs no version and goes after the subtree.
- `ps-prepend` prepend needs a version (`version_required` without) and goes before the own text.
- `ps-own-text` replace_own_text keeps subsection ids; headings in its content are `headings_not_allowed`.
- `ps-summary` `summary` changes the note's line in the index catalog.
- `ps-trace` each write event records the shape of the call.

## How to get to it (user POV)

- Call `read` for the section, then the MCP tool `patch_section` with `op`, `content`, `if_version` and `section_id` (or `path` and `heading`).

## Driving it with scripts/verify

Preconditions:

- The seed note `atlas/api`; section `Auth` at version 1.

- **Scripted journey.** Run `uv run --extra postgres scripts/verify drive patch-section`. It prints `PASS patch-section [<backend>]`.
- **Replace.** Read `Auth`, then run `scripts/verify call patch_section '{"op": "replace", "content": "\nTokens expire after 24 hours.\nRefresh tokens last 30 days.\n", "if_version": 1, "section_id": <id>}'`. A new read shows the line and version 2.
- **Conflict.** Repeat with `"if_version": 1`. `error` is `version_conflict`, `current_version` 2, `content` holds `Refresh tokens`.
- **Append.** Run with `{"op": "append", "content": "Revoke a token with the admin console.", "section_id": <id>}` and no version. `ok` is true.
- **Prepend.** Without `if_version`: `version_required`. With the current version and `"content": "All tokens are JWTs."`: the section reads JWTs, then Tokens expire, then Revoke.
- **Own text.** On the `Atlas API` section, `replace_own_text` with `"content": "## Sneaky heading\n"` is `headings_not_allowed`. With `"content": "The Atlas API serves map tiles and geocoding."` and `"summary": "API contract of Atlas: tiles, geocoding, limits and auth"`: the own text changes, the outline ids are the same, and `read atlas/index` shows the new catalog line.
- **Side effects.** The database holds 5 `tool:patch_section` events, each with `"shape"` in its body, and 3 `tool:refused` events in `memory-ops`.

Run on both backends.

## Gotchas

- `replace` accepts exactly what `read` returned; strip nothing.
- Append still respects leases and roles.
- On a page whose only section is the root, append and replace on the root may add headings; elsewhere the root refuses headings.
- Generated sections (index catalog, status lists, log) return `generated_section`.
