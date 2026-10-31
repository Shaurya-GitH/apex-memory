# Status items

`status_item` keeps the open status of a domain as items (state, open, blocker, next). The owner adds, updates and closes them; the status page and the brief render from them, and each event keeps the old text.

## Sub-features

- `si-add` an added item shows in `brief` and on `<domain>/status`.
- `si-update` update changes text and kind; the event keeps the old text.
- `si-close` a closed item leaves the brief; a second close is `not_open`.
- `si-owner` a worker cannot add.

## How to get to it (user POV)

- Call the MCP tool `status_item` with `domain`, `action` (`add`, `update`, `close`), `kind`, `text`, `item_id`.

## Driving it with scripts/verify

Preconditions:

- The seed domain `atlas` with one open item.

- **Scripted journey.** Run `uv run --extra postgres scripts/verify drive status-items`. It prints `PASS status-items [<backend>]`.
- **Add.** Run `scripts/verify call status_item '{"domain": "atlas", "action": "add", "kind": "blocker", "text": "Waiting for the map licence"}'`. `brief atlas` holds `- [#<id> blocker] Waiting for the map licence`; `read atlas/status` holds `- Waiting for the map licence`.
- **Update.** Run with `{"action": "update", "item_id": <id>, "text": "Map licence signed; waiting for keys", "kind": "open"}`. The brief shows `- [#<id> open] Map licence signed; waiting for keys` and not the old text; the latest `tool:status_item` event body holds the old text.
- **Close.** Run with `{"action": "close", "item_id": <id>}`. The brief no longer holds `Map licence`; a second close is `not_open`; the row's `closed_at` is set.
- **Worker.** An add with `--role worker` is `owner_only`.

Run on both backends.

## Gotchas

- Item ids are store-wide, not per domain.
- A domain imported with a prose status page shows that page in the brief instead of items.
