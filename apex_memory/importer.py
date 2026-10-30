"""Import one Obsidian domain folder into the store, and the one-time migration check (D07).

The importer only reads the source files. The index catalog, status page and log become rows
(notes.summary, status_items, events) and are rendered back from them.
"""
import datetime, difflib, json, pathlib, re

from .store import (CATALOG_ROW, DATE, HEADER, STATUS_KINDS, Refused, links_in, parse_frontmatter,
                    parse_sections, split_frontmatter, status_items_in)

ENTRY = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}) — (.*)$')
LOOSE_ROW = re.compile(r'^\|\s*\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]\s*\|\s*(.*?)\s*\|\s*$')  # a padded or aliased catalog row


def note_path(src, f):
    return f'{src.name}/' + f.relative_to(src).with_suffix('').as_posix()


def _kind(heading):
    return next((k for h, k in STATUS_KINDS.items() if heading.startswith(h)), None)


def import_domain(store, src):
    src = pathlib.Path(src).expanduser().resolve()
    domain = src.name
    rep = {'domain': domain, 'kept_as_written': [], 'duplicate_headings': [], 'headers_kept_as_text': [], 'catalog_unknown': [],
           'unclassified_decisions': [], 'not_imported': [str(f.relative_to(src)) for f in sorted(src.rglob('*'))
                                                          if f.is_file() and f.suffix != '.md']}
    with store._write():
        if domain in store._domains():
            raise Refused('domain_exists', domain=domain, hint='a domain is imported once, into a fresh database')
        catalog = []
        for f in sorted(src.rglob('*.md')):
            path = note_path(src, f)
            fm, rest = split_frontmatter(f.read_text())
            data = parse_frontmatter(fm) if fm is not None else None
            own, secs, dups = parse_sections(rest)
            rep['duplicate_headings'] += [f"{path}: {' / '.join(d)}" for d in dups]

            lines = own.splitlines(keepends=True)
            first = next((i for i, l in enumerate(lines) if l.strip()), None)
            header = first is not None and HEADER.match(lines[first].rstrip('\n'))
            if header:
                own = ''.join(lines[first + 1:])
            elif '**Decision status:**' in own:
                rep['headers_kept_as_text'].append(path)

            generated = {}
            if path == f'{domain}/index':
                for i, s in enumerate(secs):
                    body = s['body'].splitlines(keepends=True)
                    rows = [j for j, l in enumerate(body) if CATALOG_ROW.match(l.rstrip('\n'))]
                    if rows and rows == list(range(rows[0], rows[-1] + 1)) and not any(l.strip() for l in body[rows[-1] + 1:]):
                        catalog += [CATALOG_ROW.match(body[j].rstrip('\n')).groups() for j in rows]
                        generated[i] = ('catalog', ''.join(body[:rows[0]]))
                    else:  # a catalog that is not the plain table: the page stays as written, its rows still give summaries
                        catalog += [m.groups() for l in body if (m := LOOSE_ROW.match(l.rstrip('\n')))]
                if not generated:
                    rep['kept_as_written'].append(f'{path}: no plain catalog table; summaries taken from its table rows')
            if path == f'{domain}/status':
                for i, s in enumerate(secs):
                    kind = _kind(s['heading'])
                    if kind:
                        for pos, text in enumerate(status_items_in(s['body']), 1):
                            store.db.execute('INSERT INTO status_items (domain, kind, text, refs, position, updated_at) '
                                             'VALUES (?, ?, ?, ?, ?, ?)', (domain, kind, text, '[]', pos, data and data.get('updated')))
                        generated[i] = (f'status:{kind}', '')
                if not generated:
                    rep['kept_as_written'].append(f'{path}: no standard status sections; stays prose')
            if path == f'{domain}/log':
                first = len(secs)  # the trailing run of standard entries becomes events; anything before stays as written
                while first > 1 and secs[first - 1]['level'] == 2 and ENTRY.match(secs[first - 1]['heading']):
                    first -= 1
                if first > 1:
                    rep['kept_as_written'].append(f'{path}: {first - 1} older sections kept as written before {len(secs) - first} entries')
                for i in range(first, len(secs)):
                    stamp, op = ENTRY.match(secs[i]['heading']).groups()
                    ts = datetime.datetime.fromisoformat(stamp).astimezone(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%S.000Z')
                    store._event(domain, op, [t for t, _ in links_in(secs[i]['body'])], secs[i]['body'], ts=ts, actor='import')
                if first:  # entries render after the last section kept, so new log_append entries show on the page too
                    generated[first - 1] = ('log', secs[first - 1]['body'])
                    secs = secs[:first]

            meta = {'_keys': list(data)} | {k: v for k, v in data.items() if k not in ('title', 'status', 'updated')} if data else None
            hdr = header.groups() if header else (None, None, None)
            d = hdr[2] and DATE.search(hdr[2])
            store.db.execute('INSERT INTO notes (path, domain, title, status, meta, decision_status, impl_status, evidence, '
                             'verified_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                             (path, domain, data and data.get('title'), data and data.get('status'),
                              json.dumps(meta, ensure_ascii=False) if meta else None, *hdr, d and d.group(), data and data.get('updated')))
            store._insert_sections(path, own, secs, generated)

        for pos, (target, summary) in enumerate(catalog, 1):
            p = store._resolve(target)
            if store._one('SELECT 1 FROM notes WHERE path = ?', p):
                store.db.execute('UPDATE notes SET summary = ?, catalog_position = ? WHERE path = ?', (summary, pos, p))
            else:
                rep['catalog_unknown'].append(target)
        store._relink([r['id'] for r in store._q('SELECT s.id FROM sections s JOIN notes n ON n.path = s.note WHERE n.domain = ?', domain)])
        for r in store._q('SELECT path FROM notes WHERE domain = ?', domain):  # links from other domains into this one
            store._resolve_dangling(r['path'])
        for r in store._q('SELECT path FROM notes WHERE path LIKE ?', f'{domain}/sources/%'):
            d = DATE.search(r['path'])
            store.db.execute("INSERT INTO sources (id, kind, ref, captured_at) VALUES (?, 'snapshot', ?, ?)", (r['path'], r['path'], d and d.group()))
        store.db.execute('INSERT OR IGNORE INTO note_sources SELECT DISTINCT s.note, l.dst FROM links l JOIN sections s ON s.id = l.src_section '
                         'JOIN sources src ON src.id = l.dst WHERE s.note != l.dst')
        store._derive_decisions(domain)
        rep['unclassified_decisions'] = [r['id'] for r in store._records(domain) if len(r['status']) != 1]
        for table, sql in [('notes', 'SELECT count(*) FROM notes WHERE domain = ?'),
                           ('sections', 'SELECT count(*) FROM sections s JOIN notes n ON n.path = s.note WHERE n.domain = ?'),
                           ('links', 'SELECT count(*) FROM links l JOIN sections s ON s.id = l.src_section JOIN notes n ON n.path = s.note WHERE n.domain = ?'),
                           ('log_entries', 'SELECT count(*) FROM events WHERE domain = ?'),
                           ('status_items', 'SELECT count(*) FROM status_items WHERE domain = ?'),
                           ('sources', "SELECT count(*) FROM sources WHERE id LIKE ? || '/%'")]:
            rep[table] = store._one(sql, domain)[0]
        rep['decisions'] = {r['status']: r['n'] for r in store._q('SELECT status, count(*) AS n FROM decisions WHERE domain = ? GROUP BY status', domain)}
        rep['problems'] = store.problems(domain)
        store._event(domain, 'tool:import', None, json.dumps(rep, ensure_ascii=False), actor='import')
    return rep


