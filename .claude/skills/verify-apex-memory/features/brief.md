# Brief

`brief(domain)` is the start-of-task read: store identity, open check problems, the index with its catalog, preferences, decisions by status, open status items, the reader's own working notes and the last log entries.

## Sub-features

- `brief-owner` the owner's brief shows the identity line, zero problems, catalog lines, decisions by status, open items and the last log entry.
- `brief-worker` a worker's brief lists its own working notes.
- `brief-unknown` an unknown domain is refused with the list of domains.
- `brief-cli` `apex-memory brief <domain>` prints the same brief.

## How to get to it (user POV)

- Call the MCP tool `brief` with a domain chosen from `list_domains`.
- Run `apex-memory brief <domain> --url <store>` in a terminal.

## Driving it with scripts/verify

Preconditions:

- The seed store; `scripts/verify doctor` prints `OK`.

- **Scripted journey.** Run `uv run --extra postgres scripts/verify drive brief`. It prints `PASS brief [<backend>]`.
- **Owner brief.** Run `scripts/verify call brief '{"domain": "atlas"}'`. The text holds `domain atlas · you are verify-owner (owner)`, `check: 0 open problems`, `| [[atlas/api]] | API contract of Atlas: rate limits and auth |`, `accepted: D01, D02`, `proposed: P01`, `- [#1 open] Write the API docs` and `— SEED`.
- **Worker brief.** Run the same call with `--role worker`. The text holds `===== your working notes` followed by `atlas/working/verify-worker/draft`.
- **Unknown domain.** Run `scripts/verify call brief '{"domain": "atlass"}'`. `error` is `unknown_domain` and `domains` is `["atlas", "memory-ops", "website"]`.
- **CLI.** The journey runs `apex-memory brief atlas --url <store>`. Exit code 0; stdout holds `| [[atlas/api]] |`.

Run on both backends.

## Gotchas

- The first line of a brief names the store location, which differs between stores; compare from the second line on.
- Log entries appear only for narrative `log_append` entries, never for `tool:` events.
- Status item ids are store-wide; the seed's item is `#1`.
