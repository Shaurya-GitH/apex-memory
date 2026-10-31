# Search

`search` finds note titles and summaries, sections and log entries in three modes: ranked keywords (`bm25`), whole words or phrases (`exact`) and patterns (`regex`), within a domain or folder scope or across every domain. Other actors' working notes stay hidden unless the scope is a `working` folder.

## Sub-features

- `search-bm25` ranked keywords put the section that holds the most query terms first.
- `search-exact` matches whole words, ignoring case, with the match in `[brackets]` in the snippet; a partial id does not match.
- `search-regex` matches a Python pattern; a pattern that does not compile is `bad_regex`.
- `search-mode` an unknown mode is `bad_mode`.
- `search-scope` no scope searches every domain; a folder scope narrows it.
- `search-working` another actor's working note is hidden unless the scope is `<domain>/working`; its own actor sees it.
- `search-events` each search that runs writes a `tool:search` event.

## How to get to it (user POV)

- Call the MCP tool `search` with `query`, optional `mode`, `scope` and `limit`.

## Driving it with scripts/verify

Preconditions:

- The seed store: `atlas/api` holds `100 requests per minute` and commit `9f3c2d1`; the worker's draft holds `zephyr`; `website/launch` holds `November`.

- **Scripted journey.** Run `uv run --extra postgres scripts/verify drive search`. It prints `PASS search [<backend>]`.
- **bm25.** Run `scripts/verify call search '{"query": "rate limit requests per key", "scope": "atlas"}'`. The first hit has `path` `atlas/api` and a `heading_path` ending in `Rate limits (2026-10-01)`.
- **Exact.** Run with `{"query": "9f3c2d1", "mode": "exact", "scope": "atlas"}`. One hit in `atlas/api` whose snippet holds `[9f3c2d1]`. With `"query": "9f3c"`, `hits` is `[]`.
- **Regex.** Run with `{"query": "\\d+ requests per minute", "mode": "regex", "scope": "atlas"}`. One hit in `atlas/api`. With `"query": "("`, `error` is `bad_regex`.
- **Bad mode.** Run with `{"query": "x", "mode": "fuzzy"}`. `error` is `bad_mode`.
- **Scope.** Run with `{"query": "website launch november"}`. A hit in `website/launch`.
- **Working notes.** Run with `{"query": "zephyr"}` as owner: no hit under `/working/`. With `"scope": "atlas/working"`: the draft is a hit. With `--role worker` and no scope: the draft is a hit.
- **Side effects.** The database holds 8 `tool:search` events after the journey (the two refused searches write none).

Run on both backends.

## Gotchas

- bm25 drops weak hits: a hit must hold half of the query terms and at least two (all of a shorter query). A one-word query keeps every hit.
- Postgres refuses some patterns that Python compiles (repetitions above 255); both give `bad_regex`.
- JSON in a shell needs the backslash doubled: `"\\d+"`.
- The seed actor `verify-owner` is not the draft's actor, which is why the owner does not see the draft without a working scope.