def _norm(text):
    out = []
    for line in (l.rstrip() for l in text.splitlines()):
        if line or (out and out[-1]):
            out.append(line)
    return '\n'.join(out).strip('\n')


def verify_domain(store, src):
    """Render every page back from SQLite and compare it with its source file. Returns the mismatches."""
    src = pathlib.Path(src).expanduser().resolve()
    domain, bad = src.name, []
    for f in sorted(src.rglob('*.md')):
        path, source = note_path(src, f), f.read_text()
        try:
            rendered = store.render_note(path)
        except Refused:
            bad.append(f'{path}: not in the store')
            continue
        if path == f'{domain}/status':
            _, secs, _ = parse_sections(split_frontmatter(source)[1])
            for s in secs:
                kind = _kind(s['heading'])
                if kind:
                    want = status_items_in(s['body'])
                    got = [r['text'] for r in store._q('SELECT text FROM status_items WHERE domain = ? AND kind = ? AND closed_at IS NULL '
                                                       'ORDER BY position, id', domain, kind)]
                    if want != got:
                        bad.append(f'{path}: status items for {kind} differ: {want!r} != {got!r}')
            want = [s['heading'] for s in secs]
            got = [s['heading'] for s in parse_sections(split_frontmatter(rendered)[1])[1]]
            if want != got:
                bad.append(f'{path}: headings differ: {want} != {got}')
            continue
        if _norm(rendered) != _norm(source):
            diff = difflib.unified_diff(_norm(source).splitlines(), _norm(rendered).splitlines(), 'source', 'store', lineterm='', n=1)
            bad.append(f'{path}:\n' + '\n'.join(list(diff)[:40]))
    n_src = len([1 for f in [src / 'decisions.md'] if f.exists() for s in parse_sections(f.read_text())[1] if re.match(r'^[DP]\d+ — ', s['heading'])])
    if n_src != len(store._records(domain)):
        bad.append(f'decision records: {n_src} in the source, {len(store._records(domain))} in the store')
    return bad
