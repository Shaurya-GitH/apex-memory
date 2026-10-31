"""The memory store: a knowledge base of markdown notes for AI agents, kept in a database.

Every write tool runs in one transaction: mutate -> guards -> check (any new problem rolls everything back) ->
event -> commit. Tools return plain dicts and raise Refused for expected failures (version conflict, failed check,
guard). The database is reached only through a backend (apex_memory.backends); the SQL here is neutral.
"""
import collections, contextlib, datetime, difflib, functools, inspect, json, re, threading, unicodedata, uuid

from . import package_version
from .backends import backup, open_backend, safety_backup_dir

STALE_DAYS = 14        # a verify_live source older than this is flagged
DUP_MIN = 200          # paragraphs at least this long count as a claim for the duplicate check
COORD_POWER = 2        # bm25: score x (share of the query terms a hit holds) ** COORD_POWER; tuned on real searches

FENCE = re.compile(r'^\s*(```|~~~)')
HEADING = re.compile(r'^(#{1,6})[ \t]+(.*?)[ \t]*$')
LINK = re.compile(r'!?\[\[([^\]|#]*)(#[^\]|]*)?(?:\|[^\]]*)?\]\]')
HEADER = re.compile(r'^> \*\*Decision status:\*\* (.*?) \*\*Implementation status:\*\* (.*?) \*\*Evidence checked:\*\* (.*)$')
RECORD = re.compile(r'^([DP]\d+) — (.*)$')
STATUS_LINE = re.compile(r'^- \*\*Status:\*\* (accepted|proposed|rejected|void|superseded by ([DP]\d+))[ \t]*$', re.M)
DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
CATALOG_ROW = re.compile(r'^\| \[\[([^\]|#]+)\]\] \| (.*) \|$')
STATUS_KINDS = {'Current state': 'state', 'Unfinished / open': 'open', 'Blockers': 'blocker', 'Next concrete action': 'next'}
ACTOR = re.compile(r'^[a-z0-9][a-z0-9-]*$')
SOURCE_KINDS = ('snapshot', 'live', 'transcript', 'web', 'owner')
FIXED_PAGES = ('index', 'status', 'log', 'decisions')
BATCH_TOOLS = ('create_note', 'patch_section', 'patch_header', 'status_item', 'set_decision_status', 'log_append', 'source')
OPS_DOMAIN = 'memory-ops'   # default domain for the memory system's own feedback: failures and friction, logged by agents and automatically
TRANSITIONS = {'proposed': {'accepted', 'rejected', 'superseded'}, 'accepted': {'superseded', 'void'}}
PATCH_OPS = ['replace', 'append', 'prepend', 'replace_own_text']


class Refused(Exception):
    """An expected refusal; `info` is returned to the agent as-is."""
    def __init__(self, error, **info):
        super().__init__(error)
        self.info = {'ok': False, 'error': error, **info}


# ---------- markdown model ----------

def hp(path):
    return json.dumps(list(path), ensure_ascii=False)


def split_frontmatter(text):
    if text.startswith('---\n'):
        end = text.find('\n---\n', 3)
        if end != -1:
            return text[4:end + 1], text[end + 5:]
    return None, text


def parse_frontmatter(fm):
    data, key = {}, None
    for line in fm.splitlines():
        m = re.match(r'^([A-Za-z_][\w-]*):[ \t]*(.*)$', line)
        if m:
            key = m.group(1)
            data[key] = m.group(2) if m.group(2) else []
        elif key and isinstance(data[key], list) and re.match(r'^\s+- ', line):
            data[key].append(line.split('- ', 1)[1])
        else:
            raise ValueError(f'unsupported frontmatter line: {line!r}')
    return data


def render_frontmatter(data):
    out = ['---\n']
    for k, v in data.items():
        out.append(f'{k}:\n' + ''.join(f'  - {i}\n' for i in v) if isinstance(v, list) else f'{k}: {v}\n')
    return ''.join(out) + '---\n'


def parse_sections(text, base_level=0, base_path=()):
    """Split markdown into its own text (before the first heading) and heading sections, fence-aware.

    Heading levels in `text` are relative to base_level. A repeated sibling heading gets ' [n]' in its
    path (the heading text is kept). Returns (own, sections, duplicates)."""
    own, secs, dups, stack, seen, fence = [], [], [], [], collections.Counter(), False
    for line in text.splitlines(keepends=True):
        if FENCE.match(line):
            fence = not fence
        m = None if fence else HEADING.match(line.rstrip('\n'))
        if m:
            level, heading = base_level + len(m.group(1)), m.group(2)
            stack = [s for s in stack if s[0] < level]
            parent = tuple(base_path) + tuple(k for _, k in stack)
            seen[parent, heading] += 1
            n = seen[parent, heading]
            key = heading if n == 1 else f'{heading} [{n}]'
            if n > 1:
                dups.append(list(parent) + [key])
            stack.append((level, key))
            secs.append({'heading': heading, 'level': level, 'path': list(parent) + [key], 'body': ''})
        elif secs:
            secs[-1]['body'] += line
        else:
            own.append(line)
    return ''.join(own), secs, dups


def has_heading(text):
    return bool(parse_sections(text)[1])


def links_in(body):
    out, fence = [], False
    for line in body.splitlines():
        if FENCE.match(line):
            fence = not fence
            continue
        if not fence:
            out += [(t.strip(), a if a.strip('#').strip() else '') for t, a in LINK.findall(re.sub(r'`[^`]*`', '', line)) if t.strip()]  # [[x#]] = no anchor
    return out


def header_line(n):
    return (f"> **Decision status:** {n['decision_status']} **Implementation status:** {n['impl_status']}"
            f" **Evidence checked:** {n['evidence']}\n")


def status_items_in(body):
    """Top-level bullets (with continuation lines) and paragraphs; 'None.' means no items."""
    items, cur = [], None
    for line in body.splitlines():
        if line.startswith('- '):
            cur = [line[2:]]
            items.append(cur)
        elif not line.strip():
            cur = None
        elif cur is not None:
            cur.append(line)
        else:
            cur = [line]
            items.append(cur)
    return [t for t in ('\n'.join(i).strip() for i in items) if t and t != 'None.']


def local(ts):
    return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone().isoformat(timespec='seconds')


def today():
    return datetime.date.today().isoformat()


# ---------- the store ----------

