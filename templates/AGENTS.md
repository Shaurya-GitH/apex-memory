# Agent rules for an apex-memory store

> **How to use this template.** These are rules for agents that use an apex-memory store through MCP. Copy this file into your own `AGENTS.md` or `CLAUDE.md`. Replace the placeholders `<server-name>`, `<date>`, `<database URL or path>` and `<archive path>`. Do not write a list of domains here: the store gives the domains and their descriptions. Remove this note. Keep one copy of these rules. If two agents read them, let one file import or link the other. The tool names are the names that the server gives. Your client can add a prefix, for example `mcp__<server-name>__brief`.

## Select the relevant domain

Since `<date>`, all knowledge lives in the apex-memory store at `<database URL or path>`. Use only the `<server-name>` MCP tools to get to it.

1. At the start of a session, call `list_domains`. Call it again when the subject of the work changes. The result gives each domain with its description, and the global preferences of the owner.
2. Select the domains whose description agrees with the subject of the work. Select by subject, not by the repository or the tool name. Zero, one or more domains can agree. Use only the domains that agree.
3. Call `brief` for each domain that you selected.
4. If no description agrees, do not put the knowledge in an unrelated domain. Use the procedure in [When no domain agrees](#when-no-domain-agrees).

### When no domain agrees

Use this procedure only if the work will make knowledge that is important to keep, for example decisions, contracts, findings or plans. Do not use it for a single question.

1. Look at the `preferences` that `list_domains` gives. If a preference says "No knowledge base for <topic>" for this topic, do not ask. Do the work without a domain.
2. Ask the owner one time, at the start of the work. Ask one short question. Propose a name and a description of one line. Example: "Do you want a knowledge base `tax-filing` for this work: tax returns and their deadlines?"
3. Do the step that agrees with the answer of the owner:
   - **Yes:** Call `create_domain` with that name and description. Then call `brief`.
   - **No, never for this topic:** Call `preference` with `action="add"` and no domain. For `text`, write "No knowledge base for <topic>". For `owner_quote`, give the words of the owner. Do not ask again for that topic.
   - **Not now:** Do not record the answer. Do not ask again in this session.
4. A worker (role `worker`) does not create a domain and does not record a preference. A worker tells the session of the owner that no domain agrees.

- The domain with `ops: true` holds the failures and friction of the memory system itself. See [Report memory-system problems](#report-memory-system-problems).
- Use only the relevant domain for project context and log entries.
- Each domain has its own description, index, status, append-only log, decision records and authoritative topic owners.
- Keep the description of a domain current. When the scope of a domain changes, change the summary of `<domain>/index`. Write what the domain holds and when to select it, for example "Not for X; use Y".
- Do not read a domain only because the code is in a related repository. Do not log generic work in a project domain.
- Read another domain only for an explicit cross-domain dependency. Link to its authoritative owner. Do not copy its contracts.
- Repository instructions still govern their code. These rules for knowledge do not give permission for unrelated changes to an application.

## Use the memory store

- Do not read or write knowledge in other places, such as local markdown files or other note tools. If you keep an archive of old notes at `<archive path>`, it is history only. It is not a source.
- At the start of a relevant task or session, call `brief(domain)` for the domain that you selected. Call it again after each context reset or compaction, because a summary does not keep it. The brief gives the store identity, the open check problems and the index. It also gives decisions by status, open status items, owner preferences and the last 10 log entries in full. Use one brief for many questions. Get a new brief when relevant knowledge changes.
- Find, then read only what you need. Use `search` with `mode` `bm25` for topics, `exact` for full ids, hashes and paths, and `regex` for patterns. Give a `scope`: a domain or a folder. Then `read` one section.
- If `read` cannot find a heading or a note, the refusal gives `candidates`. Use one of them.
- Use `links` to see the links into a note and out of it. Use `log_query` to filter the log by time, op, page or task.
- Read a maximum of three sections in one batch, unless you need more. Stop when you have sufficient evidence for the question.
- Write with the version that `read` gave. In `patch_section`, `replace` accepts exactly what `read` returned for a section, subsections included. Heading levels are relative to the section.
- `append` can add subsections. It does not need a version.
- To change only the text before the first subsection, use `replace_own_text`. The subsections do not change.
- To make two or more writes together, use `batch`. For example, use it for two notes that link to each other. If one write is refused, the store rolls back all of them.
- Use `patch_header` for the decision status, implementation status, evidence and verified date of a note. Use `create_note` for a new page. A new page needs a summary and a page header. `create_note` never overwrites. Use `move` to rename a note. The links follow the note.
- Use `status_item`, `set_decision_status` and `log_append`. Do not edit a status item, the Status line of a decision or the log by hand. To change the text of an open status item, use `status_item` with `update`.
- Take a `lease` before a long edit of a shared section.
- Each write is all or nothing and does the checks itself. A refusal gives the reason. For a version conflict, read again and merge. For a failed check, correct the problem that the refusal names. Do not write the knowledge in a different place to get around a refusal.
- `check(domain)` lists the open problems of a domain.
- Put scratch work under `<domain>/working/<actor>/`. It becomes project knowledge only when the owner promotes it with `move`.
- When the owner tells you how to do work, record it with `preference`. Quote the words of the owner.
- Before you act on implementation facts that can change, do a check of the real repository, branch, code and deployed configuration. Keep these apart: agreed scope, local behavior that you verified in the source, configuration snapshots, proposals, historical or superseded claims, and unknowns. The store is not proof of a deployment or of a test result.
- If `read` shows a live source, do a check of the live system before you act on the copy.

## Report memory-system problems

- The ops domain is the domain that `list_domains` marks with `ops: true`. The store records each tool refusal and each error in it automatically.
- Also record each problem with the memory system immediately. Examples: the system fails, refuses a correct write, needs too many calls, misses in search or does not have a tool. Use `log_append`:

  ```text
  log_append(
    domain=<the ops domain>,
    op="FAILURE: ..." | "WRONG REFUSAL: ..." | "FRICTION: ..." | "SEARCH MISS: ..." | "MISSING TOOL: ..." | "IDEA: ...",
    pages=[the pages involved, or the log page of the ops domain],
    source=<what you did>,
    outcome=<what occurred, and the workaround>,
  )
  ```

- If the memory server is not available, tell the owner. Continue the work that does not need the store. Keep the knowledge edits that you could not do in your reply, and mark them as not done. When the server is available again, record the outage in the ops domain. Do not write the knowledge in a different place.

## Retrieval and decision checkpoints

- Before you answer a question about a project contract, or propose a replacement, read its authoritative section. Find it through the index. Keep the exact field names and the ownership.
- If sources do not agree, keep the latest explicit decision of the owner apart from the current implementation. Do not join them by inference.
- When the owner explicitly accepts or corrects a decision, record the decision before you close the topic.
- In a domain whose decision records have a `- **Status:**` line, add the record with its Status line. Change the status later only with `set_decision_status`.
- If a decision was accepted in error, set it to `void` with `set_decision_status` and give the reason. Do not make a new record only to supersede it.
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
