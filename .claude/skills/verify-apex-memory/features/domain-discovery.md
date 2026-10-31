# Domain discovery and the ask-once flow

An agent picks the knowledge base for its work from the domain descriptions that `list_domains` returns. When no domain fits, the owner's agent asks once: a yes creates the domain, a "never" becomes a global preference that `list_domains` returns so nobody asks again, and a worker only reports the gap.

## Sub-features

- `dd-instructions` the server instructions start with domain selection; the owner's carry the ask-once flow, the worker's say to report the gap.
- `dd-list` `list_domains` returns every domain with description, note count, last log time and the ops flag.
- `dd-yes` `create_domain` makes a domain with four start pages that `list_domains` and `brief` show.
- `dd-never` `preference` add with no domain appears in `list_domains.preferences`; domain preferences do not.
- `dd-worker` a worker cannot create a domain or record a preference; both refusals are recorded in the ops domain.

## How to get to it (user POV)

- Connect any MCP client to `apex-memory serve`; the instructions arrive in the initialize result.
- Call `list_domains` at the start of a session or when the subject changes.
- Answer the agent's one question: yes (`create_domain`), never (`preference`), not now (nothing).

## Driving it with scripts/verify

Preconditions:

- Seed domains `atlas`, `website`, `memory-ops`; no global preferences.
- `scripts/verify doctor` prints `OK`.

- **Scripted journey.** Run `uv run --extra postgres scripts/verify drive domain-discovery`. It prints `PASS domain-discovery [<backend>]`.
- **Instructions.** The journey reads the initialize result of both servers. The owner text starts with `At the start and when the subject of the work changes, call list_domains` and holds `ask the owner once`, `Not now`, `No knowledge base for <topic>`; the worker text holds `report the gap to the owner's session` and no `create_domain`.
- **List.** Run `scripts/verify call list_domains`. Three domains; `atlas` describes the map API, `website` says `use atlas`, `memory-ops` has `"ops": true`; `"preferences": []`.
- **Yes.** Run `scripts/verify call create_domain '{"domain": "tax-filing", "title": "Tax filing", "description": "Tax returns and their deadlines. Not for Atlas; use atlas."}'`. `pages` lists index, status, log, decisions; `list_domains` shows the description; `brief` of `tax-filing` is ok; the database holds 4 notes in `tax-filing`.
- **Never.** Run `scripts/verify call preference '{"action": "add", "text": "No knowledge base for garden planning", "owner_quote": "no, never for this"}'`. `list_domains` for owner and worker returns `{"id": <item_id>, "text": "No knowledge base for garden planning"}`; the row has `domain` NULL and the owner's words.
- **Domain preference.** Add one with `"domain": "atlas"`. It is absent from `list_domains.preferences`.
- **Worker.** Run `scripts/verify call create_domain '{"domain": "worker-dom", "title": "W", "description": "Holds W."}' --role worker` and the `preference` add with `--role worker`. Both return `owner_only`; no `worker-dom` notes exist; `memory-ops` holds two `tool:refused` events by `verify-worker`.

Run on both backends.

## Gotchas

- "Not now" has no store effect by design; the harness cannot observe it. Verify it by reading the instructions text only.
- `list_domains.preferences` holds only active preferences with no domain; a retired one disappears.
- The `call` store keeps changes: a second `create_domain` of the same name returns `domain_exists`.
- An imported domain has no description: `list_domains` shows its first index paragraph with `description_missing: true`. The seed creates every domain with a description.
