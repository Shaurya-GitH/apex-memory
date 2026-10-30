# Agent rules for an apex-memory store

> **How to use this template.** These are rules for agents that use an apex-memory store through MCP. Copy this file into your own `AGENTS.md` or `CLAUDE.md`. Replace each placeholder in angle brackets: `<server-name>`, `<domain-a>`, `<ops-domain>` and the others. Remove the domains that you do not use, and remove this note. Keep one copy of these rules. If two agents read them, let one file import or link the other. The tool names are the names that the server gives. Your client can add a prefix, for example `mcp__<server-name>__brief`.

## Select the relevant domain

Since `<date>`, all knowledge lives in the apex-memory store at `<database URL or path>`. Use only the `<server-name>` MCP tools to get to it. Select knowledge by the domain of the task, not by the repository or the tool name. Use only the relevant domain for project context and log entries.

| Domain | Use it for |
| --- | --- |
| `<domain-a>` | `<the product, project or area that this domain holds>` |
| `<domain-b>` | `<the product, project or area that this domain holds>` |
| `<ops-domain>` | The failures and friction of the memory system itself. See [Report memory-system problems](#report-memory-system-problems). |

- For a new domain, ask the owner before you create it. Do not put its knowledge in an unrelated domain.
- Each domain has its own index, status, append-only log, decision records and authoritative topic owners.
- Do not read a domain only because the code is in a related repository. Do not log generic work in a project domain.
- Read another domain only for an explicit cross-domain dependency. Link to its authoritative owner. Do not copy its contracts.
- Repository instructions still govern their code. These rules for knowledge do not give permission for unrelated changes to an application.

## Use the memory store

- Do not read or write knowledge in other places, such as local markdown files or other note tools. If you keep an archive of old notes at `<archive path>`, it is history only. It is not a source.
- At the start of a relevant task or session, call `brief("<domain>")`. Call it again after each context reset or compaction, because a summary does not keep it. The brief gives the store identity, the open check problems and the index. It also gives decisions by status, open status items, owner preferences and the last 10 log entries in full. Use one brief for many questions. Get a new brief when relevant knowledge changes.
- Find, then read only what you need. Use `search` with `mode` `bm25` for topics, `exact` for ids, hashes and paths, and `regex` for patterns. Give a `scope`: a domain or a folder. Then `read` one section.
- Use `links` to see the links into a note and out of it. Use `log_query` to filter the log by time, op, page or task.
- Read a maximum of three sections in one batch, unless you need more. Stop when you have sufficient evidence for the question.
- Write with the version that `read` gave. In `patch_section`, `replace` accepts exactly what `read` returned for a section, subsections included. `append` can add subsections. Heading levels are relative to the section.
- Use `patch_header` for the decision status, implementation status, evidence and verified date of a note. Use `create_note` for a new page. A new page needs a summary and a page header. `create_note` never overwrites. Use `move` to rename a note. The links follow the note.
- Use `status_item`, `set_decision_status` and `log_append`. Do not edit a status item, the Status line of a decision or the log by hand.
- Take a `lease` before a long edit of a shared section.
- Each write is all or nothing and does the checks itself. A refusal gives the reason. For a version conflict, read again and merge. For a failed check, correct the problem that the refusal names. Do not write the knowledge in a different place to get around a refusal.
- `check("<domain>")` lists the open problems of a domain.
- Put scratch work under `<domain>/working/<actor>/`. It becomes project knowledge only when the owner promotes it with `move`.
- When the owner tells you how to do work, record it with `preference`. Quote the words of the owner.
- Before you act on implementation facts that can change, do a check of the real repository, branch, code and deployed configuration. Keep these apart: agreed scope, local behavior that you verified in the source, configuration snapshots, proposals, historical or superseded claims, and unknowns. The store is not proof of a deployment or of a test result.
- If `read` shows a live source, do a check of the live system before you act on the copy.

## Report memory-system problems

- The store records each tool refusal and each error in `<ops-domain>` automatically.
- Also record each problem with the memory system immediately. Examples: the system fails, refuses a correct write, needs too many calls, misses in search or does not have a tool. Use `log_append`:

  ```text
  log_append(
    domain="<ops-domain>",
    op="FAILURE: ..." | "WRONG REFUSAL: ..." | "FRICTION: ..." | "SEARCH MISS: ..." | "MISSING TOOL: ..." | "IDEA: ...",
    pages=[the pages involved, or "<ops-domain>/log"],
    source=<what you did>,
    outcome=<what occurred, and the workaround>,
  )
  ```

- If the memory server is not available, tell the owner. Continue the work that does not need the store. Keep the knowledge edits that you could not do in your reply, and mark them as not done. When the server is available again, record the outage in `<ops-domain>`. Do not write the knowledge in a different place.

## Retrieval and decision checkpoints

- Before you answer a question about a project contract, or propose a replacement, read its authoritative section. Find it through the index. Keep the exact field names and the ownership.
- If sources do not agree, keep the latest explicit decision of the owner apart from the current implementation. Do not join them by inference.
- When the owner explicitly accepts or corrects a decision, record the decision before you close the topic.
- In a domain whose decision records have a `- **Status:**` line, add the record with its Status line. Change the status later only with `set_decision_status`.
- Record alternatives that the owner did not accept as proposals, not as accepted contracts.
- Batch routine reads. Do not wait until the end of the session to update accepted contracts.

## Maintain the knowledge

### One authoritative home, no duplicate rules

- Each decision, contract, threshold, schema and implementation fact has one authoritative topic page or section. The index shows it. Read that owner before you answer about it or change it. A copied summary is not an independent authority.
- Before you add knowledge, find its owner. Update that section. Do not write a second explanation in a plan, handoff, status page or decision note.
- If there is no owner, select one and add it to the index first.
- Notes that support a topic contain a link, and only their own rationale, dependencies or work status. Do not copy field lists, thresholds, rules or implementation details into them. A short summary for navigation can describe the topic. It must not repeat a contract that can change.
- When a decision changes, use `links` to see the notes that link to the owner. Search current notes for the affected identifiers and old words. Update the owner. Replace duplicate current guidance with links. Repair the navigation and the work status that the change affects. Do not edit each linked page only to repeat the change. Read each candidate before you patch it.
- If you find copies that do not agree, use the latest explicit decision of the owner for the current guidance. Keep implementation evidence apart. Put the topic in one place, and keep its unique rationale or evidence at the correct owner. If the authority is not clear, report the conflict. Do not invent a solution. Do not turn a small task into a cleanup of the full store.
- Keep the original source snapshots and the append-only log as they are. Decision records and new log entries give the date of the change, the reason and the result. They link to the authoritative section. They do not contain the full current specification. Label old plans that you can still edit as historical, and link them to the current owner.
- Before you close an update, make sure that the affected guidance has one owner. Do a search in the scope of the topic. Make sure that it finds no other copies or old rules. The checks of the store already cover links and anchors. Report each duplicate that you cannot remove. Do not claim an audit of the full store from a check of one topic.

### Navigation, evidence and verification

- Read the affected sections before you update them. Update only the dependencies and work status that important decisions, discoveries, code changes or verification affect. For the content of a contract, obey the ownership rules above.
- On a new topic page, or a page that you change much, give the page header with `create_note` or `patch_header`. The page header has the **Decision status**, **Implementation status** and **Evidence checked**. Evidence gives the source and date, and the repository commit and symbols or the SDK version where relevant. If you do not know the deployment state, write that. Keep these facts that can change in the store, not in agent instructions.
- Each page needs a summary, which is its line in the index. `create_note` needs one. To change it, use `patch_section` with `summary`. Do not keep counts or lists by hand when the store makes them.
- The log is append-only. The database stamps the time. Use `log_append` with `op`, `pages`, `source` and `outcome` for important imports, updates, substantive queries, verification and maintenance. Put routine reads together in one query entry. To correct an entry, append a new correction entry.
- Link with `[[<domain>/<page>#<Heading>]]` to connect related concepts, decisions, contracts and evidence. The store keeps the backlinks, and `move` repairs them. Do not leave a page without links.
- Before a handoff or a context reset, update the status: verified work, work that is not finished, blockers and the next concrete action. Use `status_item` where the domain keeps status as items. If the status page is prose, patch the status page. Then append to the log. Mark implemented, verified and deployed work separately.
