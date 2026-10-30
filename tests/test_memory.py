"""Memory store tests. Run: uv run python -m unittest -v

Covers the plan's test list (KB office-ai/memory/implementation-plan#Tests), including one test per staleness guard.
"""
import asyncio, datetime, json, pathlib, sqlite3, sys, tempfile, textwrap, threading, unittest

from apex_memory.importer import import_domain, verify_domain
from apex_memory.store import Refused, Store, TOOLS, backup, local

REAL_KB = pathlib.Path('~/dev/kb/office-ai').expanduser()

FIXTURE = {
    'index.md': """\
        ---
        title: Demo index
        status: current
        updated: 2026-10-01
        tags:
          - demo
        ---

        # Demo knowledge index

        Primary knowledge base for the demo.

        ## Catalog and ownership

        | Page | Purpose and authority |
        |---|---|
        | [[demo/topic]] | **Owner:** the topic, with its revert detector rules |
        | [[demo/decisions]] | **Owner:** accepted decisions and proposals |
        | [[demo/sub/deep]] | A note in a subfolder |
        | [[demo/sources/2026-10-01-snapshot]] | Source snapshot |
        | [[demo/status]] | Current state |
        | [[demo/log]] | Append-only activity |
        """,
    'status.md': """\
        ---
        title: Demo status
        status: current
        updated: 2026-10-01
        tags:
          - demo
        ---

        # Demo — status

        ## Current state (2026-10-01)

        - **Done:** the demo exists.
        - **Implemented:** nothing.

        ## Unfinished / open

        - Write the tests.

        ## Blockers

        None.

        ## Next concrete action

        Build the thing.
        """,
    'log.md': """\
        ---
        title: Demo activity log
        status: current
        tags:
          - demo
        ---

        # Demo activity log

        ## 2026-10-01T10:00:00+05:30 — CREATE DEMO

        - **Pages:** [[demo/index]], [[demo/topic]]
        - **Source / reason:** test fixture
        - **Outcome:** created

        ## 2026-10-01T11:00:00+05:30 — ACCEPT D01

        - **Pages:** [[demo/decisions]]
        - **Source / reason:** owner said so
        - **Outcome:** recorded
        """,
    'decisions.md': """\
        ---
        title: Demo decisions
        status: current
        updated: 2026-10-01
        tags:
          - demo
        ---

        > **Decision status:** register. **Implementation status:** none. **Evidence checked:** 2026-10-01 — fixture.

        # Decisions

        ## Accepted

        ### D01 — Use SQLite (2026-10-01)
        - **Status:** accepted
        - **Decision:** SQLite is the store.

        ### D02 — No export (2026-10-02)
        - **Status:** accepted
        - **Decision:** no markdown export.

        ## Proposals (not yet accepted)

        ### P01 — Vectors later
        - **Status:** proposed

        Defer embeddings.

        ### P02 — Unclassified record
        No status line here.
        """,
    'topic.md': """\
        ---
        title: Demo topic
        status: current
        updated: 2026-10-01
        tags:
          - demo
        ---

        > **Decision status:** accepted (see [[demo/decisions#D01 — Use SQLite (2026-10-01)|D01]]). **Implementation status:** not built. **Evidence checked:** 2026-10-01 — fixture.

        # Topic

        Intro text with flow id 9d84d6c8-a845-4f00 and a link to [[demo/sub/deep]].

        ## Rules

        The revert detector flags changes that undo recent work.

        ### Details

        Detailed rules live here.

        ## Notes

        First notes section.

        ## Notes

        Second notes section with the same heading.
        """,
    'sub/deep.md': """\
        ---
        title: Deep note
        status: current
        updated: 2026-10-01
        tags:
          - demo
        ---

        # Deep note

        See [[demo/topic#Details]] and the snapshot [[demo/sources/2026-10-01-snapshot]].
        """,
    'sources/2026-10-01-snapshot.md': """\
        ---
        title: Snapshot
        status: current
        tags:
          - demo
        ---

        # Snapshot

        Immutable source text.
        """,
}


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.src = self.dir / 'demo'
        for name, text in FIXTURE.items():
            f = self.src / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(textwrap.dedent(text))
        self.db = str(self.dir / 'test.db')
        self.s = Store(self.db, 'test')
        self.report = import_domain(self.s, self.src)

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def refused(self, error, fn, *args, **kw):
        with self.assertRaises(Refused) as cm:
            fn(*args, **kw)
        self.assertEqual(cm.exception.info['error'], error, cm.exception.info)
        return cm.exception.info

    def events(self):
        return self.s._one('SELECT count(*) FROM events')[0]

    # ----- import and migration check -----

    def test_round_trip_and_import_report(self):
        self.assertEqual(verify_domain(self.s, self.src), [])
        self.assertEqual(self.report['duplicate_headings'], ['demo/topic: Topic / Notes [2]'])
        self.assertEqual(self.report['unclassified_decisions'], ['P02'])
        self.assertEqual(self.report['decisions'], {'accepted': 2, 'proposed': 1})
        self.assertEqual(self.report['log_entries'], 2)
        self.assertEqual(self.report['status_items'], 4)
        self.assertEqual(self.s._one("SELECT verified_at FROM notes WHERE path='demo/topic'")[0], '2026-10-01')
        self.assertEqual(self.s._q('SELECT note, source FROM note_sources')[0][:], ('demo/sub/deep', 'demo/sources/2026-10-01-snapshot'))
        self.refused('domain_exists', import_domain, self.s, self.src)

    @unittest.skipUnless(REAL_KB.is_dir(), 'the real Office AI KB is not on this machine')
    def test_real_office_ai_kb(self):
        s = Store(str(self.dir / 'real.db'), 'test')
        rep = import_domain(s, REAL_KB)
        self.assertEqual(verify_domain(s, REAL_KB), [])
        self.assertEqual(rep['unclassified_decisions'], [])
        self.assertEqual(rep['problems'], [])
        b = s.brief('office-ai')['brief']
        self.assertIn(s.store_id, b)
        last = s._q("SELECT * FROM events WHERE domain = 'office-ai' AND op NOT LIKE 'tool:%' ORDER BY id DESC LIMIT 10")
        self.assertTrue(all(s._entry(e).strip() in b for e in last))  # the last 10 entries, in full

    # ----- read / patch_section -----

    def test_read_replace_symmetry(self):
        r = self.s.read('demo/topic', heading='Rules')
        self.assertEqual(r['content'], '\nThe revert detector flags changes that undo recent work.\n\n# Details\n\nDetailed rules live here.\n\n')
        ids = [o['section_id'] for o in r['outline']]
        out = self.s.patch_section('replace', r['content'], r['version'], section_id=r['section_id'])
        self.assertFalse(out['changed'])
        r2 = self.s.read('demo/topic', heading='Rules')
        self.assertEqual(([o['section_id'] for o in r2['outline']], r2['version']), (ids, r['version']))
        # an edited subsection keeps its id; the parent's version moves so a stale parent replace is caught
        new = r['content'].replace('Detailed rules', 'Detailed, edited rules')
        out = self.s.patch_section('replace', new, r['version'], section_id=r['section_id'])
        r3 = self.s.read('demo/topic', heading='Rules')
        self.assertEqual([o['section_id'] for o in r3['outline']], ids)
        self.assertEqual(r3['content'], new)
        self.assertGreater(r3['version'], r['version'])

    def test_append_adds_subsection_and_relative_levels(self):
        r = self.s.read('demo/topic', heading='Rules')
        self.s.patch_section('append', '# More\n\nMore rules.', r['version'], section_id=r['section_id'])
        self.assertIn('### More\n\nMore rules.', self.s.render_note('demo/topic'))
        self.refused('headings_not_allowed', self.s.patch_section, 'prepend', '# X', self.s.read('demo/topic', heading='Rules')['version'],
                     path='demo/topic', heading='Rules')

    def test_atomic_rollback_on_new_problem(self):
        r = self.s.read('demo/topic', heading='Rules')
        n_events, links = self.events(), self.s._q('SELECT * FROM links')
        info = self.refused('check_failed', self.s.patch_section, 'append', 'See [[demo/nowhere]].', r['version'], section_id=r['section_id'])
        self.assertIn('broken link [[demo/nowhere]]', info['problems'][0])
        # removing a subsection that another note links to is also refused
        self.refused('check_failed', self.s.patch_section, 'replace', '\nOnly the parent text.\n\n', r['version'], section_id=r['section_id'])
        after = self.s.read('demo/topic', heading='Rules')
        self.assertEqual((after['content'], after['version']), (r['content'], r['version']))
        self.assertEqual((self.events(), [tuple(x) for x in self.s._q('SELECT * FROM links')]), (n_events, [tuple(x) for x in links]))

    def test_version_conflict_and_different_sections(self):
        other = Store(self.db, 'other')
        a, b = self.s.read('demo/topic', heading='Rules'), self.s.read('demo/topic', heading=['Topic', 'Notes'])
        other.patch_section('append', 'Edit from another agent.', b['version'], section_id=b['section_id'])
        out = self.s.patch_section('append', 'Edit to a different section.', a['version'], section_id=a['section_id'])
        self.assertTrue(out['ok'])
        info = self.refused('version_conflict', self.s.patch_section, 'append', 'stale', b['version'], section_id=b['section_id'])
        self.assertEqual(info['current_version'], b['version'] + 1)
        self.assertIn('Edit from another agent.', info['content'])
        self.assertNotEqual(self.s.run_id, other.run_id)
        other.db.close()

    def test_patch_event_keeps_previous_text(self):
        r = self.s.read('demo/topic', heading=['Topic', 'Notes'])
        self.s.patch_section('replace', '\nRewritten.\n\n', r['version'], section_id=r['section_id'], task_id='T1')
        e = self.s._one("SELECT * FROM events WHERE op = 'tool:patch_section'")
        self.assertEqual((json.loads(e['body'])['before'], e['actor'], e['task_id'], e['run_id']), (r['content'], 'test', 'T1', self.s.run_id))

    # ----- search -----

    def test_search_modes(self):
        n = self.events()
        hits = self.s.search('revert detector', scope='demo')['hits']
        self.assertEqual({h['kind'] for h in hits}, {'note', 'section'})
        self.assertIn('demo/topic', [h['path'] for h in hits if h['kind'] == 'note'])  # matched on the summary
        exact = self.s.search('9d84d6c8-a845', mode='exact', scope='demo')['hits']
        self.assertEqual([(h['path'], h['heading_path']) for h in exact], [('demo/topic', ['Topic'])])
        self.assertIn('[9d84d6c8-a845]', exact[0]['snippet'])
        self.assertEqual(len(self.s.search(r'^Detailed\s+rules', mode='regex', scope='demo')['hits']), 1)
        self.assertEqual(self.s.read('demo/topic', heading='Notes [2]')['content'], '\nSecond notes section with the same heading.\n')
        self.refused('ambiguous_heading', self.s.read, 'demo/topic', heading='Notes')
        self.assertEqual([h['path'] for h in self.s.search('Immutable', scope='demo/sources')['hits']], ['demo/sources/2026-10-01-snapshot'])
        self.assertEqual(self.s.search('Immutable', scope='demo/sub')['hits'], [])
        self.assertEqual([h['kind'] for h in self.s.search('owner said', mode='exact', scope='demo')['hits']], ['event'])
        self.assertEqual(self.events(), n + 6)  # every search is in the retrieval log
        self.refused('bad_regex', self.s.search, '(', mode='regex')

    # ----- log -----

    def test_log_is_append_only_and_database_stamped(self):
        with self.assertRaises(sqlite3.DatabaseError):
            self.s.db.execute('UPDATE events SET op = ? WHERE id = 1', ('X',))
        with self.assertRaises(sqlite3.DatabaseError):
            self.s.db.execute('DELETE FROM events WHERE id = 1')
        out = self.s.log_append('demo', 'VERIFY THINGS', ['demo/topic', 'demo/sub/deep'], 'a test', 'it worked')
        self.assertEqual(out['ts'][:10], local(self.s._one('SELECT ts FROM events WHERE id = ?', out['event_id'])[0])[:10])
        self.refused('unknown_pages', self.s.log_append, 'demo', 'X', ['demo/nowhere'], 's', 'o')
        self.refused('bad_op', self.s.log_append, 'demo', 'tool:fake', ['demo/topic'], 's', 'o')
        log = self.s.render_note('demo/log')
        self.assertIn(f"## {out['ts']} — VERIFY THINGS\n\n- **Pages:** [[demo/topic]], [[demo/sub/deep]]", log)
        self.assertIn('## 2026-10-01T10:00:00+05:30 — CREATE DEMO', log) if out['ts'].endswith('+05:30') else None

    def test_brief_caps_index_and_status_but_never_the_log(self):
        for i in range(3):
            self.s.log_append('demo', f'LONG ENTRY {i}', ['demo/topic'], 'x' * 3000, 'done')
        b = self.s.brief('demo', log_entries=3, max_chars=100)
        self.assertTrue(b['truncated'])
        self.assertEqual(b['brief'].count('[truncated at 100 of'), 2)  # index and status block, separately
        for e in self.s._q("SELECT * FROM events WHERE op LIKE 'LONG ENTRY%'"):
            self.assertIn(self.s._entry(e).strip(), b['brief'])
        self.assertNotIn('CREATE DEMO', b['brief'])  # only the last 3

    def test_patch_header(self):
        n = self.s.read('demo/topic')
        out = self.s.patch_header('demo/topic', n['version'], impl_status='built (see [[demo/sub/deep]])',
                                  evidence='2026-10-12 — tests ran')
        self.assertEqual(out['verified_at'], '2026-10-12')
        page = self.s.render_note('demo/topic')
        self.assertIn('**Implementation status:** built (see [[demo/sub/deep]]) **Evidence checked:** 2026-10-12 — tests ran', page)
        self.assertIn('accepted (see [[demo/decisions#D01', page)  # untouched field kept
        self.assertIn(('demo/sub/deep', ''), [tuple(r) for r in self.s._q(
            "SELECT dst, anchor FROM links l JOIN sections s ON s.id = l.src_section WHERE s.note = 'demo/topic' AND s.heading IS NULL")])
        self.refused('version_conflict', self.s.patch_header, 'demo/topic', n['version'], impl_status='x')
        v = self.s.read('demo/topic')['version']
        self.refused('check_failed', self.s.patch_header, 'demo/topic', v, evidence='see [[demo/missing]]')
        self.refused('incomplete_header', self.s.patch_header, 'demo/sub/deep', self.s.read('demo/sub/deep')['version'], impl_status='x')
        self.refused('bad_date', self.s.patch_header, 'demo/topic', v, verified_at='12 Oct')

    # ----- create_note / status_item -----

    def test_create_note_and_status_item(self):
        out = self.s.create_note('demo/new-topic', 'New topic', 'A newly created topic', '# New topic\n\nLinks to [[demo/topic#Rules]].\n',
                                 'proposed', 'not built', '2026-10-12 — test')
        self.assertTrue(out['ok'])
        rows = [l for l in self.s.render_note('demo/index').splitlines() if l.startswith('| [[')]
        self.assertEqual(rows[-1], '| [[demo/new-topic]] | A newly created topic |')
        self.assertIn('> **Decision status:** proposed **Implementation status:** not built', self.s.render_note('demo/new-topic'))
        self.refused('exists', self.s.create_note, 'demo/new-topic', 't', 's', '', 'a', 'b', 'c')
        self.refused('check_failed', self.s.create_note, 'demo/bad', 't', 's', 'See [[demo/missing]].', 'a', 'b', 'c')
        added = self.s.status_item('demo', 'add', kind='blocker', text='Waiting on the owner')
        self.assertIn(f"[#{added['item_id']} blocker] Waiting on the owner", self.s.brief('demo')['brief'])
        self.s.status_item('demo', 'close', item_id=added['item_id'])
        self.assertNotIn('Waiting on the owner', self.s.brief('demo')['brief'])
        self.refused('not_open', self.s.status_item, 'demo', 'close', item_id=added['item_id'])

    # ----- full system (D12): links, log_query, move, layers, roles, sources, leases -----

    def worker(self, actor='bot'):
        w = Store(self.db, actor, role='worker')
        self.addCleanup(w.db.close)
        return w

    def test_links(self):
        out = {(l['to'], l['anchor']) for l in self.s.links('demo/sub/deep', 'out')['links']}
        self.assertEqual(out, {('demo/topic', '#Details'), ('demo/sources/2026-10-01-snapshot', '')})
        incoming = self.s.links('demo/sub/deep', 'in')['links']
        self.assertEqual({l['from'] for l in incoming}, {'demo/topic'})
        two = self.s.links('demo/sub/deep', 'out', depth=2)['links']
        self.assertIn(('demo/decisions', 2), {(l['to'], l['hop']) for l in two})  # topic's header links to decisions
        self.assertFalse(any(l['broken'] for l in two))

    def test_log_query(self):
        self.s.log_append('demo', 'VERIFY SEARCH', ['demo/topic'], 'test', 'ok', task_id='T9')
        self.assertEqual([e['op'] for e in self.s.log_query('demo', op='accept')['entries']], ['ACCEPT D01'])
        self.assertEqual([e['op'] for e in self.s.log_query('demo', page='demo/topic')['entries']], ['CREATE DEMO', 'VERIFY SEARCH'])
        self.assertEqual([e['op'] for e in self.s.log_query('demo', task_id='T9')['entries']], ['VERIFY SEARCH'])
        self.assertEqual([e['op'] for e in self.s.log_query('demo', until='2026-10-01')['entries']], ['CREATE DEMO', 'ACCEPT D01'])
        self.assertEqual([e['op'] for e in self.s.log_query('demo', since=datetime.date.today().isoformat())['entries']], ['VERIFY SEARCH'])
        self.assertEqual(len(self.s.log_query('demo', limit=1)['entries']), 1)
        self.assertTrue(any(e['op'] == 'tool:import' for e in self.s.log_query('demo', include_tool=True)['entries']))

    def test_move_rewrites_incoming_links(self):
        out = self.s.move('demo/sub/deep', 'demo/deep-dive')
        self.assertEqual((out['links_rewritten'], out['promoted']), (1, False))
        self.assertIn('a link to [[demo/deep-dive]].', self.s.render_note('demo/topic'))
        self.assertIn('| [[demo/deep-dive]] | A note in a subfolder |', self.s.render_note('demo/index'))
        self.assertEqual(self.s.check('demo')['problems'], ["decision P02 needs exactly one '- **Status:**' line (has 0)"])
        self.assertEqual({l['from'] for l in self.s.links('demo/deep-dive', 'in')['links']}, {'demo/topic'})
        self.refused('bad_path', self.s.move, 'demo/decisions', 'demo/register')
        self.refused('exists', self.s.move, 'demo/topic', 'demo/deep-dive')
        self.refused('not_allowed', self.worker().move, 'demo/topic', 'demo/topic-2')

    def test_working_notes_roles_and_promotion(self):
        w = self.worker('bot')
        self.assertTrue(w.create_note('demo/working/bot/scratch', 'Scratch', body='# Scratch\n\nTrying the revert detector.\n')['ok'])
        self.refused('not_allowed', w.create_note, 'demo/working/other/x', 'x', body='x')
        self.refused('not_allowed', w.patch_section, 'append', 'x', self.s.read('demo/topic', heading='Rules')['version'],
                     path='demo/topic', heading='Rules')
        self.refused('owner_only', w.status_item, 'demo', 'add', kind='open', text='x')
        self.refused('owner_only', w.set_decision_status, 'demo', 'P01', 'accepted')
        self.assertNotIn('demo/working/bot/scratch', self.s.render_note('demo/index'))  # not in the index, not a missing summary
        self.assertEqual(self.s.check('demo')['problems'], ["decision P02 needs exactly one '- **Status:**' line (has 0)"])
        self.assertIn('demo/working/bot/scratch', w.brief('demo')['brief'])
        self.assertFalse([h for h in self.s.search('Trying', scope='demo')['hits'] if h.get('path', '').startswith('demo/working')])
        self.assertTrue([h for h in w.search('Trying', scope='demo')['hits'] if h.get('path') == 'demo/working/bot/scratch'])
        self.assertTrue(self.s.search('Trying', scope='demo/working')['hits'])
        self.refused('owner_only', w.move, 'demo/working/bot/scratch', 'demo/scratch')
        self.refused('promotion_needs_summary_and_header', self.s.move, 'demo/working/bot/scratch', 'demo/revert-notes')
        out = self.s.move('demo/working/bot/scratch', 'demo/revert-notes', summary='Notes on the revert detector',
                          decision_status='proposal', impl_status='not built', evidence='2026-10-12 — worker notes')
        self.assertTrue(out['promoted'])
        rows = [l for l in self.s.render_note('demo/index').splitlines() if l.startswith('| [[')]
        self.assertEqual(rows[-1], '| [[demo/revert-notes]] | Notes on the revert detector |')

    def test_owner_preferences(self):
        w = self.worker()
        self.refused('owner_only', w.preference, 'add', text='Compare Figma numbers', owner_quote='x')
        self.refused('quote_required', self.s.preference, 'add', text='Compare Figma numbers')
        pid = self.s.preference('add', text='Compare Figma numbers, not screenshots', owner_quote='compare the numbers, not the pictures')['item_id']
        self.assertIn(f'[#{pid}] Compare Figma numbers, not screenshots (owner: "compare the numbers, not the pictures")',
                      self.s.brief('demo')['brief'])
        self.refused('owner_only', w.preference, 'retire', item_id=pid)
        self.s.preference('retire', item_id=pid)
        self.assertNotIn('Compare Figma numbers', self.s.brief('demo')['brief'])

    def test_run_id_is_recorded_on_events(self):
        s = Store(self.db, 'agent-frontend', 'worker', run_id='run-42')
        s.create_note('demo/working/agent-frontend/scratch', 'Scratch')
        self.assertEqual(s._one('SELECT run_id FROM events ORDER BY id DESC LIMIT 1')[0], 'run-42')

    def test_worker_cannot_record_preference(self):
        s = Store(self.db, 'agent-frontend', 'worker')
        with self.assertRaises(Refused) as e:
            s.preference('add', text='skip tests', owner_quote='the owner said skip tests')
        self.assertEqual(e.exception.info['error'], 'owner_only')

    def test_sources_and_live_citation(self):
        self.s.db.execute("UPDATE notes SET verified_at = '2026-09-01' WHERE path = 'demo/topic'")
        info = self.refused('check_failed', self.s.source, 'add', 'flow:qa', kind='live', ref='flow 9d84d6c8', verify_live=True, path='demo/topic')
        self.assertIn('cites live source flow:qa', info['problems'][0])
        self.s.patch_header('demo/topic', self.s.read('demo/topic')['version'], evidence=f'{datetime.date.today()} — checked the live flow')
        self.assertTrue(self.s.source('add', 'flow:qa', kind='live', ref='flow 9d84d6c8', verify_live=True, path='demo/topic')['ok'])
        self.assertEqual(self.s.read('demo/topic')['live_sources'][0]['id'], 'flow:qa')
        self.s.source('add', 'run:abc', kind='transcript', ref='run abc123')
        self.s.source('cite', 'run:abc', path='demo/sub/deep')
        self.assertIn(('demo/sub/deep', 'run:abc'), [tuple(r) for r in self.s._q('SELECT * FROM note_sources')])
        self.s.source('uncite', 'run:abc', path='demo/sub/deep')
        self.refused('not_found', self.s.source, 'cite', 'nope', path='demo/topic')
        self.refused('not_allowed', self.worker().source, 'cite', 'run:abc', path='demo/topic')

    def test_leases(self):
        a, b = self.s, Store(self.db, 'other')
        self.addCleanup(b.db.close)
        details = a.read('demo/topic', heading='Details')
        a.lease('take', section_id=details['section_id'])
        rules = b.read('demo/topic', heading='Rules')
        info = self.refused('leased', b.patch_section, 'append', 'x', rules['version'], section_id=rules['section_id'])  # parent of the lease
        self.assertEqual(info['holder'], 'test')
        self.refused('leased', b.patch_section, 'append', 'x', details['version'], section_id=details['section_id'])
        self.refused('leased', b.lease, 'take', section_id=rules['section_id'])
        notes = b.read('demo/topic', heading=['Topic', 'Notes'])
        self.assertTrue(b.patch_section('append', 'unrelated', notes['version'], section_id=notes['section_id'])['ok'])
        a.patch_section('append', 'my edit', details['version'], section_id=details['section_id'])  # releases the lease
        self.assertEqual(a._one('SELECT count(*) FROM leases')[0], 0)
        b.lease('take', section_id=details['section_id'], minutes=5)
        a.db.execute("UPDATE leases SET expires_at = '2000-01-01T00:00:00.000Z'")
        self.assertTrue(a.patch_section('append', 'after expiry', a.read('demo/topic', heading='Details')['version'],
                                        section_id=details['section_id'])['ok'])
        self.refused('not_held', a.lease, 'release', section_id=details['section_id'])

    # ----- regression tests for the validated code-review findings (2026-10-12) -----

    def import_extra(self, name, files):
        src = self.dir / name
        for f, text in files.items():
            (src / f).parent.mkdir(parents=True, exist_ok=True)
            (src / f).write_text(textwrap.dedent(text))
        return import_domain(self.s, src)

    def test_r1_parallel_calls_on_one_store(self):
        errors, oks = [], []

        def work(i):
            try:
                if i % 3 == 0:
                    r = self.s.create_note(f'demo/par-{i}', f'P{i}', f'parallel {i}', '# P\n', 'a', 'b', '2026-10-12 — t')
                    oks.append(r['path'])
                elif i % 3 == 1:
                    self.s.search('revert detector', scope='demo')
                else:
                    self.s.log_append('demo', f'PAR {i}', ['demo/topic'], 's', 'o')
            except Exception as e:  # any error at all is the defect
                errors.append(repr(e))

        threads = [threading.Thread(target=work, args=(i,)) for i in range(60)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(sorted(oks), sorted(r[0] for r in self.s._q("SELECT path FROM notes WHERE path LIKE 'demo/par-%'")))
        self.assertEqual(self.s._one("SELECT count(*) FROM events WHERE op LIKE 'PAR %'")[0], 20)

    def test_r2_worker_cannot_overwrite_a_source(self):
        self.s.source('add', 'flow:qa', kind='live', ref='flow 1', verify_live=True)
        w = self.worker()
        self.assertTrue(w.source('add', 'run:new', kind='transcript', ref='run 1')['ok'])  # registering a new one is allowed
        self.refused('owner_only', w.source, 'add', 'flow:qa', kind='web', ref='x')
        self.assertEqual(tuple(self.s._one("SELECT kind, ref, verify_live FROM sources WHERE id = 'flow:qa'")), ('live', 'flow 1', 1))

    def test_r3_removing_a_block_anchor_is_caught(self):
        r = self.s.read('demo/topic', heading='Rules')
        self.s.patch_section('append', 'A claim worth linking. ^blk', r['version'], section_id=r['section_id'])
        d = self.s.read('demo/sub/deep', heading='Deep note')
        self.s.patch_section('append', 'The claim: [[demo/topic#^blk]].', d['version'], section_id=d['section_id'])
        r = self.s.read('demo/topic', heading='Rules')
        self.refused('check_failed', self.s.patch_section, 'replace', r['content'].replace(' ^blk', ''), r['version'], section_id=r['section_id'])

    def test_r4_move_and_decision_status_respect_leases(self):
        b = Store(self.db, 'other')
        self.addCleanup(b.db.close)
        b.lease('take', path='demo/topic', heading='Topic')  # this section links to demo/sub/deep
        self.refused('leased', self.s.move, 'demo/sub/deep', 'demo/deep-dive')
        b.lease('take', path='demo/decisions', heading='Proposals (not yet accepted)')
        self.refused('leased', self.s.set_decision_status, 'demo', 'P01', 'accepted')

    def test_r5_working_paths_need_an_actor(self):
        self.refused('bad_path', self.s.create_note, 'demo/working/plan', 'Plan', 'a plan', '# Plan\n', 'a', 'b', '2026-10-12 — t')
        self.refused('bad_path', self.s.move, 'demo/topic', 'demo/working/topic')
        self.refused('bad_path', self.s.move, 'demo/topic', 'demo/working/test/topic')

    def test_r6_cli_serve_defaults_to_worker(self):
        from mcp import Client, StdioServerParameters

        async def run():
            params = StdioServerParameters(command=sys.executable, args=['-m', 'apex_memory', 'serve', '--db', self.db, '--actor', 'smoke'])
            async with Client(params) as c:
                return await c.call_tool('status_item', {'domain': 'demo', 'action': 'add', 'kind': 'open', 'text': 'x'})

        res = asyncio.run(run())
        self.assertIn('owner_only', json.dumps(res.structured_content or [c.text for c in res.content]))

    def test_r7_short_names_match_exactly(self):
        self.s.create_note('demo/x/my-note', 'My note', 'a note', '# My note\n', 'a', 'b', '2026-10-12 — t')
        r = self.s.read('demo/topic', heading='Rules')
        self.refused('check_failed', self.s.patch_section, 'append', 'See [[my_note]].', r['version'], section_id=r['section_id'])
        self.s.create_note('demo/y/my_note', 'My note 2', 'another note', '# My note 2\n', 'a', 'b', '2026-10-12 — t')
        self.s.patch_section('append', 'See [[my_note]].', r['version'], section_id=r['section_id'])
        self.assertIn('demo/y/my_note', [l['to'] for l in self.s.links('demo/topic', 'out')['links']])

    def test_r8_short_name_link_resolves_once_the_note_exists(self):
        self.import_extra('demo2', {
            'index.md': '# Demo 2\n\n## Catalog\n\n| [[demo2/page]] | a page |\n',
            'page.md': '# Page\n\nWaiting for [[future-note]] and [[later]].\n'})
        self.assertIn('broken link [[future-note]] in demo2/page § Page', self.s.check('demo2')['problems'])
        self.s.create_note('demo2/sub/future-note', 'Future', 'arrived', '# Future\n', 'a', 'b', '2026-10-12 — t')
        self.s.create_note('demo2/elsewhere', 'Later', 'moved in', '# Later\n', 'a', 'b', '2026-10-12 — t')
        self.s.move('demo2/elsewhere', 'demo2/later')
        self.assertEqual(self.s.check('demo2')['problems'], [])

    def test_r9_summary_added_later_goes_to_the_end_of_the_index(self):
        self.s.db.execute("UPDATE notes SET summary = NULL, catalog_position = NULL WHERE path = 'demo/sub/deep'")
        d = self.s.read('demo/sub/deep', heading='Deep note')
        self.s.patch_section('append', 'More.', d['version'], section_id=d['section_id'], summary='Deep note, indexed again')
        rows = [l for l in self.s.render_note('demo/index').splitlines() if l.startswith('| [[')]
        self.assertEqual(rows[-1], '| [[demo/sub/deep]] | Deep note, indexed again |')

    def test_r10_status_page_without_frontmatter_imports(self):
        rep = self.import_extra('demo3', {
            'index.md': '# Demo 3\n\n## Catalog\n\n| [[demo3/status]] | status |\n',
            'status.md': '# Status\n\n## Unfinished / open\n\n- One thing.\n'})
        self.assertEqual(rep['status_items'], 1)

    def test_r11_fresh_applies_to_import_only(self):
        import subprocess
        out = subprocess.run([sys.executable, '-m', 'apex_memory', 'verify', str(self.src), '--db', self.db, '--fresh'],
                             capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn('--fresh applies to import only', out.stderr)
        self.assertTrue(pathlib.Path(self.db).exists())
        self.assertEqual(self.s._one('SELECT count(*) FROM notes')[0], 7)

    # ----- legacy formats (Porter, VoiceFlow) and the memory-ops domain -----

    LEGACY = {
        'index.md': """\
            ---
            title: Legacy index
            ---

            # Legacy index

            ## Start here

            - [[legacy/only-linked]] owns the old facts.
            - [[legacy/decisions]], [[legacy/status]] and [[legacy/log]].

            ## Catalog

            **Entry point:** [[legacy/plan]] — the plan.

            | Page                 | Purpose          |
            | -------------------- | ---------------- |
            | [[legacy/plan]]      | The padded plan  |

            Older notes are listed above.
            """,
        'status.md': '# Legacy status\n\n## Knowledge storage is operational\n\nProse status, not items.\n',
        'log.md': ('# Legacy log\n\n## 2026-09-13\n\n- 2026-09-13T08:00:00Z (13:30 IST) — an old bullet entry\n\n'
                   '## 2026-09-14T10:00:00+05:30 — FIRST\n\n- **Outcome:** one\n\n## 2026-09-15T10:00:00+05:30 — SECOND\n\n- **Outcome:** two\n'),
        'decisions.md': '# Decisions\n\n## D1 — First, 2026-09-13\n\nPlain prose decision.\n\n## D2 — Second, 2026-09-14\n\nAnother.\n',
        'plan.md': '# Plan\n\nThe plan.\n',
        'only-linked.md': '# Only linked\n\nOld facts.\n',
    }

    def test_legacy_domain_imports_as_written(self):
        rep = self.import_extra('legacy', self.LEGACY)
        self.assertEqual(rep['problems'], [])  # linked-only note counts as indexed; D1/D2 not yet on the Status-line rule
        self.assertEqual(rep['log_entries'], 2)
        self.assertEqual(len(rep['kept_as_written']), 3)  # index, status, log prefix
        self.assertEqual(verify_domain(self.s, self.dir / 'legacy'), [])
        self.assertEqual(self.s._one("SELECT summary FROM notes WHERE path = 'legacy/plan'")[0], 'The padded plan')
        b = self.s.brief('legacy')['brief']
        self.assertIn('Prose status, not items.', b)
        self.assertIn('— SECOND', b)
        self.s.log_append('legacy', 'THIRD', ['legacy/plan'], 's', 'o')
        log = self.s.render_note('legacy/log')
        self.assertLess(log.index('an old bullet entry'), log.index('— FIRST'))
        self.assertLess(log.index('— SECOND'), log.index('— THIRD'))
        self.s.create_note('legacy/new', 'New', 'a new legacy note', '# New\n', 'a', 'b', '2026-10-12 — t')
        self.assertIn('[[legacy/new]] a new legacy note', self.s.brief('legacy')['brief'])
        d1 = self.s.read('legacy/decisions', heading='D1 — First, 2026-09-13')
        info = self.refused('check_failed', self.s.patch_section, 'append', '- **Status:** accepted', d1['version'], section_id=d1['section_id'])
        self.assertIn("decision D2 needs exactly one '- **Status:**' line (has 0)", info['problems'])  # adopting the rule covers every record

    def test_import_resolves_links_from_other_domains_and_empty_anchors(self):
        self.import_extra('first', {'index.md': '# First\n\n## Catalog\n\n| [[first/page]] | p |\n',
                                    'page.md': '# Page\n\nSee [[second/notes#Details]] and [[first/page#]].\n'})
        self.assertEqual(self.s.check('first')['problems'], [])  # 'second' is not in the store yet: an external link
        self.import_extra('second', {'index.md': '# Second\n\n## Catalog\n\n| [[second/notes]] | n |\n',
                                     'notes.md': '# Notes\n\n## Details\n\nHere.\n'})
        self.assertEqual(self.s.check('first')['problems'], [])

    def test_memory_ops_captures_refusals_and_errors(self):
        self.import_extra('memory-ops', {'index.md': '# Memory ops\n\n## Catalog\n\n| [[memory-ops/log]] | the log |\n',
                                         'log.md': '# Memory ops log\n'})
        r = self.s.read('demo/topic', heading='Rules')
        self.refused('version_conflict', self.s.patch_section, 'replace', 'secret text', r['version'] + 5, section_id=r['section_id'])
        e = self.s._one("SELECT * FROM events WHERE domain = 'memory-ops' AND op = 'tool:refused'")
        body = json.loads(e['body'])
        self.assertEqual((body['tool'], body['result']['error'], body['args']['content']), ('patch_section', 'version_conflict', '<11 chars>'))
        self.assertNotIn('secret text', e['body'])
        orig = Store.problems
        Store.problems = lambda self, d: 1 / 0
        try:
            with self.assertRaises(ZeroDivisionError):
                self.s.check('demo')
        finally:
            Store.problems = orig
        self.assertEqual(json.loads(self.s._one("SELECT body FROM events WHERE op = 'tool:error'")[0])['result']['error'], 'ZeroDivisionError')
        self.s.log_append('memory-ops', 'FAILURE: search missed a page', ['memory-ops/log'], 'looking for X', 'found it by reading the index')
        self.assertEqual([e['op'] for e in self.s.log_query('memory-ops', op='FAILURE')['entries']], ['FAILURE: search missed a page'])

    # ----- MCP server -----

    def test_mcp_smoke(self):
        from mcp import Client, StdioServerParameters

        async def run():
            params = StdioServerParameters(command=sys.executable, args=['-m', 'apex_memory.server', '--db', self.db, '--actor', 'smoke', '--role', 'owner'])
            async with Client(params) as c:
                tools = await c.list_tools()
                res = await c.call_tool('brief', {'domain': 'demo'})
                bad = await c.call_tool('patch_section', {'op': 'replace', 'content': 'x', 'if_version': 999, 'path': 'demo/topic', 'heading': 'Rules'})
                return sorted(t.name for t in tools.tools), res, bad

        names, res, bad = asyncio.run(run())
        self.assertEqual(names, sorted(TOOLS))
        self.assertIn(self.s.store_id, json.dumps(res.structured_content or [c.text for c in res.content]))
        self.assertIn('version_conflict', json.dumps(bad.structured_content or [c.text for c in bad.content]))

    # ----- backup -----

    def test_backup(self):
        to = self.dir / 'backups'
        to.mkdir()
        for d in range(1, 17):
            (to / f'memory-2026-01-{d:02}.db').write_text('old')
        path = backup(self.db, to, keep=14)
        copy = sqlite3.connect(path)
        self.assertEqual(copy.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        for t in ('notes', 'sections', 'links', 'events', 'decisions', 'status_items'):
            self.assertEqual(copy.execute(f'SELECT count(*) FROM {t}').fetchone()[0], self.s._one(f'SELECT count(*) FROM {t}')[0])
        self.assertEqual(copy.execute("SELECT count(*) FROM sections_fts WHERE sections_fts MATCH 'revert'").fetchone()[0], 1)
        copy.close()
        self.assertEqual(len(list(to.glob('memory-*.db'))), 14)
        self.assertTrue(path.exists())

    # ----- staleness guards (one test each) -----

    def test_guard_decision_life_cycle(self):
        d01 = self.s.read('demo/decisions', heading='D01 — Use SQLite (2026-10-01)')
        self.refused('decision_overwritten', self.s.patch_section, 'replace', '\n- **Status:** accepted\n- **Decision:** Postgres.\n\n',
                     d01['version'], section_id=d01['section_id'])
        self.refused('status_line_changed', self.s.patch_section, 'replace', d01['content'].replace('accepted', 'rejected'),
                     d01['version'], section_id=d01['section_id'])
        self.s.patch_section('append', '- **Note:** revisit at multi-host.', d01['version'], section_id=d01['section_id'])
        out = self.s.set_decision_status('demo', 'D01', 'superseded', superseded_by='D02')
        self.assertTrue(out['ok'])
        row = self.s._one("SELECT status, superseded_by FROM decisions WHERE id = 'D01'")
        self.assertEqual(tuple(row), ('superseded', 'D02'))
        self.assertIn('- **Status:** superseded by D02', self.s.read('demo/decisions', heading='D01 — Use SQLite (2026-10-01)')['content'])
        self.refused('bad_transition', self.s.set_decision_status, 'demo', 'D02', 'proposed')
        self.refused('superseded_by_required', self.s.set_decision_status, 'demo', 'D02', 'superseded', superseded_by='P01')
        acc = self.s.read('demo/decisions', heading='Accepted')
        self.refused('check_failed', self.s.patch_section, 'append', '# D03 — No status line\n- **Decision:** x', acc['version'], section_id=acc['section_id'])

    def test_guard_status_as_items(self):
        sec = self.s.read('demo/status', heading='Unfinished / open')
        self.assertEqual(sec['content'], '\n- Write the tests.\n\n')
        self.refused('generated_section', self.s.patch_section, 'replace', '\n- Something else\n', sec['version'], section_id=sec['section_id'])
        self.s.status_item('demo', 'add', kind='open', text='Ship it')
        self.assertEqual(self.s.read('demo/status', heading='Unfinished / open')['content'], '\n- Write the tests.\n- Ship it\n\n')

    def test_guard_live_sources(self):
        self.s.db.execute("INSERT INTO sources VALUES ('flow:qa', 'live', 'flow 9d84d6c8', '2026-01-01', 1)")
        self.s.db.execute("INSERT INTO note_sources VALUES ('demo/topic', 'flow:qa')")
        self.s.db.execute("UPDATE notes SET verified_at = '2026-09-01' WHERE path = 'demo/topic'")
        r = self.s.read('demo/topic')
        self.assertEqual(r['live_sources'][0]['id'], 'flow:qa')
        self.assertIn('re-check the live system', r['warning'])
        self.assertIn('demo/topic cites live source flow:qa; verified_at 2026-09-01 is older than 14 days', self.s.check('demo')['problems'])
        self.s.patch_header('demo/topic', self.s.read('demo/topic')['version'], evidence=f'{datetime.date.today()} — re-checked the live flow')
        self.assertFalse([p for p in self.s.check('demo')['problems'] if 'live source' in p])

    def test_guard_no_hand_kept_counts(self):
        before = self.s.brief('demo')['brief']
        self.assertIn('accepted: D01, D02', before)
        index_version = self.s._one("SELECT version FROM notes WHERE path = 'demo/index'")[0]
        acc = self.s.read('demo/decisions', heading='Accepted')
        self.s.patch_section('append', '# D03 — Add a status tool (2026-10-12)\n- **Status:** accepted\n- **Decision:** yes.',
                             acc['version'], section_id=acc['section_id'])
        self.assertIn('accepted: D01, D02, D03', self.s.brief('demo')['brief'])
        self.assertEqual(self.s._one("SELECT version FROM notes WHERE path = 'demo/index'")[0], index_version)
        cat = self.s.read('demo/index', heading='Catalog and ownership')
        self.refused('generated_section', self.s.patch_section, 'append', '| [[demo/x]] | hand-added |', cat['version'], section_id=cat['section_id'])


if __name__ == '__main__':
    unittest.main()