class Store:
    def __init__(self, url, actor='unknown', role='owner', run_id: str | None = None, ops_domain: str = OPS_DOMAIN):
        """url: a database URL (see apex_memory.backends) or a SQLite file path. The schema is created or
        migrated on open. ops_domain receives automatic records of refusals and errors, when it exists."""
        # ponytail: roles are declared at registration, not authenticated (one machine, one owner)
        if role not in ('owner', 'worker') or not ACTOR.match(actor):
            raise ValueError(f'role must be owner or worker and actor a path-safe name: {role!r}, {actor!r}')
        self.actor, self.role, self.run_id, self.ops_domain = actor, role, run_id or str(uuid.uuid4()), ops_domain
        # One connection, shared by the MCP server's worker threads: one tool call at a time per process.
        # Processes are serialised by the backend's transaction (one writer).
        self._lock = threading.RLock()
        self._tx = self._batch = False   # inside a write transaction; inside batch (checks run once, at its end)
        self.db = open_backend(url)
        self.location = self.db.location
        try:
            with self._write():  # under the writer lock: of two servers started together, one migrates
                version, known = self.db.schema_version(), len(self.db.migrations())
                saved = self._safety_backup(version, known) if 0 < version < known else None
                if self.db.migrate():
                    for key, value in (('store_id', str(uuid.uuid4())), ('created_at', today())):
                        self.db.execute('INSERT INTO meta VALUES (?, ?)', (key, value))
                if saved and self._one('SELECT 1 FROM notes WHERE domain = ? LIMIT 1', self.ops_domain):
                    self._event(self.ops_domain, f'MIGRATION: schema {version} to {known}', {'from': version, 'to': known, 'backup': saved},
                                f'\n- **Source / reason:** apex-memory {package_version()} opened the store at schema {version}\n'
                                f'- **Outcome:** migrated to schema {known}; safety backup {saved}\n\n')
            self.store_id = self._one("SELECT value FROM meta WHERE key='store_id'")[0]
        except BaseException:
            self.db.close()
            raise

    # ----- plumbing -----

    def _safety_backup(self, version, known):
        """A verified backup before pending migrations run; on failure nothing is migrated."""
        to = safety_backup_dir(self.db)
        try:
            return str(backup(self.db, to, name=f'pre-migration-{datetime.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex}-v{version}-to-v{known}.db'))
        except Exception as e:
            raise RuntimeError(f'{self.location} needs migration from schema {version} to {known}, but the safety backup to {to} failed '
                               f'({type(e).__name__}: {e}); the store is not migrated. Free disk space or set APEX_MEMORY_BACKUP_DIR '
                               'to a writable folder, then start again.') from e

    @contextlib.contextmanager
    def _write(self):
        with self._lock:
            if self._tx:  # a write inside batch joins its transaction
                yield
                return
            with self.db.transaction():
                self._tx = True
                try:
                    yield
                finally:
                    self._tx = False

    def _q(self, sql, *args):
        return self.db.query(sql, args)

    def _one(self, sql, *args):
        return self.db.one(sql, args)

    def _event(self, domain, op, refs=None, body=None, task_id=None, ts=None, actor=None):
        row = {'domain': domain, 'actor': actor or self.actor, 'task_id': task_id, 'run_id': self.run_id, 'op': op,
               'refs': json.dumps(refs, ensure_ascii=False), 'body': body}
        return self.db.insert('events', row | ({'ts': ts} if ts else {}))

    def _guarded(self, domain, op, task_id, mutate):
        """One write transaction: mutate, refuse on any new check problem, log a tool event. Inside batch the
        checks run once, at the end of the batch."""
        with self._write():
            # ponytail: checks the whole domain before and after each write; scope to affected notes if domains grow large
            domains = [domain] if domain else sorted(self._domains())
            before = None if self._batch else self._check_state(domains)
            result, refs, body = mutate()
            if before is not None:
                self._refuse_new(before, domains)
            self._event(domain, 'tool:' + op, refs, body, task_id)
            return {'ok': True, **result}

    def _check_state(self, domains):
        return {(d, key): (text, n, shown) for d in domains for key, text, n, shown in self._problems(d)}

    def _refuse_new(self, before, domains):
        new = sorted(text for key, (text, n, shown) in self._check_state(domains).items()
                     if shown and (key not in before or n > before[key][1]))
        if new:
            raise Refused('check_failed', problems=new)

    def _catalog_generated(self, domain):
        return bool(self._one("SELECT 1 FROM sections WHERE note = ? AND generated = 'catalog'", f'{domain}/index'))

    def _record_failure(self, tool, op, info, bound):
        """Write a refusal or error into the ops domain, if it exists. Never raises."""
        try:
            if not self._one('SELECT 1 FROM notes WHERE domain = ? LIMIT 1', self.ops_domain):
                return
            def redact(value, key=None):
                if isinstance(value, str) and (key in ('content', 'body') or len(value) > 200):
                    return f'<{len(value)} chars>'
                if isinstance(value, dict):
                    return {k: redact(v, k) for k, v in value.items()}
                if isinstance(value, (list, tuple)):
                    return [redact(v) for v in value]
                return value
            args = {k: redact(v, k) for k, v in bound.items() if k != 'self' and v is not None}
            result = {k: v for k, v in info.items() if k != 'content'}
            body = json.dumps({'tool': tool, 'args': args, 'result': result}, ensure_ascii=False, default=str)[:4000]
            with self._write():
                self._event(self.ops_domain, op, {'tool': tool, 'error': result.get('error')}, body)
        except Exception:
            pass

    def _domains(self):
        return {r[0] for r in self._q('SELECT DISTINCT domain FROM notes')}

    def _need_domain(self, domain):
        if domain not in self._domains():
            raise Refused('unknown_domain', domain=domain, domains=sorted(self._domains()))

    def _is_working(self, path):
        parts = path.split('/')
        return len(parts) > 3 and parts[1] == 'working'

    def _check_path(self, path):
        if path.split('/')[1:2] == ['working'] and not self._is_working(path):
            raise Refused('bad_path', path=path, hint='working notes live under <domain>/working/<actor>/…')

    def _need_write(self, path):
        if self.role != 'owner' and not path.startswith(f"{path.split('/')[0]}/working/{self.actor}/"):
            raise Refused('not_allowed', path=path, role=self.role,
                          hint=f"a worker writes only its own working notes under <domain>/working/{self.actor}/")

    def _need_owner(self, action):
        if self.role != 'owner':
            raise Refused('owner_only', action=action, role=self.role)

    def _check_leases(self, section_ids):
        ids = list(section_ids)
        r = self._one(f'SELECT * FROM leases WHERE section IN ({",".join("?" * len(ids))}) AND run_id != ? AND expires_at > {self.db.now()}',
                      *ids, self.run_id) if ids else None
        if r:
            raise Refused('leased', section_id=r['section'], holder=r['actor'], expires_at=local(r['expires_at']),
                          hint='another agent is editing this part; wait for the lease to end or ask it to release')

    def _note(self, path):
        n = self._one('SELECT * FROM notes WHERE path = ?', path)
        if not n:
            if not path:
                raise Refused('path_required', hint='give path (a whole note), path and heading (one section), or section_id')
            domain = path.split('/')[0]
            pool = [r[0] for r in self._q('SELECT path FROM notes WHERE domain = ?', domain)] or [r[0] for r in self._q('SELECT path FROM notes')]
            raise Refused('not_found', path=path, candidates=_closest(path, pool))
        return n

    def _sections(self, note):
        return self._q('SELECT * FROM sections WHERE note = ? ORDER BY position', note)

    def _subtree(self, t):
        rows = self._sections(t['note'])
        i = next(i for i, r in enumerate(rows) if r['id'] == t['id'])
        if t['level'] is None:
            return [rows[i]]
        j = i + 1
        while j < len(rows) and rows[j]['level'] > t['level']:
            j += 1
        return rows[i:j]

    def _ancestors(self, t):
        rows, out, lvl = self._sections(t['note']), [], t['level']
        if lvl is None:
            return out
        for r in reversed([r for r in rows if r['position'] < t['position']]):
            if r['level'] is not None and r['level'] < lvl:
                out.append(r)
                lvl = r['level']
        return out

    def _target(self, section_id=None, path=None, heading=None):
        if section_id is not None:
            t = self._one('SELECT * FROM sections WHERE id = ?', section_id)
            if not t or (path and t['note'] != path):
                raise Refused('not_found', section_id=section_id, path=path)
            return t
        self._note(path)
        if heading is None:
            raise Refused('heading_required', path=path, hint='give heading (text or path; heading=[] is the text before the first heading) or section_id')
        if isinstance(heading, list):
            t = self._one('SELECT * FROM sections WHERE note = ? AND heading_path = ?', path, hp(heading))
            if not t:
                raise Refused('not_found', path=path, heading=heading, candidates=self._close_headings(path, heading[-1] if heading else ''),
                              hint='read the note without heading for its outline')
            return t
        rows = [r for r in self._sections(path) if r['heading'] == heading or (json.loads(r['heading_path']) or [''])[-1] == heading]
        if len(rows) != 1:
            raise Refused('not_found' if not rows else 'ambiguous_heading', path=path, heading=heading,
                          candidates=[self._candidate(r) for r in rows] or self._close_headings(path, heading),
                          hint='use a candidate section_id, or read the note without heading for its outline')
        return rows[0]

    def _candidate(self, r):
        return {'section_id': r['id'], 'heading_path': json.loads(r['heading_path'])}

    def _close_headings(self, path, heading):
        """Up to 5 sections of the note whose heading is closest to `heading`; a missing date suffix still matches."""
        rows = {_bare(r['heading']): r for r in reversed(self._sections(path)) if r['heading']}
        return [self._candidate(rows[h]) for h in _closest(_bare(heading), list(rows))]

    def _touch(self, note):
        self.db.execute('UPDATE notes SET version = version + 1, updated_at = ? WHERE path = ?', (today(), note))

    def _resolve(self, target):
        t = target.strip().removesuffix('.md')
        if self._one('SELECT 1 FROM notes WHERE path = ?', t):
            return t
        if '/' in t and t.split('/')[0] not in self._domains():
            return t  # a domain not in this store: external, never matched by file name
        name = t.rsplit('/', 1)[-1]
        rows = self._q("SELECT path FROM notes WHERE lower(path) = lower(?) OR lower(path) LIKE lower(?) ESCAPE '!'",
                       name, '%/' + _like(name))
        return rows[0]['path'] if len(rows) == 1 else t

    def _anchor_section(self, dst, anchor):
        last = anchor.split('#')[-1].strip()
        if not last:
            return None
        if last.startswith('^'):
            rx = re.compile(r'\^' + re.escape(last[1:]) + r'\s*$', re.M)
            return next((r['id'] for r in self._sections(dst) if rx.search(r['body'])), None)
        r = self._one('SELECT id FROM sections WHERE note = ? AND heading = ? ORDER BY position LIMIT 1', dst, last)
        return r['id'] if r else None

    def _relink(self, section_ids):
        for sid in section_ids:
            self.db.execute('DELETE FROM links WHERE src_section = ?', (sid,))
            s = self._one('SELECT * FROM sections WHERE id = ?', sid)
            if s['generated'] and s['generated'] != 'log':
                continue
            body = s['body']
            if s['heading'] is None:
                n = self._note(s['note'])
                body = (header_line(n) if n['decision_status'] is not None else '') + body
            for target, anchor in links_in(body):
                dst = self._resolve(target)
                if not self._one('SELECT 1 FROM links WHERE src_section = ? AND dst = ? AND anchor = ?', sid, dst, anchor):
                    self.db.execute('INSERT INTO links VALUES (?, ?, ?, ?)',
                                    (sid, dst, anchor, self._anchor_section(dst, anchor) if anchor else None))

    def _resolve_dangling(self, note):
        """Re-resolve links into a note after it changed: every anchor (a ^block can leave a surviving section), and
        links still waiting under a short name or the full path for a note that now exists."""
        for l in self._q("SELECT src_section, dst, anchor FROM links WHERE dst = ? AND anchor != ''", note):
            self.db.execute('UPDATE links SET dst_section = ? WHERE src_section = ? AND dst = ? AND anchor = ?',
                            (self._anchor_section(l['dst'], l['anchor']), l['src_section'], l['dst'], l['anchor']))
        waiting = self._q('SELECT DISTINCT src_section FROM links WHERE dst NOT IN (SELECT path FROM notes) AND lower(dst) IN (lower(?), lower(?))',
                          note.rsplit('/', 1)[-1], note)
        self._relink([r[0] for r in waiting])

    # ----- rendering -----

    def _text(self, s):
        """A section's own text, with generated content rendered from rows."""
        g, body = s['generated'], s['body']
        if not g:
            return body
        domain = s['note'].split('/')[0]
        if g == 'catalog':
            rows = self._q("SELECT path, summary FROM notes WHERE domain = ? AND summary IS NOT NULL AND path NOT LIKE domain || '/working/%' "
                           "AND path != domain || '/index' ORDER BY coalesce(catalog_position, 0), path", domain)
            return body + ''.join(f"| [[{r['path']}]] | {r['summary']} |\n" for r in rows) + '\n'
        if g.startswith('status:'):
            items = self._q('SELECT text FROM status_items WHERE domain = ? AND kind = ? AND closed_at IS NULL '
                            'ORDER BY position, id', domain, g.split(':', 1)[1])
            return body + ('\n' + ''.join(f"- {r['text']}\n" for r in items) + '\n' if items else '\nNone.\n\n')
        if g == 'log':
            return body + ''.join(self._entry(e) for e in self._q(
                "SELECT * FROM events WHERE domain = ? AND op NOT LIKE 'tool:%' ORDER BY id", domain))
        raise ValueError(g)

    def _entry(self, e):
        return f"## {local(e['ts'])} — {e['op']}\n{e['body']}"

    def _subtree_text(self, t, rows=None):
        rows = rows or self._subtree(t)
        base = t['level'] or 0
        return self._text(rows[0]) + ''.join('#' * (r['level'] - base) + f" {r['heading']}\n" + self._text(r) for r in rows[1:])

    def render_note(self, path, frontmatter=True):
        n = self._note(path)
        out = []
        if frontmatter and n['meta']:
            meta = json.loads(n['meta'])
            cols = {'title': n['title'], 'status': n['status'], 'updated': n['updated_at']}
            out.append(render_frontmatter({k: cols.get(k, meta.get(k)) for k in meta['_keys']}))
        for s in self._sections(path):
            if s['heading'] is None:
                out.append(('\n' + header_line(n) if n['decision_status'] is not None else '') + s['body'])
            else:
                out.append('#' * s['level'] + f" {s['heading']}\n" + self._text(s))
        return ''.join(out)

    # ----- decisions -----

    def _records(self, domain):
        out = []
        for s in self._sections(f'{domain}/decisions'):
            m = s['heading'] and RECORD.match(s['heading'])
            if m:
                out.append({'id': m.group(1), 'title': m.group(2), 'section': s, 'status': STATUS_LINE.findall(s['body'])})
        return out

    def _derive_decisions(self, domain):
        self.db.execute('DELETE FROM decisions WHERE domain = ?', (domain,))
        for r in self._records(domain):
            if len(r['status']) == 1:
                value, by = r['status'][0]
                d = DATE.search(r['title'])
                if not self._one('SELECT 1 FROM decisions WHERE domain = ? AND id = ?', domain, r['id']):  # the first record of an id wins
                    self.db.execute('INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?)',
                                    (domain, r['id'], r['title'], value.split()[0], r['section']['id'], d and d.group(), by or None))

    def _guard_records(self, before, domain):
        after = {r['id']: r for r in self._records(domain)}
        for rid, old in before.items():
            new = after.get(rid)
            if not new:
                raise Refused('decision_removed', id=rid, hint='decision records are never deleted; supersede them with set_decision_status')
            if len(old['status']) != 1:
                continue  # an unclassified record may gain its one Status line
            if new['status'] != old['status']:
                raise Refused('status_line_changed', id=rid, hint='change a Status line only with set_decision_status')
            if old['status'] != [('proposed', '')] and not new['section']['body'].rstrip().startswith(old['section']['body'].rstrip()):
                raise Refused('decision_overwritten', id=rid, hint='an accepted, superseded or rejected record accepts append only')

    # ----- check -----

    def problems(self, domain):
        return [text for _, text, _, shown in self._problems(domain) if shown]

    def _problems(self, domain):
        """Every problem as (key, text, count, shown). A write is refused when a shown problem is new: its key was
        not there before, or its count grew (more copies of one paragraph). A latent problem (shown False: a
        record without a Status line in a domain that has none) is existing debt once the domain adopts the rule."""
        p, out, domains = [], [], self._domains()
        for r in self._q('SELECT s.note, s.heading, l.dst, l.anchor, l.dst_section, '
                         '(SELECT 1 FROM notes WHERE path = l.dst) AS found FROM links l '
                         'JOIN sections s ON s.id = l.src_section JOIN notes n ON n.path = s.note WHERE n.domain = ?', domain):
            where = f"{r['note']} § {r['heading'] or '(top)'}"
            if not r['found']:
                if '/' not in r['dst'] or r['dst'].split('/')[0] in domains:
                    p.append(f"broken link [[{r['dst']}]] in {where}")
            elif r['anchor'] and r['dst_section'] is None:
                p.append(f"missing anchor [[{r['dst']}{r['anchor']}]] in {where}")
        linked = '' if self._catalog_generated(domain) else (
            ' AND NOT EXISTS (SELECT 1 FROM links l JOIN sections s ON s.id = l.src_section WHERE s.note = ? AND l.dst = notes.path)')
        for r in self._q("SELECT path FROM notes WHERE domain = ? AND summary IS NULL AND path != ? AND path NOT LIKE domain || '/working/%'"
                         + linked, domain, f'{domain}/index', *([f'{domain}/index'] if linked else [])):
            p.append(f"{r['path']} has no summary (missing from the index)")
        if self._one("SELECT 1 FROM notes WHERE path = ? AND (summary IS NULL OR trim(summary) = '')", f'{domain}/index'):
            p.append(f'{domain}/index has no summary: the domain description that list_domains shows')
        claims = collections.defaultdict(set)
        for s in self._q('SELECT s.note, s.body FROM sections s JOIN notes n ON n.path = s.note '
                         "WHERE n.domain = ? AND s.generated IS NULL AND n.path NOT LIKE n.domain || '/working/%' AND n.path != n.domain || '/log'",
                         domain):
            for para in re.split(r'\n[ \t]*\n', s['body']):
                norm = ' '.join(para.split())
                if len(norm) >= DUP_MIN:
                    claims[norm].add(s['note'])
        out += [(('same paragraph', domain, norm), f"same paragraph in {', '.join(sorted(notes))}: {norm[:80]}…", len(notes), True)
                for norm, notes in claims.items() if len(notes) > 1]
        recs = self._records(domain)
        ids = collections.Counter(r['id'] for r in recs)
        adopted = any(r['status'] for r in recs)  # a domain adopts the Status-line rule with its first Status line
        for r in recs:
            if ids[r['id']] > 1:
                p.append(f"decision {r['id']} is recorded more than once")
            if len(r['status']) != 1:
                text = f"decision {r['id']} needs exactly one '- **Status:**' line (has {len(r['status'])})"
                out.append((text, text, 1, bool(r['status']) or adopted))
            elif r['status'][0][1] and r['status'][0][1] not in ids:
                p.append(f"decision {r['id']} is superseded by unknown {r['status'][0][1]}")
        cutoff = (datetime.date.today() - datetime.timedelta(days=STALE_DAYS)).isoformat()
        for r in self._q('SELECT n.path, n.verified_at, src.id FROM note_sources ns JOIN sources src ON src.id = ns.source '
                         'JOIN notes n ON n.path = ns.note WHERE n.domain = ? AND src.verify_live = 1', domain):
            if not r['verified_at'] or r['verified_at'][:10] < cutoff:
                p.append(f"{r['path']} cites live source {r['id']}; verified_at {r['verified_at'] or 'missing'} is older than {STALE_DAYS} days")
        return [(text, text, 1, True) for text in p] + out

    # ---------- tools ----------

    def check(self, domain: str) -> dict:
        """Read-only check of a domain: broken links and anchors, notes missing from the index, an index without
        its summary (the domain description), the same paragraph claimed in two notes, decision records without
        exactly one Status line, stale live sources. The same check runs inside every write and rolls the write back
        if it adds a problem."""
        self._need_domain(domain)
        p = self.problems(domain)
        return {'ok': True, 'domain': domain, 'problems': p}

    def brief(self, domain: str, log_entries: int = 10, max_chars: int = 12000) -> dict:
        """Start-of-task read: store identity and open check problems, the index, decisions by status and open
        status items, then the last log_entries log entries. max_chars caps the index and the status block
        separately; the log entries always appear in full."""
        self._need_domain(domain)
        by = collections.defaultdict(list)
        for d in self._q('SELECT * FROM decisions WHERE domain = ? ORDER BY id', domain):
            by[d['status']].append(d['id'] + (f"→{d['superseded_by']}" if d['superseded_by'] else ''))
        items = self._q('SELECT * FROM status_items WHERE domain = ? AND closed_at IS NULL ORDER BY kind, position, id', domain)
        events = self._q("SELECT * FROM events WHERE domain = ? AND op NOT LIKE 'tool:%' ORDER BY id DESC LIMIT ?", domain, log_entries)
        index = self.render_note(f'{domain}/index', frontmatter=False).strip()
        prefs = self._q('SELECT * FROM preferences WHERE retired_at IS NULL AND (domain IS NULL OR domain = ?) ORDER BY id', domain)
        mine = self._q('SELECT path FROM notes WHERE path LIKE ? ORDER BY path', f'{domain}/working/{self.actor}/%')
        if not self._catalog_generated(domain):  # index kept as written: show notes it does not mention
            extra = self._q("SELECT path, summary FROM notes n WHERE domain = ? AND summary IS NOT NULL AND path NOT LIKE domain || '/working/%' "
                            "AND path != domain || '/index' AND NOT EXISTS (SELECT 1 FROM links l JOIN sections s ON s.id = l.src_section WHERE s.note = ? AND l.dst = n.path) "
                            'ORDER BY coalesce(catalog_position, 0), path', domain, f'{domain}/index')
            if extra:
                index += '\n\n===== notes not mentioned on the index page\n' + '\n'.join(f"- [[{r['path']}]] {r['summary']}" for r in extra)
        prose = self._one('SELECT 1 FROM notes WHERE path = ?', f'{domain}/status') and not self._one(
            'SELECT 1 FROM sections WHERE note = ? AND generated IS NOT NULL', f'{domain}/status')
        status = '\n'.join(['===== owner preferences', '\n'.join(f"- [#{r['id']}] {r['text']} (owner: \"{r['owner_quote']}\")" for r in prefs) or 'none',
                            *(['===== your working notes', '\n'.join(r['path'] for r in mine)] if mine else []),
                            '===== decisions', '\n'.join(f"{k}: {', '.join(v)}" for k, v in sorted(by.items())),
                            '===== open status items', '\n'.join(f"- [#{i['id']} {i['kind']}] {i['text']}" for i in items) or 'none',
                            *(['===== status page', self.render_note(f'{domain}/status', frontmatter=False).strip()] if prose else [])])
        text = '\n'.join([
            f'store {self.store_id} at {self.location} · domain {domain} · you are {self.actor} ({self.role})',
            f'check: {len(self.problems(domain))} open problems (run check for the list)',
            '===== index', _cap(index, max_chars), _cap(status, max_chars),
            f'===== last {len(events)} log entries', ''.join(self._entry(e) for e in reversed(events)).strip()])
        return {'ok': True, 'store_id': self.store_id, 'db': self.location, 'brief': text,
                'truncated': len(index) > max_chars or len(status) > max_chars}

    def search(self, query: str, mode: str = 'bm25', scope: str = '', limit: int = 10) -> dict:
        """Search note titles and summaries, sections and log entries. mode: 'bm25' (ranked keywords; hits that hold
        more of the query terms rank higher, and weak hits holding few of them drop out), 'exact' (whole words or
        phrase, ignoring case, e.g. ids and hashes) or 'regex' (Python syntax). scope: a domain or folder such as
        'project/docs'; empty searches every domain. Hits carry path, section id, heading path, version and a snippet."""
        scope = scope.strip('/')
        domain = scope.split('/')[0] or None
        inscope, args = (('(t.{0} = ? OR ' + self.db.prefix('t.{0}') + ')', (scope, scope + '/%'))
                         if scope else ('1 = 1', ()))
        log, log_args = ("t.op NOT LIKE 'tool:%' AND t.domain = ?", (domain,)) if domain else ("t.op NOT LIKE 'tool:%'", ())
        hits = []
        if mode == 'bm25':
            texts = []
            for r in self.db.search('notes', query, inscope.format('path'), args):
                hits.append({'kind': 'note', 'path': r['path'], 'version': r['version'], 'snippet': r['snippet'], 'score': r['score']})
                texts.append(f"{r['title'] or ''} {r['summary'] or ''}")
            for r in self.db.search('sections', query, inscope.format('note'), args):
                hits.append(self._hit(r, r['snippet'], r['score']))
                texts.append(f"{r['heading'] or ''} {r['body']}")
            for r in self.db.search('events', query, log, log_args):
                hits.append({'kind': 'event', 'event_id': r['id'], 'ts': local(r['ts']), 'op': r['op'], 'snippet': r['snippet'], 'score': r['score']})
                texts.append(f"{r['op']} {r['body'] or ''}")
        elif mode in ('exact', 'regex'):
            if mode == 'regex':
                try:  # the database can refuse a pattern that Python compiles (e.g. Postgres: at most 255 repetitions)
                    rx = re.compile(query, re.M)
                    rows = self._q(f'SELECT t.* FROM sections t WHERE {self.db.regex("t.body")} AND {inscope.format("note")}', query, *args)
                except re.error as e:
                    raise Refused('bad_regex', detail=str(e))
            else:  # whole words: the text around the query is not a letter or digit where the query starts or ends with one
                rx = re.compile(('(?<![^\\W_])' if query[:1].isalnum() else '') + re.escape(query) + ('(?![^\\W_])' if query[-1:].isalnum() else ''), re.I)
                rows = [r for r in self.db.search('sections', query, inscope.format('note'), args, exact=True) if rx.search(r['body'])]
            for r in sorted(rows, key=lambda r: (r['note'], r['position'])):
                hits.append(self._hit(r, _snip(r['body'], rx.search(r['body']))))
            for r in self._q(f'SELECT * FROM events t WHERE {log} ORDER BY id', *log_args):
                m = rx.search(r['body'] or '')
                if m:
                    hits.append({'kind': 'event', 'event_id': r['id'], 'ts': local(r['ts']), 'op': r['op'], 'snippet': _snip(r['body'], m)})
        else:
            raise Refused('bad_mode', mode=mode, modes=['bm25', 'exact', 'regex'])
        in_working = scope.split('/')[1:2] == ['working']
        visible = [i for i, h in enumerate(hits) if in_working or 'path' not in h or not self._is_working(h['path'])
                   or h['path'].startswith(f"{h['path'].split('/')[0]}/working/{self.actor}/")]
        hits = (_coordinate(query, [hits[i] for i in visible], [texts[i] for i in visible]) if mode == 'bm25'
                else [hits[i] for i in visible])[:limit]
        with self._write():
            self._event(domain, 'tool:search', [h.get('section_id') or h.get('path') or h.get('event_id') for h in hits],
                        json.dumps({'query': query, 'mode': mode, 'scope': scope, 'hits': len(hits)}, ensure_ascii=False))
        return {'ok': True, 'hits': hits}

    def _hit(self, s, snippet, score=None):
        h = {'kind': 'section', 'path': s['note'], 'section_id': s['id'], 'heading_path': json.loads(s['heading_path']),
             'version': s['version'], 'snippet': snippet}
        return h if score is None else {**h, 'score': score}

    def read(self, path: str | None = None, heading: list[str] | str | None = None, section_id: int | None = None,
             max_chars: int = 30000) -> dict:
        """Read a whole note (no heading) or one section with its subsections. A section's content has headings
        relative to it (a direct child is '#'), exactly what patch_section's replace accepts. Returns ids and
        versions for the outline, the page header fields and live-source flags."""
        if section_id is not None or heading is not None:
            t = self._target(section_id, path, heading)
            rows, content = self._subtree(t), None
            content = self._subtree_text(t, rows)
            n = self._note(t['note'])
            out = {'path': t['note'], 'section_id': t['id'], 'heading_path': json.loads(t['heading_path']), 'version': t['version']}
        else:
            n = self._note(path)
            rows, content = self._sections(path), self.render_note(path)
            out = {'path': path, 'version': n['version'], 'title': n['title'], 'summary': n['summary'],
                   'decision_status': n['decision_status'], 'impl_status': n['impl_status'], 'evidence': n['evidence'],
                   'verified_at': n['verified_at']}
        live = [dict(r) for r in self._q('SELECT src.id, src.ref, src.captured_at FROM note_sources ns JOIN sources src '
                                         'ON src.id = ns.source WHERE ns.note = ? AND src.verify_live = 1', n['path'])]
        truncated = len(content) > max_chars
        out.update({
            'ok': True, 'note_version': n['version'],
            'content': content[:max_chars] + (f'\n… [truncated at {max_chars} chars: do not replace from this text]' if truncated else ''),
            'truncated': truncated,
            'outline': [{'section_id': r['id'], 'heading_path': json.loads(r['heading_path']), 'level': r['level'],
                         'version': r['version'], **({'generated': r['generated']} if r['generated'] else {})} for r in rows],
        })
        if live:
            out['live_sources'] = live
            out['warning'] = ('this note copies live state; re-check the live system before acting on it '
                              f"(verified_at {n['verified_at'] or 'missing'})")
        return out

    def patch_section(self, op: str, content: str, if_version: int | None = None, section_id: int | None = None, path: str | None = None,
                      heading: list[str] | str | None = None, summary: str | None = None, task_id: str | None = None) -> dict:
        """Edit one section. op: 'replace' (content = what read returned for the section, subsections included,
        headings relative), 'append' (after the subtree; may add subsections), 'prepend' (before the own text; no
        headings) or 'replace_own_text' (only the text before the first subsection; subsections kept; no headings).
        All but append need if_version (the section version read returned). On a page whose only section is the
        root, append and replace on the root can add headings. Optional summary updates the note's index line (on
        <domain>/index: the domain description). All or nothing, with the checks inside."""
        if op not in PATCH_OPS:
            raise Refused('bad_op', op=op, ops=PATCH_OPS)
        if if_version is None and op != 'append':
            raise Refused('version_required', op=op, hint='give if_version: the section version that read returned')
        t0 = self._target(section_id, path, heading)
        domain = t0['note'].split('/')[0]
        self._need_write(t0['note'])

        def mutate():
            t = self._one('SELECT * FROM sections WHERE id = ?', t0['id'])
            rows = self._subtree(t)
            self._check_leases([r['id'] for r in rows + self._ancestors(t)])
            if op != 'append' and t['version'] != if_version:  # appending at the end does not depend on what is there
                raise Refused('version_conflict', section_id=t['id'], current_version=t['version'], content=self._subtree_text(t, rows))
            if any(r['generated'] for r in (rows[:1] if op == 'replace_own_text' else rows)):
                raise Refused('generated_section', section_id=t['id'],
                              hint='index catalog, status and log are rendered from rows: use create_note/summary, status_item, log_append')
            current = self._subtree_text(t, rows)
            if op == 'replace':
                new = content
            elif op == 'append':
                new = current.rstrip('\n') + '\n\n' + content.strip('\n') + '\n\n'
            else:
                if has_heading(content):
                    raise Refused('headings_not_allowed', hint=f'{op} changes only the own text; use append for subsections')
                if op == 'prepend':
                    new = '\n' + content.strip('\n') + '\n\n' + current.lstrip('\n')
                elif content.strip() == t['body'].strip():  # own text resent unchanged (e.g. to set only the summary): keep it
                    new = t['body']
                else:
                    new = '\n' + content.strip('\n') + '\n\n' if content.strip() else '\n'
            records = {r['id']: r for r in self._records(domain)} if t['note'] == f'{domain}/decisions' else None
            changed, diff = self._apply_subtree(t, rows[:1] if op == 'replace_own_text' else rows, new)
            if summary is not None:
                pos = None if self._is_working(t['note']) else self._one(
                    'SELECT coalesce(max(catalog_position), 0) + 1 FROM notes WHERE domain = ?', domain)[0]
                self.db.execute('UPDATE notes SET summary = ?, catalog_position = coalesce(catalog_position, ?) WHERE path = ?',
                                (summary, pos, t['note']))
            if changed or summary is not None:
                self._touch(t['note'])
            if records is not None:
                self._guard_records(records, domain)
                self._derive_decisions(domain)
            t = self._one('SELECT * FROM sections WHERE id = ?', t['id'])
            n = self._note(t['note'])
            done = [r['id'] for r in self._subtree(t)]
            self.db.execute(f'DELETE FROM leases WHERE run_id = ? AND section IN ({",".join("?" * len(done))})', (self.run_id, *done))
            return ({'section_id': t['id'], 'version': t['version'], 'note_version': n['version'], 'changed': bool(changed)},
                    {'note': t['note'], 'sections': changed},
                    json.dumps({'op': op, 'before': current, 'shape': _shape(content), 'sections': diff}, ensure_ascii=False))

        return self._guarded(domain, 'patch_section', task_id, mutate)

    def _apply_subtree(self, t, rows, text):
        """Replace a section's subtree with parsed text; existing subsections keep their ids. Returns the changed ids
        and {created, changed, removed} ids."""
        base = t['level'] or 0
        own, children, _ = parse_sections(text, base, json.loads(t['heading_path']))
        if t['level'] is None and children and len(self._sections(t['note'])) > 1:  # a root-only page may get its first headings
            raise Refused('headings_not_allowed', hint='the text before the first heading cannot contain headings')
        old = {r['heading_path']: r for r in rows[1:]}
        changed, order, created = [], [t['id']], []
        if own != t['body']:
            self.db.execute('UPDATE sections SET body = ?, version = version + 1 WHERE id = ?', (own, t['id']))
            changed.append(t['id'])
        for c in children:
            key = hp(c['path'])
            r = old.pop(key, None)
            if r:
                if (r['heading'], r['level'], r['body']) != (c['heading'], c['level'], c['body']):
                    self.db.execute('UPDATE sections SET heading = ?, level = ?, body = ?, version = version + 1 WHERE id = ?',
                                    (c['heading'], c['level'], c['body'], r['id']))
                    changed.append(r['id'])
                order.append(r['id'])
            else:
                sid = self.db.insert('sections', {'note': t['note'], 'heading': c['heading'], 'heading_path': key, 'level': c['level'],
                                                  'position': -1, 'body': c['body']})
                changed.append(sid)
                created.append(sid)
                order.append(sid)
        for r in old.values():
            self.db.execute('DELETE FROM sections WHERE id = ?', (r['id'],))
            changed.append(r['id'])
        if old or len(order) != len(rows) or [r['id'] for r in rows] != order:
            all_rows = self._sections(t['note'])
            before = [r['id'] for r in all_rows if 0 <= r['position'] < t['position']]
            after = [r['id'] for r in all_rows if r['position'] > rows[-1]['position']]
            for i, sid in enumerate(before + order + after):
                self.db.execute('UPDATE sections SET position = ? WHERE id = ?', (i, sid))
        if changed:
            if t['id'] not in changed:
                self.db.execute('UPDATE sections SET version = version + 1 WHERE id = ?', (t['id'],))
            for a in self._ancestors(self._one('SELECT * FROM sections WHERE id = ?', t['id'])):
                self.db.execute('UPDATE sections SET version = version + 1 WHERE id = ?', (a['id'],))
        live = [sid for sid in changed if self._one('SELECT 1 FROM sections WHERE id = ?', sid)]
        self._relink(live)
        self._resolve_dangling(t['note'])
        removed = [r['id'] for r in old.values()]
        return changed, {'created': created, 'changed': [i for i in changed if i not in created and i not in removed], 'removed': removed}

    def create_note(self, path: str, title: str, summary: str | None = None, body: str = '', decision_status: str | None = None,
                    impl_status: str | None = None, evidence: str | None = None, tags: list[str] | None = None,
                    task_id: str | None = None) -> dict:
        """Create a new note (never overwrites) in an existing domain. body is markdown (text before the first heading
        is allowed). A project note needs a summary (its index line) and the page header (decision_status, impl_status,
        evidence). A working note (<domain>/working/<actor>/…) needs neither and stays out of the index."""
        path = path.strip('/').removesuffix('.md')
        domain = path.split('/')[0]
        self._need_domain(domain)
        self._check_path(path)
        self._need_write(path)
        header = (decision_status, impl_status, evidence)
        if not self._is_working(path):
            if not (summary or '').strip():
                raise Refused('summary_required', hint="the summary is the note's line in the index")
            if not all((h or '').strip() for h in header):
                raise Refused('header_required', hint='a project note needs decision_status, impl_status and evidence')
        elif any(h is not None for h in header) and not all(header):
            raise Refused('incomplete_header', hint='give all three header fields or none')

        def mutate():
            if self._one('SELECT 1 FROM notes WHERE path = ?', path):
                raise Refused('exists', path=path, hint='create_note never overwrites; use patch_section')
            pos = None if self._is_working(path) else self._one('SELECT coalesce(max(catalog_position), 0) + 1 FROM notes WHERE domain = ?', domain)[0]
            d = evidence and DATE.search(evidence)
            self.db.execute('INSERT INTO notes (path, domain, title, status, meta, summary, catalog_position, decision_status, '
                            'impl_status, evidence, verified_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                            (path, domain, title, 'current', json.dumps({'_keys': ['title', 'status', 'updated', 'tags'], 'tags': tags or [domain]}),
                             summary, pos, decision_status, impl_status, evidence, d and d.group(), today()))
            own, secs, _ = parse_sections(body)
            ids = self._insert_sections(path, '\n' + own.strip('\n') + ('\n\n' if own.strip() else ''), secs)
            self._relink(ids)
            self._resolve_dangling(path)
            return ({'path': path, 'version': 1, 'sections': len(ids)}, {'note': path},
                    json.dumps({'note_created': path, 'shape': _shape(body), 'sections': len(ids)}))

        return self._guarded(domain, 'create_note', task_id, mutate)

    def _insert_sections(self, note, preamble, secs, generated=None):
        generated = generated or {}
        ids = [self.db.insert('sections', {'note': note, 'heading_path': '[]', 'position': 0, 'body': preamble})]
        for i, s in enumerate(secs, 1):
            g, body = generated.get(i - 1, (None, s['body']))
            ids.append(self.db.insert('sections', {'note': note, 'heading': s['heading'], 'heading_path': hp(s['path']), 'level': s['level'],
                                                   'position': i, 'body': body, 'generated': g}))
        return ids

    def patch_header(self, path: str, if_version: int, decision_status: str | None = None, impl_status: str | None = None,
                     evidence: str | None = None, verified_at: str | None = None, task_id: str | None = None) -> dict:
        """Edit a note's page header: decision status, implementation status, evidence checked, verified_at
        (YYYY-MM-DD). if_version is the note version (read without a heading returns it). Fields left out keep their
        value; new evidence sets verified_at from its first date unless verified_at is given."""
        fields = {'decision_status': decision_status, 'impl_status': impl_status, 'evidence': evidence}
        if all(v is None for v in [*fields.values(), verified_at]):
            raise Refused('nothing_to_change')
        if verified_at is not None and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', verified_at):
            raise Refused('bad_date', verified_at=verified_at, hint='YYYY-MM-DD')
        domain = self._note(path)['domain']
        self._need_write(path)

        def mutate():
            n = self._note(path)
            self._check_leases([r['id'] for r in self._q("SELECT id FROM sections WHERE note = ? AND heading_path = '[]'", path)])
            old = {k: n[k] for k in (*fields, 'verified_at')}
            if n['version'] != if_version:
                raise Refused('version_conflict', current_version=n['version'], header=old)
            new = {k: (v if v is not None else old[k]) for k, v in fields.items()}
            if any(v is None or not v.strip() for v in new.values()):
                raise Refused('incomplete_header', header=new, hint='a page header needs all three of decision_status, impl_status and evidence')
            d = evidence is not None and DATE.search(evidence)
            new['verified_at'] = verified_at or (d.group() if d else old['verified_at'])
            self.db.execute('UPDATE notes SET decision_status = ?, impl_status = ?, evidence = ?, verified_at = ? WHERE path = ?',
                            (*new.values(), path))
            self._touch(path)
            pre = self._one("SELECT id FROM sections WHERE note = ? AND heading_path = '[]'", path)
            self._relink([pre['id']])
            return ({'path': path, 'version': self._note(path)['version'], 'verified_at': new['verified_at']},
                    {'note': path}, json.dumps({'before': old, 'shape': {k: _shape(v) for k, v in fields.items() if v is not None}}, ensure_ascii=False))

        return self._guarded(domain, 'patch_header', task_id, mutate)

    def status_item(self, domain: str, action: str, kind: str | None = None, text: str | None = None,
                    refs: list[str] | None = None, item_id: int | None = None, task_id: str | None = None) -> dict:
        """Add one status item (action='add', kind: state|open|blocker|next, text), change the text (and optionally
        the kind) of an open one (action='update', item_id, text) or close one (action='close', item_id). The status
        page and brief render from these items; the event keeps the old text. Owner only."""
        self._need_domain(domain)
        self._need_owner('status_item')

        def mutate():
            if action == 'add':
                if kind not in STATUS_KINDS.values() or not (text or '').strip():
                    raise Refused('bad_item', kinds=list(STATUS_KINDS.values()), hint='add needs a kind and text')
                pos = self._one('SELECT coalesce(max(position), 0) + 1 FROM status_items WHERE domain = ? AND kind = ?', domain, kind)[0]
                iid = self.db.insert('status_items', {'domain': domain, 'kind': kind, 'text': text.strip(), 'refs': json.dumps(refs or []),
                                                      'position': pos, 'updated_at': today()})
                return {'item_id': iid}, {'item': iid}, json.dumps({'action': 'add', 'kind': kind, 'text': text}, ensure_ascii=False)
            if action == 'close':
                it = self._one('SELECT * FROM status_items WHERE id = ? AND domain = ?', item_id, domain)
                if not it or it['closed_at']:
                    raise Refused('not_open', item_id=item_id)
                self.db.execute('UPDATE status_items SET closed_at = ? WHERE id = ?', (today(), item_id))
                return {'item_id': item_id}, {'item': item_id}, json.dumps({'action': 'close', 'text': it['text']}, ensure_ascii=False)
            if action == 'update':
                it = self._one('SELECT * FROM status_items WHERE id = ? AND domain = ? AND closed_at IS NULL', item_id, domain)
                if not it:
                    raise Refused('not_open', item_id=item_id)
                new_kind = kind or it['kind']
                if new_kind not in STATUS_KINDS.values() or not (text or '').strip():
                    raise Refused('bad_item', kinds=list(STATUS_KINDS.values()), hint='update needs an open item_id and text; kind is optional')
                pos = it['position'] if new_kind == it['kind'] else self._one(
                    'SELECT coalesce(max(position), 0) + 1 FROM status_items WHERE domain = ? AND kind = ?', domain, new_kind)[0]
                self.db.execute('UPDATE status_items SET text = ?, kind = ?, position = ?, updated_at = ? WHERE id = ?',
                                (text.strip(), new_kind, pos, today(), item_id))
                return {'item_id': item_id}, {'item': item_id}, json.dumps(
                    {'action': 'update', 'kind': new_kind, 'text': text.strip(), 'old_kind': it['kind'], 'old_text': it['text']}, ensure_ascii=False)
            raise Refused('bad_action', actions=['add', 'update', 'close'])

        return self._guarded(domain, 'status_item', task_id, mutate)

    def set_decision_status(self, domain: str, id: str, status: str, superseded_by: str | None = None, reason: str | None = None,
                            task_id: str | None = None) -> dict:
        """Move a decision record through its life cycle by rewriting its one Status line: proposed -> accepted,
        rejected or superseded; accepted -> superseded (superseded_by must be an accepted decision) or void (an
        acceptance made in error; needs reason, which is added to the record as a Void reason line). Owner only."""
        self._need_domain(domain)
        self._need_owner('set_decision_status')

        def mutate():
            rec = next((r for r in self._records(domain) if r['id'] == id), None)
            if not rec or len(rec['status']) != 1:
                raise Refused('no_record', id=id, hint='the record needs exactly one Status line')
            cur = rec['status'][0][0].split()[0]
            if status not in TRANSITIONS.get(cur, set()):
                raise Refused('bad_transition', id=id, current=cur, allowed=sorted(TRANSITIONS.get(cur, set())))
            line = new = status
            if status == 'void':
                if not (reason or '').strip():
                    raise Refused('reason_required', hint='void needs reason: why the acceptance was an error')
                new = f"void\n- **Void reason:** {' '.join(reason.split())} ({today()})"
            if status == 'superseded':
                by = self._one("SELECT 1 FROM decisions WHERE domain = ? AND id = ? AND status = 'accepted'", domain, superseded_by)
                if not by or superseded_by == id:
                    raise Refused('superseded_by_required', hint='superseded_by must name another accepted decision')
                line = new = f'superseded by {superseded_by}'
            s = rec['section']
            self._check_leases([r['id'] for r in self._subtree(s) + self._ancestors(s)])
            body = STATUS_LINE.sub(lambda m: f'- **Status:** {new}', s['body'], count=1)
            self.db.execute('UPDATE sections SET body = ?, version = version + 1 WHERE id = ?', (body, s['id']))
            self._relink([s['id']])
            for a in self._ancestors(s):
                self.db.execute('UPDATE sections SET version = version + 1 WHERE id = ?', (a['id'],))
            self._touch(s['note'])
            self._derive_decisions(domain)
            return ({'id': id, 'status': status, 'superseded_by': superseded_by, 'section_id': s['id']},
                    {'decision': id, 'section': s['id']}, json.dumps({'from': rec['status'][0][0], 'to': line, 'reason': reason}, ensure_ascii=False))

        return self._guarded(domain, 'set_decision_status', task_id, mutate)

    def links(self, path: str, direction: str = 'both', depth: int = 1) -> dict:
        """Links of a note: 'out' (what it links to), 'in' (what links to it) or 'both', up to 3 hops. Each link has
        its source section, anchor, and whether it is broken or points outside the store."""
        self._note(path)
        if direction not in ('in', 'out', 'both'):
            raise Refused('bad_direction', directions=['in', 'out', 'both'])
        domains, out, seen, frontier = self._domains(), {}, {path}, {path}
        for d in range(1, max(1, min(depth, 3)) + 1):
            nxt = set()
            for p in frontier:
                for side, col in (('out', 's.note'), ('in', 'l.dst')):
                    if direction not in (side, 'both'):
                        continue
                    for r in self._q('SELECT l.*, s.note, s.heading, (SELECT 1 FROM notes WHERE path = l.dst) AS found FROM links l '
                                     f'JOIN sections s ON s.id = l.src_section WHERE {col} = ? ORDER BY s.note, s.position, l.dst, l.anchor', p):
                        external = not r['found'] and '/' in r['dst'] and r['dst'].split('/')[0] not in domains
                        broken = (not r['found'] and not external) or bool(r['found'] and r['anchor'] and r['dst_section'] is None)
                        out.setdefault((r['src_section'], r['dst'], r['anchor']), {
                            'hop': d, 'from': r['note'], 'from_section_id': r['src_section'], 'from_heading': r['heading'],
                            'to': r['dst'], 'anchor': r['anchor'], 'broken': broken, 'external': external})
                        other = r['dst'] if side == 'out' else r['note']
                        if r['found'] and other not in seen:
                            nxt.add(other)
            seen |= nxt
            frontier = nxt
        return {'ok': True, 'path': path, 'links': list(out.values())}

    def log_query(self, domain: str, since: str | None = None, until: str | None = None, op: str | None = None,
                  page: str | None = None, task_id: str | None = None, include_tool: bool = False, limit: int = 20) -> dict:
        """Log entries filtered by time (since/until: ISO date or date-time, local time if no offset; until a date
        includes that day), op substring, page and task id; the newest `limit` matches, oldest first. include_tool
        adds the automatic tool events (writes, searches)."""
        self._need_domain(domain)
        conds, args = ['domain = ?'], [domain]
        if not include_tool:
            conds.append("op NOT LIKE 'tool:%'")
        for value, cmp, end in ((since, '>=', False), (until, '<', True)):
            if value:
                conds.append(f'ts {cmp} ?')
                args.append(_utc(value, end))
        if op:
            conds.append("lower(op) LIKE lower(?) ESCAPE '!'")
            args.append('%' + _like(op) + '%')
        if task_id:
            conds.append('task_id = ?')
            args.append(task_id)
        sql = f'SELECT * FROM events WHERE {" AND ".join(conds)} ORDER BY id DESC'
        if page:  # ponytail: matches refs in Python over the filtered log; a refs table if logs grow very large
            want = self._resolve(page)
            rows = [e for e in self._q(sql, *args) if want in _strings(json.loads(e['refs'] or 'null'))][:limit]
        else:
            rows = self._q(sql + ' LIMIT ?', *args, limit)
        return {'ok': True, 'entries': [{'event_id': e['id'], 'ts': local(e['ts']), 'op': e['op'], 'actor': e['actor'],
                                         'task_id': e['task_id'], 'run_id': e['run_id'],
                                         'text': self._entry(e) if not e['op'].startswith('tool:') else e['body']}
                                        for e in reversed(rows)]}

    def move(self, path: str, new_path: str, summary: str | None = None, decision_status: str | None = None,
             impl_status: str | None = None, evidence: str | None = None, task_id: str | None = None) -> dict:
        """Rename a note within its domain; every link to it is rewritten in the same transaction (anchors kept).
        Moving a working note out of <domain>/working/ promotes it into project knowledge: owner only, and it needs
        a summary and, if it has none, the page header. Index, status, log and decisions pages do not move."""
        path, new_path = (p.strip('/').removesuffix('.md') for p in (path, new_path))
        n = self._note(path)
        domain = n['domain']
        fixed = {f'{domain}/{x}' for x in FIXED_PAGES}
        if new_path.split('/')[0] != domain or new_path.count('/') < 1 or path in fixed or new_path in fixed:
            raise Refused('bad_path', hint='a note moves within its domain; index, status, log and decisions are fixed')
        self._check_path(new_path)
        promote = self._is_working(path) and not self._is_working(new_path)
        if self._is_working(new_path) and not self._is_working(path):
            raise Refused('bad_path', hint='project knowledge does not move into a working namespace')
        if promote:
            self._need_owner('promote a working note')
        self._need_write(path)
        self._need_write(new_path)
        if promote:
            header = {k: v or n[k] for k, v in (('decision_status', decision_status), ('impl_status', impl_status), ('evidence', evidence))}
            if not (summary or '').strip() or not all(header.values()):
                raise Refused('promotion_needs_summary_and_header', hint='give summary and decision_status, impl_status, evidence')

        def mutate():
            if self._one('SELECT 1 FROM notes WHERE path = ?', new_path):
                raise Refused('exists', path=new_path)
            sids = [r[0] for r in self._q('SELECT DISTINCT src_section FROM links WHERE dst = ?', path)]
            linking = [self._one('SELECT * FROM sections WHERE id = ?', i) for i in sids]
            for s in linking:
                self._check_leases([r['id'] for r in self._subtree(s) + self._ancestors(s)])
            for s in linking:
                body = self._rewrite_links(s['body'], path, new_path)
                if body != s['body']:
                    self.db.execute('UPDATE sections SET body = ?, version = version + 1 WHERE id = ?', (body, s['id']))
                    for a in self._ancestors(s):
                        self.db.execute('UPDATE sections SET version = version + 1 WHERE id = ?', (a['id'],))
                if s['heading'] is None:
                    src = self._note(s['note'])
                    fields = [self._rewrite_links(src[k], path, new_path) if src[k] else src[k] for k in ('decision_status', 'impl_status', 'evidence')]
                    self.db.execute('UPDATE notes SET decision_status = ?, impl_status = ?, evidence = ? WHERE path = ?', (*fields, s['note']))
                self._touch(s['note'])
            self.db.execute('UPDATE notes SET path = ? WHERE path = ?', (new_path, path))
            self.db.execute('UPDATE sources SET id = ?, ref = ? WHERE id = ?', (new_path, new_path, path))
            if promote:
                pos = self._one('SELECT coalesce(max(catalog_position), 0) + 1 FROM notes WHERE domain = ?', domain)[0]
                d = DATE.search(header['evidence'])
                self.db.execute('UPDATE notes SET summary = ?, catalog_position = ?, decision_status = ?, impl_status = ?, evidence = ?, '
                                'verified_at = coalesce(?, verified_at) WHERE path = ?',
                                (summary, pos, *header.values(), d and d.group(), new_path))
            self._touch(new_path)
            moved = [r['id'] for r in self._sections(new_path)]
            self._relink(sorted(set(sids) | set(moved)))
            self._resolve_dangling(new_path)
            return ({'path': new_path, 'from': path, 'links_rewritten': len(sids), 'promoted': promote},
                    {'from': path, 'to': new_path}, None)

        return self._guarded(domain, 'move', task_id, mutate)

    def _rewrite_links(self, text, old, new):
        out, fence = [], False
        for line in text.splitlines(keepends=True):
            if FENCE.match(line):
                fence = not fence
            if not fence:
                code = [m.span() for m in re.finditer(r'`[^`]*`', line)]
                line = LINK.sub(lambda m: (m.group(0) if any(a <= m.start() < b for a, b in code) or self._resolve(m.group(1)) != old
                                           else m.group(0)[:m.start(1) - m.start()] + new + m.group(0)[m.end(1) - m.start():]), line)
            out.append(line)
        return ''.join(out)

    def preference(self, action: str, text: str | None = None, owner_quote: str | None = None, domain: str | None = None,
                   item_id: int | None = None, task_id: str | None = None) -> dict:
        """Owner preferences layer. action='add' records how the owner wants work done (text) with the owner's own
        words (owner_quote, required); domain limits it to one domain, empty means everywhere. action='retire' (owner
        only) ends one by item_id. Every brief shows the active ones."""
        if domain:
            self._need_domain(domain)

        def mutate():
            if action == 'add':
                self._need_owner('record a preference')
                if not (text or '').strip() or not (owner_quote or '').strip():
                    raise Refused('quote_required', hint="a preference needs its text and the owner's own words")
                iid = self.db.insert('preferences', {'domain': domain or None, 'text': text.strip(), 'owner_quote': owner_quote.strip(),
                                                     'recorded_by': self.actor, 'run_id': self.run_id})
                return {'item_id': iid}, {'preference': iid}, json.dumps({'action': 'add', 'text': text}, ensure_ascii=False)
            if action == 'retire':
                self._need_owner('retire a preference')
                r = self._one('SELECT * FROM preferences WHERE id = ? AND retired_at IS NULL', item_id)
                if not r:
                    raise Refused('not_active', item_id=item_id)
                self.db.execute(f'UPDATE preferences SET retired_at = {self.db.now()} WHERE id = ?', (item_id,))
                return {'item_id': item_id}, {'preference': item_id}, json.dumps({'action': 'retire', 'text': r['text']}, ensure_ascii=False)
            raise Refused('bad_action', actions=['add', 'retire'])

        return self._guarded(domain, 'preference', task_id, mutate)

    def source(self, action: str, id: str, kind: str | None = None, ref: str | None = None, captured_at: str | None = None,
               verify_live: bool = False, path: str | None = None, task_id: str | None = None) -> dict:
        """Sources behind knowledge. action='add' registers or updates one (kind: snapshot, live, transcript (ref = run
        id or file), web, owner; verify_live=True means re-check the live system before acting on a copy) and, with
        path, cites it from that note. 'cite' / 'uncite' link an existing source to a note. Citing a verify_live source
        is refused while the note's verified_at is stale: refresh it with patch_header first."""
        if path:
            self._note(path)
            self._need_write(path)

        def mutate():
            if action == 'add':
                if kind not in SOURCE_KINDS or not (ref or '').strip():
                    raise Refused('bad_source', kinds=list(SOURCE_KINDS), hint='add needs kind and ref')
                values = (kind, ref, captured_at or today(), int(verify_live), id)
                if self._one('SELECT 1 FROM sources WHERE id = ?', id):
                    self._need_owner('update an existing source')
                    self.db.execute('UPDATE sources SET kind = ?, ref = ?, captured_at = ?, verify_live = ? WHERE id = ?', values)
                else:
                    self.db.execute('INSERT INTO sources (kind, ref, captured_at, verify_live, id) VALUES (?, ?, ?, ?, ?)', values)
            elif action not in ('cite', 'uncite'):
                raise Refused('bad_action', actions=['add', 'cite', 'uncite'])
            elif not self._one('SELECT 1 FROM sources WHERE id = ?', id) or not path:
                raise Refused('not_found', source=id, hint='cite and uncite need an existing source and a path')
            if path and action in ('add', 'cite') and not self._one('SELECT 1 FROM note_sources WHERE note = ? AND source = ?', path, id):
                self.db.execute('INSERT INTO note_sources VALUES (?, ?)', (path, id))
            if action == 'uncite':
                self.db.execute('DELETE FROM note_sources WHERE note = ? AND source = ?', (path, id))
            return {'source': id, 'path': path}, {'source': id, 'note': path}, json.dumps({'action': action}, ensure_ascii=False)

        return self._guarded(path.split('/')[0] if path else None, 'source', task_id, mutate)

    def lease(self, action: str, section_id: int | None = None, path: str | None = None, heading: list[str] | str | None = None,
              minutes: int = 15, task_id: str | None = None) -> dict:
        """Claim a section (and its subsections) while drafting a long edit, for `minutes` (1–60, default 15).
        Other agents' writes to it, its subsections or its ancestors are refused until it expires or is released;
        your own patch_section releases it. action: 'take' or 'release'."""
        t = self._target(section_id, path, heading)
        self._need_write(t['note'])

        def mutate():
            if action == 'take':
                self._check_leases([r['id'] for r in self._subtree(t) + self._ancestors(t)])
                m = max(1, min(int(minutes), 60))
                self.db.execute('DELETE FROM leases WHERE section = ?', (t['id'],))  # an expired lease or our own
                self.db.execute(f'INSERT INTO leases VALUES (?, ?, ?, {self.db.now(m)})', (t['id'], self.actor, self.run_id))
            elif action == 'release':
                if not self.db.execute('DELETE FROM leases WHERE section = ? AND run_id = ?', (t['id'], self.run_id)):
                    raise Refused('not_held', section_id=t['id'])
            else:
                raise Refused('bad_action', actions=['take', 'release'])
            r = self._one('SELECT expires_at FROM leases WHERE section = ?', t['id'])
            return {'section_id': t['id'], 'action': action, 'expires_at': r and local(r[0])}, {'section': t['id']}, None

        return self._guarded(t['note'].split('/')[0], 'lease', task_id, mutate)

    def create_domain(self, domain: str, title: str, description: str, task_id: str | None = None) -> dict:
        """Create a new domain with its start pages: index (with the catalog), status, log and decisions. description
        is the domain's line in list_domains: what it holds and when to choose it (e.g. "not for X; use Y"); it is
        kept as the index summary. domain: lowercase letters, digits and hyphens. Owner only."""
        self._need_owner('create_domain')
        if not ACTOR.match(domain or ''):
            raise Refused('bad_name', domain=domain, hint='lowercase letters, digits and hyphens, e.g. my-product')
        if not (description or '').strip():
            raise Refused('description_required', hint='say what the domain holds and when to choose it')
        title = (title or '').strip() or domain
        CATALOG = {'decisions': 1, 'status': 2, 'log': 3}  # catalog order of the start pages
        pages = {  # path: (title, summary, [(heading, level, generated, body)])
            'index': (f'{title} index', description.strip(), [(f'{title} index', 1, None, '\n'),
                                                              ('Catalog', 2, 'catalog', '\n| Page | Purpose |\n|---|---|\n')]),
            'status': (f'{title} status', 'Current state', [(f'{title} status', 1, None, '\n')] + [
                (h, 2, f'status:{k}', '') for h, k in STATUS_KINDS.items()]),
            'log': (f'{title} log', 'Append-only activity', [(f'{title} log', 1, 'log', '\n')]),
            'decisions': (f'{title} decisions', 'Decision records', [(f'{title} decisions', 1, None, '\n')]),
        }

        def mutate():
            if domain in self._domains():
                raise Refused('domain_exists', domain=domain)
            for name, (page_title, summary, secs) in pages.items():
                path = f'{domain}/{name}'
                self.db.execute('INSERT INTO notes (path, domain, title, status, meta, summary, catalog_position, updated_at) '
                                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (path, domain, page_title, 'current', json.dumps(
                                    {'_keys': ['title', 'status', 'updated', 'tags'], 'tags': [domain]}), summary, CATALOG.get(name), today()))
                stack = []
                parsed = []
                for heading, level, _, body in secs:
                    stack = [h for h in stack if h[0] < level] + [(level, heading)]
                    parsed.append({'heading': heading, 'level': level, 'path': [h for _, h in stack], 'body': body})
                self._insert_sections(path, '', parsed, {i: (g, s[3]) for i, s in enumerate(secs) if (g := s[2])})
            paths = [f'{domain}/{p}' for p in pages]
            return {'domain': domain, 'pages': paths}, {'domain': domain, 'pages': paths}, json.dumps({'title': title}, ensure_ascii=False)

        return self._guarded(domain, 'create_domain', task_id, mutate)

    def list_domains(self) -> dict:
        """Every domain: its description (what it holds and when to choose it), note count, last log activity, and
        ops: true for the domain that records the memory system's own failures. Choose domains by description, then
        call brief. description_missing: true means the index has no summary; the first paragraph of the index stands
        in for it. preferences: the owner's active preferences for every domain (e.g. "No knowledge base for <topic>":
        do not ask again to create one)."""
        out = []
        for d in sorted(self._domains()):
            n = self._one('SELECT summary FROM notes WHERE path = ?', f'{d}/index')
            row = {'domain': d, 'description': (n and n['summary'] or '').strip()}
            if not row['description']:
                text = self.render_note(f'{d}/index', frontmatter=False) if n else ''
                para = next((p for p in re.split(r'\n[ \t]*\n', text) if p.strip() and not re.match(r'\s*(#|\||> \*\*Decision status)', p)), '')
                para = ' '.join(para.split())
                row.update(description=para if len(para) <= 400 else para[:400].rsplit(' ', 1)[0] + ' …', description_missing=True)
            last = self._one("SELECT max(ts) FROM events WHERE domain = ? AND op NOT LIKE 'tool:%'", d)[0]
            row.update(notes=self._one('SELECT count(*) FROM notes WHERE domain = ?', d)[0], last_log=last and local(last),
                       ops=d == self.ops_domain)
            out.append(row)
        prefs = [{'id': r['id'], 'text': r['text']} for r in self._q('SELECT id, text FROM preferences WHERE retired_at IS NULL AND domain IS NULL ORDER BY id')]
        return {'ok': True, 'domains': out, 'preferences': prefs}

    def batch(self, ops: list[dict], task_id: str | None = None) -> dict:
        """Several writes in one transaction, all or nothing: ops = [{"tool": name, "args": {...}}, ...] with tools
        create_note, patch_section, patch_header, status_item, set_decision_status, log_append and source. Each op
        has its own role rules and refusals. The checks (and the pages that log_append names) run once, against the
        final state, so notes that link to each other can be created together. A refusal rolls back every op and
        gives op_index. Returns the result of each op."""
        if not isinstance(ops, list) or not ops:
            raise Refused('bad_batch', hint='ops is a non-empty list of {"tool": name, "args": {...}}', tools=list(BATCH_TOOLS))
        for i, o in enumerate(ops):
            if not isinstance(o, dict) or o.get('tool') not in BATCH_TOOLS or not isinstance(o.get('args', {}), dict):
                raise Refused('bad_batch', op_index=i, tools=list(BATCH_TOOLS))
        # log entries go last: the pages they name are checked against the final state
        order = [i for i, o in enumerate(ops) if o['tool'] != 'log_append'] + [i for i, o in enumerate(ops) if o['tool'] == 'log_append']
        results = [None] * len(ops)
        with self._write():
            # ponytail: checks every domain before and after; scope to the domains the ops touch if stores grow large
            before = self._check_state(sorted(self._domains()))
            self._batch = True
            try:
                for i in order:
                    fn, args = _RAW[ops[i]['tool']], {'task_id': task_id, **ops[i].get('args', {})}
                    try:
                        inspect.signature(fn).bind(self, **args)
                    except TypeError as e:
                        raise Refused('bad_args', op_index=i, tool=ops[i]['tool'], detail=str(e))
                    try:
                        results[i] = fn(self, **args)
                    except Refused as e:
                        raise Refused(e.info['error'], op_index=i, tool=ops[i]['tool'],
                                      **{k: v for k, v in e.info.items() if k not in ('ok', 'error')}) from None
            finally:
                self._batch = False
            self._refuse_new(before, sorted(self._domains()))
        return {'ok': True, 'results': results}

    def log_append(self, domain: str, op: str, pages: list[str], source: str, outcome: str, task_id: str | None = None) -> dict:
        """Append a narrative log entry. The database stamps the time; pages must exist in the store."""
        self._need_domain(domain)
        op = op.strip()
        if not op or op.startswith('tool:'):
            raise Refused('bad_op', hint="op names the activity, e.g. 'ACCEPT DECISION D01'; 'tool:' is reserved")
        resolved = [self._resolve(p) for p in pages]
        missing = [p for p, r in zip(pages, resolved) if not self._one('SELECT 1 FROM notes WHERE path = ?', r)]
        if missing:
            raise Refused('unknown_pages', pages=missing)
        body = (f"\n- **Pages:** {', '.join(f'[[{p}]]' for p in resolved)}\n- **Source / reason:** {source.strip()}\n"
                f"- **Outcome:** {outcome.strip()}\n\n")
        with self._write():
            eid = self._event(domain, op, resolved, body, task_id)
        return {'ok': True, 'event_id': eid, 'ts': local(self._one('SELECT ts FROM events WHERE id = ?', eid)[0])}


