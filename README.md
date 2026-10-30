# apex-memory

apex-memory is a knowledge store for AI agents. It keeps markdown notes in a database: SQLite or Postgres. Agents use the store through the Model Context Protocol (MCP).

Each write is one transaction. The store does a check of the knowledge in each write. If the write adds a problem, such as a broken link, the store rolls back the write. The store then tells the agent why.

- Python package: `apex_memory`
- Command-line interface (CLI): `apex-memory`
- License: MIT
- Python: 3.12 or later

The project started as a part of a private project. This repository is the public extract.

**Low token cost.** In 21 days of real use, the store used less than 1% of all new context tokens. File reads, search and shell output used approximately 15 times more. See [Token cost](#token-cost).

## Contents

- [Quick start for agents](#quick-start-for-agents)
- [Token cost](#token-cost)
- [Data model](#data-model)
- [Install](#install)
- [Choose the database](#choose-the-database)
- [Create a domain](#create-a-domain)
- [Connect an MCP client](#connect-an-mcp-client)
- [Roles](#roles)
- [MCP tools](#mcp-tools)
- [Refusals](#refusals)
- [CLI commands](#cli-commands)
- [Import a folder of markdown notes](#import-a-folder-of-markdown-notes)
- [Move a store from SQLite to Postgres](#move-a-store-from-sqlite-to-postgres)
- [Backups](#backups)
- [Differences between the backends](#differences-between-the-backends)
- [Run the tests](#run-the-tests)
- [Add a database backend](#add-a-database-backend)
- [Agent rules template](#agent-rules-template)
- [License](#license)

## Quick start for agents

This procedure installs the store with SQLite, creates one domain and connects Claude Code. For other MCP clients, see [Connect an MCP client](#connect-an-mcp-client).

1. Make sure that `uv` is installed. Run `uv --version`. If the command fails, install uv from <https://docs.astral.sh/uv/>.
2. Install the package from GitHub:

   ```sh
   uv tool install git+https://github.com/Shaurya-GitH/apex-memory
   ```

3. Run `apex-memory --help`. If the shell cannot find the command, run `uv tool update-shell` and open a new shell.
4. Create the store. The default database is the SQLite file `~/.apex-memory/memory.db`.

   ```sh
   apex-memory init
   ```

5. Create the first domain. Use the first script in [Create a domain](#create-a-domain). Replace `myproject` with the name of your domain.
6. Create the ops domain `memory-ops`. Use the second script in [Create a domain](#create-a-domain). The store records refusals and errors there.
7. Do a check of the import:

   ```sh
   apex-memory verify ~/apex-notes/myproject
   apex-memory brief myproject
   ```

8. Register the MCP server for all of your projects. Replace `claude-code` with a short name for the agent.

   ```sh
   claude mcp add apex-memory --scope user -- apex-memory serve --actor claude-code --role owner
   ```

9. Start a new Claude Code session. Run `/mcp` and make sure that `apex-memory` shows as connected.
10. Copy [templates/AGENTS.md](templates/AGENTS.md) into your own `AGENTS.md` or `CLAUDE.md`. Replace its placeholders.
11. In the new session, call the tool `brief` with `domain` set to your domain.

## Token cost

An agent reads knowledge from the store into its context. Thus the store adds tokens to each session. We measured this cost in real use, not in a benchmark.

The data: all Claude Code sessions of one owner for 21 days. That is 3,515 sessions. 245 of these sessions used the store.

| Measure | Result |
| --- | --- |
| Store output, as a part of all new context tokens | **0.77%** |
| File reads, search and shell output (`Read`, `Grep`, `Bash`), for comparison | 11.9% |
| Store cost in the sessions that used the store, with cache re-reads | 3.4% (median 1.7%) |
| Store output in one session that used the store, on average | approximately 29,000 tokens |
| Part of the store output that came from `read` | 60% |
| Part of the store output that came from `brief` | 20% |

The store keeps the cost low because an agent reads one section, not a full page. `brief` gives the agent its start context in one call.

How we measured:

- We counted the characters in each tool result. We used 4 characters for each token.
- For cost, we used these weights: input 1, cache write 1.25, cache read 0.1, output 5.
- For "with cache re-reads", each later turn reads each store result again from the cache, until the context is compacted.

Your results can be different. They change with the size of your knowledge and with how your agents use it.

## Data model

The store keeps knowledge in domains. A domain is one area of knowledge, for example one product. This table gives each part of the model.

| Item | What it is |
| --- | --- |
| Domain | A set of notes with the path prefix `<domain>/`. Each domain has its own index, status page, log and decision records. You create a domain with `apex-memory import`. |
| Note | One markdown page with a path such as `<domain>/topics/storage`. A note has a title, a summary and a version. A project note also has a page header. |
| Page header | Three fields on a project note: decision status, implementation status and evidence checked. The store also keeps the date `verified_at`. |
| Index | The page `<domain>/index`. Its catalog table shows one line for each note: the link and the summary. The store makes the catalog from the summaries. |
| Section | One heading of a note and its text. Each section has an id and a version. You read and edit one section at a time. |
| Link | `[[<domain>/<note>]]`, `[[<domain>/<note>#Heading]]` or `[[<note>#^block]]` in the text. The store keeps the links in the two directions. `move` rewrites them in notes. The index catalog and the log are generated from rows, so their links are not stored as links. Log entries keep the old page name after a `move`. |
| Decision record | A heading such as `D01 — Use SQLite (2026-10-30)` on the page `<domain>/decisions`. It has one `- **Status:**` line. |
| Decision status | `proposed`, `accepted`, `rejected` or `superseded by Dnn`. Only `set_decision_status` changes the Status line. |
| Status item | One item on the page `<domain>/status`. The kinds are `state`, `open`, `blocker` and `next`. |
| Log | The page `<domain>/log`. It is append-only. The database stamps the time of each entry. |
| Tool event | An automatic log record of each write and each search. The log page does not show tool events. `log_query` shows them on request. |
| Source | A record of where knowledge came from: `snapshot`, `live`, `transcript`, `web` or `owner`. A note can cite a source. |
| Preference | A rule from the owner about how to do work, with the words of the owner. Each brief shows the active preferences. |
| Lease | A claim on a section for 1 to 60 minutes. A longer time becomes 60 minutes. Other agents cannot write to that section while the lease is active. |
| Working note | A scratch note under `<domain>/working/<actor>/`. It needs no summary and no page header. It is not in the index. |
| Ops domain | The domain that gets an automatic record of each refusal and each error. The default name is `memory-ops`. |

### The check

The store does a check of a domain before and after each write. The check finds these problems:

- Broken links, and anchors that the store cannot find.
- Notes that have no summary, so they are not in the index.
- The same paragraph of 200 characters or more in two notes.
- Decision records that do not have exactly one Status line.
- Notes that cite a `live` source, with a `verified_at` date older than 14 days.

If a write adds a new problem, the store rolls back the write. The refusal `check_failed` lists the problems. Problems that were there before the write do not stop it. The tool `check` and the command `apex-memory check` show all the problems of a domain.

### Decision records

A decision record is a heading on `<domain>/decisions`. The heading starts with an id: `D` or `P` and digits. Then it has ` — ` and a title. Write the record as in this example:

```markdown
## D01 — Use SQLite first (2026-10-30)
- **Status:** proposed
- **Decision:** The store uses SQLite first.
```

These rules apply to decision records:

- You cannot delete a record. Supersede it with `set_decision_status`.
- Only `set_decision_status` changes the Status line.
- You can edit a `proposed` record. You can only append text to an `accepted`, `rejected` or superseded record.
- The status can change from `proposed` to `accepted`, `rejected` or `superseded`. It can change from `accepted` to `superseded`.
- `superseded_by` must name an accepted decision.

## Install

The package is not on PyPI. Install it from GitHub. You need Python 3.12 or later.

| Need | Command |
| --- | --- |
| SQLite only, with uv | `uv tool install git+https://github.com/Shaurya-GitH/apex-memory` |
| SQLite and Postgres, with uv | `uv tool install "apex-memory[postgres] @ git+https://github.com/Shaurya-GitH/apex-memory"` |
| SQLite only, with pip | `pip install git+https://github.com/Shaurya-GitH/apex-memory` |
| SQLite and Postgres, with pip | `pip install "apex-memory[postgres] @ git+https://github.com/Shaurya-GitH/apex-memory"` |

The `postgres` extra installs `psycopg[binary]` 3. To add Postgres to an installed tool, run the second uv command with `--force`.

`uv tool install` puts the command `apex-memory` in `~/.local/bin`. If that folder is not on your `PATH`, run `uv tool update-shell`. Use pip in a virtual environment.

To update the tool, run `uv tool upgrade apex-memory`.

## Choose the database

The store finds its database in this sequence:

1. The option `--url`.
2. The environment variable `APEX_MEMORY_URL`.
3. The SQLite file `~/.apex-memory/memory.db`.

| URL | Database |
| --- | --- |
| `sqlite:///absolute/path/memory.db` | A SQLite file at an absolute path. |
| `sqlite://~/path/memory.db` | A SQLite file relative to your home folder. |
| `/absolute/path/memory.db` | A bare path is a SQLite file. |
| `postgresql://user:password@host:port/database` | A Postgres database. `postgres://` also works. |

Each command and the MCP server open the database and create or update the schema. The store refuses a database whose schema is newer than the installed package. If that occurs, upgrade the package.

### SQLite

SQLite needs no setup. `apex-memory init` creates the file and its folder. Many processes can use one file at the same time. The store lets only one process write at a time.

```sh
apex-memory init --url sqlite:///Users/you/stores/memory.db
```

The output is `store <store id> at <path>`. Run `init` again to see the store id. It does not change a store.

### Postgres

1. Install the package with the `postgres` extra. See [Install](#install).
2. Start a Postgres server. For a local test server, use Docker:

   ```sh
   docker run -d --name apex-memory-pg -e POSTGRES_PASSWORD=test -p 5432:5432 postgres:17
   ```

   If a Postgres server already uses port 5432 on your computer, use a different host port, for example `-p 55432:5432`. Then use that port in the URLs below.

3. Create a user. The password of the `postgres` user is `test`. `psql` asks for it.

   ```sh
   psql -h localhost -U postgres -c "CREATE ROLE apex LOGIN PASSWORD 'secret'"
   ```

   If `psql` is not installed, run it in the Docker container. Use this form for each `psql` command in this section:

   ```sh
   docker exec -e PGPASSWORD=test apex-memory-pg psql -U postgres -c "CREATE ROLE apex LOGIN PASSWORD 'secret'"
   ```

4. Create a database that the user owns. The user must be able to create tables in it.

   ```sh
   psql -h localhost -U postgres -c "CREATE DATABASE apex_memory OWNER apex"
   ```

5. Create the store:

   ```sh
   apex-memory init --url postgresql://apex:secret@localhost:5432/apex_memory
   ```

6. Set the URL for all commands. Put this line in your shell profile:

   ```sh
   export APEX_MEMORY_URL=postgresql://apex:secret@localhost:5432/apex_memory
   ```

The URL accepts each libpq parameter. To keep a store in its own schema, create the schema first. Then add the `options` parameter:

```sh
psql -h localhost -U apex -d apex_memory -c "CREATE SCHEMA team_a"
apex-memory init --url 'postgresql://apex:secret@localhost:5432/apex_memory?options=-csearch_path%3Dteam_a'
```

One schema holds one store. If the password contains special characters, percent-encode them in the URL. The store does not show the password in its output.

If the `postgres` extra is not installed, a Postgres URL stops with the message `postgresql needs the psycopg package: install apex-memory[postgres]`.

## Create a domain

No MCP tool creates a domain. You create a domain when you import a folder of markdown notes. The name of the folder is the name of the domain. Use lowercase letters, digits and hyphens.

This script writes the four start pages of a new domain and imports them. Change `DOMAIN` and `NOTES` as necessary.

```sh
DOMAIN=myproject
NOTES=~/apex-notes
mkdir -p "$NOTES/$DOMAIN"
cat > "$NOTES/$DOMAIN/index.md" <<EOF
# $DOMAIN index

## Catalog

| Page | Purpose |
|---|---|
| [[$DOMAIN/decisions]] | Decision records |
| [[$DOMAIN/status]] | Current state |
| [[$DOMAIN/log]] | Append-only activity |
EOF
cat > "$NOTES/$DOMAIN/status.md" <<EOF
# $DOMAIN status

## Current state

None.

## Unfinished / open

None.

## Blockers

None.

## Next concrete action

None.
EOF
printf '# %s log\n' "$DOMAIN" > "$NOTES/$DOMAIN/log.md"
printf '# %s decisions\n' "$DOMAIN" > "$NOTES/$DOMAIN/decisions.md"
apex-memory import "$NOTES/$DOMAIN"
```

The store records refusals and errors only when the ops domain exists. This script creates the ops domain `memory-ops`:

```sh
NOTES=~/apex-notes
mkdir -p "$NOTES/memory-ops"
cat > "$NOTES/memory-ops/index.md" <<'EOF'
# memory-ops index

## Catalog

| Page | Purpose |
|---|---|
| [[memory-ops/log]] | Failures and friction of the memory system |
EOF
printf '# memory-ops log\n' > "$NOTES/memory-ops/log.md"
apex-memory import "$NOTES/memory-ops"
```

To use a different name for the ops domain, give `--ops-domain <name>` to `apex-memory serve`.

After the import, the store holds the knowledge. The folder is only the start point. Agents add notes with `create_note`.

## Connect an MCP client

The MCP server uses stdio. The client starts this command:

```sh
apex-memory serve --actor <name> --role <owner or worker>
```

| Option | What it does |
| --- | --- |
| `--actor <name>` | The name of the agent. Use lowercase letters, digits and hyphens. The name is in each log entry and in the path of the working notes. |
| `--role owner`, `--role worker` | The role of the agent. The default for `serve` is `worker`. See [Roles](#roles). |
| `--url <url>` | The database. You can also set `APEX_MEMORY_URL` in the environment of the server. |
| `--ops-domain <name>` | The domain that gets the records of refusals and errors. The default is `memory-ops`. |
| `--run-id <id>` | An id for this run. The default is a new UUID. Log entries keep it. |

When the client connects, the server sends instructions to the agent. The instructions give the store id, the actor, the role and the main rules.

### Claude Code

Use `--scope user` to register the server for all of your projects. Give the URL with `-e`:

```sh
claude mcp add apex-memory --scope user \
  -e APEX_MEMORY_URL=sqlite:///Users/you/.apex-memory/memory.db \
  -- apex-memory serve --actor claude-code --role owner
```

For Postgres, set `APEX_MEMORY_URL` to the Postgres URL. Without `-e`, the server uses `~/.apex-memory/memory.db`.

To remove the server, run `claude mcp remove apex-memory --scope user`.

### Codex

```sh
codex mcp add apex-memory --env APEX_MEMORY_URL=sqlite:///Users/you/.apex-memory/memory.db \
  -- apex-memory serve --actor codex --role owner
```

### Other MCP clients

Most clients accept a JSON configuration of this type:

```json
{
  "mcpServers": {
    "apex-memory": {
      "command": "/Users/you/.local/bin/apex-memory",
      "args": ["serve", "--actor", "my-agent", "--role", "owner"],
      "env": { "APEX_MEMORY_URL": "sqlite:///Users/you/.apex-memory/memory.db" }
    }
  }
}
```

A desktop client can start without your shell `PATH`. If the client cannot find `apex-memory`, give the full path. To find the full path, run `command -v apex-memory`.

## Roles

You give the role when you register the server. The store does not authenticate the role. It is for one machine with one owner.

| Role | Who | What it can do |
| --- | --- | --- |
| `owner` | The sessions of the owner. | All the tools. |
| `worker` | Agents that you run for the owner. | Read and search all the knowledge. Write only its own working notes under `<domain>/working/<actor>/`. Append log entries. |

A worker cannot do these actions:

- Write a project note, a page header or a section outside its working notes.
- Add or close a status item.
- Change a decision status.
- Add or retire a preference.
- Promote a working note into project knowledge.
- Change a source that exists.

The owner promotes a working note with `move`. The promotion needs a summary and a page header.

Search results do not show the working notes of other actors. To see them, search with a `scope` in the `working` folder.

## MCP tools

Each tool returns a JSON object. A result has `"ok": true`. A refusal has `"ok": false` and an `error`.

| Tool | What it does |
| --- | --- |
| `brief` | Gives the start read for a domain: store identity, number of problems, index, preferences, decisions, open status items and the last log entries. |
| `search` | Finds text in notes, sections and log entries. `mode` is `bm25`, `exact` or `regex`. `scope` is a domain or folder; empty means all domains. |
| `read` | Reads a full note, or one section and its subsections, with ids and versions. |
| `links` | Shows the links out of a note, into a note, or both, up to 3 hops. |
| `log_query` | Filters log entries by time, `op` text, page or task id. `include_tool` adds the tool events. |
| `patch_section` | Edits one section with a version check. `op` is `replace`, `append` or `prepend`. |
| `patch_header` | Edits the page header of a note with a version check. |
| `create_note` | Creates a note in a domain that exists. It never overwrites. |
| `move` | Renames a note in its domain and rewrites all links to it. It also promotes a working note. |
| `status_item` | Adds or closes one status item. Owner only. |
| `set_decision_status` | Changes the Status line of one decision record. Owner only. |
| `log_append` | Appends one entry to the log of a domain. The pages that it names must exist. |
| `preference` | Adds or retires a preference of the owner. `add` needs the words of the owner. Owner only. |
| `source` | Adds a source, or cites or uncites a source from a note. |
| `lease` | Takes or releases a lease on a section. |
| `check` | Shows the problems of a domain. It changes nothing. |

### How to edit a section

1. Call `read` with `path` and `heading`. The result gives `section_id`, `version` and `content`.
2. Change the text of `content`.
3. Call `patch_section` with `op: "replace"`, the new `content`, `section_id`, and `if_version` set to `version`.
4. If the result is `version_conflict`, another agent changed the section. Merge your change into the `content` of the refusal.
5. Call `patch_section` again with `if_version` set to `current_version`.

The headings in `content` are relative to the section. A direct subsection starts with `#`. `replace` accepts the same format that `read` gives.

To add a subsection, use `op: "append"`. `prepend` adds text before the own text of the section. It cannot add headings.

`heading` can be the heading text or a list with the full heading path. The path starts at the highest heading in the note text. An imported note usually has an H1, so its path starts with the H1, for example `["myproject status", "Blockers"]`. A note from `create_note` has its title outside the text, so its path starts with its first heading, for example `["Design", "Details"]`. If two sections have the same heading, the store gives the candidates. Then use `section_id`.

The index catalog, the status items and the log entries are generated sections. `patch_section` refuses them. Use the `summary` field, `status_item` and `log_append`.

### Search modes

| Mode | Use it for |
| --- | --- |
| `bm25` | Topics. A keyword search with a rank order. Lower scores are better. |
| `exact` | Ids, hashes and paths. A substring search that ignores case. |
| `regex` | Patterns. Each pattern must compile in Python. See [Differences between the backends](#differences-between-the-backends). |

## Refusals

A refusal is an expected result. It is not an error of the server. The `error` field gives the reason. Many refusals also give a `hint`.

| `error` | Cause | What to do |
| --- | --- | --- |
| `version_conflict` | The section or note changed after you read it. | Merge into the `content` of the refusal. Try again with `current_version`. |
| `check_failed` | The write adds a problem. `problems` lists it. | Correct the problem in your text. Try again. |
| `leased` | Another agent has a lease on that part. | Wait until `expires_at`, or ask the holder to release it. |
| `not_allowed` | A worker tried to write outside its working notes. | Write under `<domain>/working/<actor>/`. |
| `owner_only` | A worker tried an owner action. | Ask the owner. |
| `unknown_domain` | The domain is not in the store. | Use a domain from `domains`, or import the domain. |
| `not_found`, `ambiguous_heading` | The store cannot find the note or section, or the heading is not unique. | Use a heading path or a `section_id`. |
| `summary_required`, `header_required` | A project note needs a summary and a full page header. | Give `summary`, `decision_status`, `impl_status` and `evidence`. |
| `exists` | The note exists. `create_note` does not overwrite. | Use `patch_section`. |
| `generated_section` | The store makes the section from rows. | Use `status_item`, `log_append` or the `summary` field. |
| `status_line_changed`, `decision_overwritten`, `decision_removed` | The edit changes a decision record in a way that is not permitted. | Use `set_decision_status`, or append text only. |
| `unknown_pages` | `log_append` names a page that is not in the store. | Name pages that exist. |
| `bad_regex` | The pattern does not compile in Python. | Correct the pattern. |
| `headings_not_allowed` | `prepend` text contains a heading. | Use `append` to add subsections. |
| `bad_transition` | `set_decision_status` asks for a change that the life cycle does not permit, for example accepted to proposed. | Use proposed to accepted, rejected or superseded, or accepted to superseded. |
| `quote_required` | `preference` `add` has no words of the owner. | Give the quote of the owner. |
| `promotion_needs_summary_and_header` | `move` of a working note into project knowledge has no summary or page header. | Give `summary` and the header fields. |
| `bad_path` | The path is not permitted, for example a `move` of the index. | Use a path of a normal note. |
| `domain_exists` | `apex-memory import` of a domain that is in the store. | Use a new database or another domain name. |

If the ops domain exists, the store records each refusal and each error in it. The record keeps the tool, the arguments and the result. The store replaces the `content` argument and long text with their length.

## CLI commands

The CLI uses the same database rules as the server. All commands accept `--url`. The CLI acts as the actor `cli` with the role `owner`.

| Command | What it does |
| --- | --- |
| `apex-memory init` | Creates the store, or shows the store that exists. |
| `apex-memory serve --actor <name> --role <role>` | Starts the MCP stdio server. |
| `apex-memory import <folder>` | Imports a folder of markdown notes as one domain. |
| `apex-memory import <store>` | Copies a full store, such as a SQLite file or a backup, into a new empty store. |
| `apex-memory verify <folder>` | Makes sure that each imported page renders back the same as its file. |
| `apex-memory brief <domain>` | Prints the brief of a domain. |
| `apex-memory check <domain>` | Prints the problems of a domain. The exit code is 1 if there are problems. |
| `apex-memory backup [--to <folder>] [--keep <n>]` | Writes a verified backup. See [Backups](#backups). |

## Import a folder of markdown notes

`apex-memory import <folder>` reads each `*.md` file in the folder and its subfolders. The import only reads the files. The path of a note is `<folder name>/<relative path without .md>`. The report lists other files in `not_imported`.

The import gives special treatment to these files:

| File | Treatment |
| --- | --- |
| `index.md` | Rows such as `\| [[<domain>/<note>]] \| <summary> \|` give the summaries. If the rows are a plain table at the end of a section, the store makes that catalog from the summaries. If not, the page stays as written. |
| `status.md` | Sections that start with `Current state`, `Unfinished / open`, `Blockers` or `Next concrete action` become status items. Each bullet or paragraph is one item. `None.` means no items. |
| `log.md` | The last level-2 headings with the format `## 2026-10-01T10:00:00+05:30 — OP` become log entries. Older sections stay as written. |
| `decisions.md` | Headings such as `D01 — Title` or `P01 — Title` become decision records. |
| `sources/*.md` | Each note becomes a `snapshot` source. |

A note gets a page header from its first line before the first heading, in this format:

```markdown
> **Decision status:** accepted **Implementation status:** built **Evidence checked:** 2026-10-30, tests pass
```

The import accepts simple YAML front matter: `key: value` lines and lists of `- item` lines. Other front matter stops the import.

The import prints a report. Read `problems`, `catalog_unknown`, `unclassified_decisions` and `kept_as_written`.

You can import each domain one time. A second import of the same domain gets the refusal `domain_exists`. For a test, `--fresh` deletes the database first. `--fresh` only works on a SQLite file whose name contains `test`.

Then do a check of the import:

```sh
apex-memory verify ~/apex-notes/myproject
```

`verify` renders each page from the store and compares it with its file. It ignores spaces at line ends, extra blank lines and blank lines at the start and end. If a page is different, `verify` prints a diff and the exit code is 1.

## Move a store from SQLite to Postgres

`apex-memory import` copies a full store when its argument is a file or a URL and not a folder.

1. Create an empty Postgres database. See [Postgres](#postgres).
2. Copy the store:

   ```sh
   apex-memory import ~/.apex-memory/memory.db --url postgresql://apex:secret@localhost:5432/apex_memory
   ```

3. Do a check of each domain:

   ```sh
   apex-memory check myproject --url postgresql://apex:secret@localhost:5432/apex_memory
   ```

4. Set `APEX_MEMORY_URL` to the Postgres URL in your shell profile and in each MCP client.

The copy keeps all rows, ids, the store id and the dates. It reads the source in one transaction and writes the target in one transaction. The target must not have notes or log entries. If it has, the copy stops with `already holds knowledge`. A copy of a store into the same store stops with `cannot copy a store into itself`.

The same command moves a store from Postgres to SQLite, or from SQLite to SQLite. The source can be a backup file or a URL.

## Backups

```sh
apex-memory backup --to ~/.apex-memory/backups --keep 14
```

The command writes `<folder>/memory-YYYY-MM-DD.db`. Each backup is a SQLite file. The command does an integrity check of the file before it keeps it. It then deletes the oldest backups and keeps the newest `--keep` files. A second backup on the same day replaces the first one.

| Backend | How the backup works |
| --- | --- |
| SQLite | `VACUUM INTO` a new file, then `PRAGMA integrity_check`. |
| Postgres | A logical copy of the store into a new SQLite file, then `PRAGMA integrity_check`. It does not need `pg_dump`. |

For a full backup of a Postgres cluster, also use `pg_dump`.

To restore a backup, import it into a new empty store:

```sh
apex-memory import ~/.apex-memory/backups/memory-2026-10-30.db --url sqlite:///Users/you/.apex-memory/restored.db
```

The target of a restore can also be a Postgres URL.

### Nightly backup on macOS

The file `ops/com.apex-memory.backup.plist` is a launchd agent. It runs `apex-memory backup --keep 14` each day at 03:00. If the Mac is asleep at that time, the backup runs when the Mac wakes. The output goes to `/tmp/apex-memory-backup.log`.

The agent runs the command in a zsh login shell. Thus `~/.zprofile` or `~/.zshenv` must put `~/.local/bin` on `PATH`. If you do not use the default database, the same file must set `APEX_MEMORY_URL`. A login shell that is not interactive does not read `~/.zshrc`.

1. Copy the file:

   ```sh
   cp ops/com.apex-memory.backup.plist ~/Library/LaunchAgents/
   ```

2. Load the agent:

   ```sh
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.apex-memory.backup.plist
   ```

3. To do a test now, run `launchctl kickstart gui/$(id -u)/com.apex-memory.backup`.
4. Read `/tmp/apex-memory-backup.log`. Make sure that it shows the path of a new backup.

On Linux, use cron or a systemd timer to run the same command.

## Differences between the backends

SQLite and Postgres give the same pages, checks, briefs, links and log queries. These differences remain:

| Area | SQLite | Postgres |
| --- | --- | --- |
| `regex` search | Python `re` syntax. | POSIX syntax of the `~` operator. The pattern must also compile in Python. |
| `bm25` search | FTS5 `bm25()`. | `ts_rank` with BM25 weights. The hits are almost the same, but the rank order can be different. |
| Tokens for `bm25` | Paths and URLs split into words. | Paths and URLs can stay as one token. |
| `exact` search | A trigram index for queries of 3 characters or more. | A scan of all section rows. |
| Single writer | `BEGIN IMMEDIATE` and WAL. | An advisory lock for each schema. |
| Backup | `VACUUM INTO`. | A logical copy into a SQLite file. |
| Search snippets | The match is in brackets, for example `The [café] on`. | Context around the match, with no brackets. |
| `section_id` values | A refused write uses no id. | A refused write can use an id. Thus ids can be different from the ids of the same history on SQLite. Do not compare ids between backends. |

### Regular expressions on Postgres

The store compiles each `regex` pattern in Python first. Then it sends the pattern to Postgres to find sections. It finds log entries with Python. Thus a pattern must have the same meaning in the two syntaxes.

- Do not use `\b`. In Postgres, `\b` is a backspace, so the search finds no sections.
- Do not use `\y`, `\m` or `\M`. Python refuses them with `bad_regex`.
- For a word boundary, use character classes, for example `(^|[^A-Za-z0-9_])copies([^A-Za-z0-9_]|$)`.
- Do not use named groups `(?P<name>...)`. Do not use inline flags after the start of the pattern.

### Speed

`exact` search on Postgres scans rows. `log_query` with `page` scans rows on the two backends. This is fast for thousands of rows. For much larger stores, add an index.

## Run the tests

1. Clone the repository and go into it.
2. Run the tests on SQLite:

   ```sh
   uv run python -m unittest
   ```

   The Postgres tests are skipped. The result is `OK (skipped=47)`.

3. To run the tests on Postgres too, install the extra:

   ```sh
   uv sync --extra postgres
   ```

4. Start a Postgres server. The tests create and drop one schema for each test in the database that you give.

   ```sh
   docker run -d --name apex-memory-test-pg -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:17
   ```

5. Run the tests with the URL of that database:

   ```sh
   APEX_MEMORY_TEST_POSTGRES_URL=postgresql://postgres:test@localhost:55432/postgres uv run python -m unittest
   ```

6. Remove the server: `docker rm -f apex-memory-test-pg`.

## Add a database backend

The store reaches the database only through the `Backend` interface in `apex_memory/backends/base.py`. Each backend is one module.

1. Write a module in `apex_memory/backends/` with a subclass of `Backend`. Set `name` and `location`. `location` must not contain a password.
2. Write the schema in `apex_memory/backends/sql/<name>/001_schema.sql`. `migrate()` applies the files in that folder in name order.
3. Add one line for each URL scheme to `BACKENDS` in `apex_memory/backends/__init__.py`.
4. Add a test class that runs `MemoryTest` on the new backend. Use `PostgresMemoryTest` in `tests/test_memory.py` as the example. Override `new_url()` to give a new empty store.
5. Run all the tests.

The backend must supply these methods:

| Method | What it must do |
| --- | --- |
| `__init__(url)` | Connect to the database that the URL names. |
| `transaction()` | Give one write transaction. Allow only one writer at a time across all processes. Commit on success. Roll back on an exception. |
| `query(sql, args)` | Run neutral SQL with `?` placeholders. Return rows that you can index by position and by column name. |
| `execute(sql, args)` | Run one statement. Return the number of rows that changed. |
| `insert(table, values)` | Insert one row and return its integer id. Accept a given id and move the id sequence after it. |
| `search(table, query, where, args, exact)` | Do a ranked full-text search with `score` and `snippet`. When `exact` is true, do a substring search. |
| `regex(expr)` | Return a condition that is true when `expr` matches the next `?` argument. |
| `now(minutes)` | Return SQL for the UTC time plus `minutes` as `YYYY-MM-DDTHH:MM:SS.mmmZ`. |
| `schema_version()`, `set_schema_version(version)` | Read and write the number of applied migrations. |
| `run_script(sql)` | Run one migration file. |
| `backup(destination)` | Write a full, verified copy of the store as a new SQLite file. |
| `close()` | Close the connection. |

`store.py`, `importer.py` and `server.py` contain only neutral SQL: `?` placeholders, `lower`, `coalesce`, `count`, `max`, `LIKE ... ESCAPE '!'`, `||`, `LIMIT` and `EXISTS`. All database-specific SQL goes in the backend. The test `PortabilityTest.test_no_database_specific_sql` fails if these files contain tokens such as `fts5`, `PRAGMA`, `ON CONFLICT`, `RETURNING`, `::`, `ILIKE` or `psycopg`.

## Agent rules template

[templates/AGENTS.md](templates/AGENTS.md) is a template of rules for agents that use this store. Copy it into your own `AGENTS.md` or `CLAUDE.md`. Then replace the placeholders.

## License

MIT. See [LICENSE](LICENSE).