def _utc(value, end=False):
    """ISO date or date-time (local if no offset) to the events.ts format; a date as an end bound means the next day."""
    if len(value) == 10:
        d = datetime.datetime.combine(datetime.date.fromisoformat(value) + datetime.timedelta(days=1 if end else 0), datetime.time())
    else:
        d = datetime.datetime.fromisoformat(value)
    return d.astimezone(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def _cap(text, limit):
    return text if len(text) <= limit else text[:limit] + f'\n… [truncated at {limit} of {len(text)} chars; read by section]'


def _bare(heading):
    """A heading compared loosely: lower case, without a trailing '(YYYY-MM-DD)' or ' [n]' suffix."""
    return re.sub(r'\s*(\(\d{4}-\d{2}-\d{2}\)|\[\d+\])\s*$', '', heading or '').strip().lower()


def _closest(word, pool, n=5, cutoff=0.6):
    """The pool entries closest to word: those that contain it or that it contains first, then difflib's close matches."""
    near = [p for p in pool if word and (word in p or p in word)]
    near.sort(key=lambda p: -difflib.SequenceMatcher(None, word, p).ratio())
    return list(dict.fromkeys(near + difflib.get_close_matches(word, pool, n, cutoff)))[:n]


def _like(text):
    """text as a literal LIKE pattern (ESCAPE '!')."""
    return re.sub(r'([!%_])', r'!\1', text)


def _strings(value):
    """Every string in a JSON value, at any depth."""
    if isinstance(value, str):
        return [value]
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    return [s for v in items for s in _strings(v)]


def _shape(text):
    """What a write carried, without its text: characters, lines and headings."""
    text = text or ''
    return {'chars': len(text), 'lines': len(text.splitlines()), 'headings': len(parse_sections(text)[1])}


def _words(text):
    """Lower-case words without diacritics, split like the search index (letters and digits)."""
    folded = ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))
    return re.findall(r'[^\W_]+', folded)


def _coordinate(query, hits, texts):
    """bm25 hits ranked by score x (share of the query terms a hit holds) ** COORD_POWER. The relevance floor: a hit
    must hold half the terms and at least two (all of a shorter query); when no hit does, every hit stays."""
    terms = set(_words(query))
    if not terms:
        return sorted(hits, key=lambda h: h['score'])
    need = min(len(terms), max(2, -(-len(terms) // 2)))
    held = [len(terms & set(_words(x))) for x in texts]
    for h, n in zip(hits, held):
        h['score'] = h['score'] * (n / len(terms)) ** COORD_POWER
    # ponytail: a strict floor (no fallback) lost top-3 hits in the real-search eval; it keeps every hit when none passes
    return sorted([h for h, n in zip(hits, held) if n >= need] or hits, key=lambda h: h['score'])


def _snip(text, m, width=80):
    """About width characters around a match, starting and ending on word boundaries."""
    if not m:
        return ''
    a, b = max(0, m.start() - width), min(len(text), m.end() + width)
    if a and not text[a - 1].isspace():
        s = re.search(r'\s', text[a:m.start()])
        a = a + s.end() if s else m.start()
    if b < len(text) and not text[b].isspace():
        s = [x.start() for x in re.finditer(r'\s', text[m.end():b])]
        b = m.end() + s[-1] if s else m.end()
    return ('…' if a else '') + text[a:m.start()] + '[' + text[m.start():m.end()] + ']' + text[m.end():b] + ('…' if b < len(text) else '')


TOOLS = ['brief', 'search', 'read', 'links', 'log_query', 'patch_section', 'patch_header', 'create_note', 'move', 'status_item',
         'set_decision_status', 'log_append', 'preference', 'source', 'lease', 'check', 'batch', 'create_domain', 'list_domains']
_RAW = {name: getattr(Store, name) for name in BATCH_TOOLS}  # unwrapped: inside batch, only the batch records a refusal


def _locked(fn, capture):
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def run(self, *args, **kwargs):
        with self._lock:
            try:
                return fn(self, *args, **kwargs)
            except Refused as e:
                if capture:
                    self._record_failure(fn.__name__, 'tool:refused', e.info, _bound(sig, self, args, kwargs))
                raise
            except Exception as e:
                if capture:
                    self._record_failure(fn.__name__, 'tool:error', {'error': type(e).__name__, 'detail': str(e)[:500]},
                                         _bound(sig, self, args, kwargs))
                raise
    return run


def _bound(sig, self, args, kwargs):
    try:
        return dict(sig.bind(self, *args, **kwargs).arguments)
    except TypeError:
        return dict(kwargs)


for _name in TOOLS:
    setattr(Store, _name, _locked(getattr(Store, _name), capture=True))
Store.render_note = _locked(Store.render_note, capture=False)

