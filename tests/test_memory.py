"""Store tests. Run: uv run python -m unittest -v

MemoryTest runs on SQLite; PostgresMemoryTest runs the same tests on Postgres when APEX_MEMORY_TEST_POSTGRES_URL
names a database the tests may create schemas in (each test gets its own schema).
"""
import ast, asyncio, datetime, json, os, pathlib, re, sqlite3, subprocess, sys, tempfile, textwrap, threading, unittest, urllib.parse, uuid
from unittest import mock

from apex_memory.backends import backup, copy_store, open_backend
from apex_memory.importer import import_domain, verify_domain
from apex_memory.store import Refused, Store, TOOLS, local

PG_URL = os.environ.get('APEX_MEMORY_TEST_POSTGRES_URL')
ROOT = pathlib.Path(__file__).resolve().parents[1]


def cli(*args):
    return subprocess.run([sys.executable, '-m', 'apex_memory', *args], capture_output=True, text=True, cwd=ROOT)

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
        self.db = self.new_url()
        self.s = Store(self.db, 'test')
        self.report = import_domain(self.s, self.src)

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def new_url(self, name='test'):
        """A URL for a new, empty store on the backend under test."""
        return str(self.dir / f'{name}-{uuid.uuid4().hex[:8]}.db')

    @staticmethod
    def no_desc(domain):
        return f'{domain}/index has no summary: the domain description that list_domains shows'

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
        info = self.refused('version_conflict', self.s.patch_section, 'prepend', 'stale', b['version'], section_id=b['section_id'])
        self.assertEqual(info['current_version'], b['version'] + 1)
        self.assertIn('Edit from another agent.', info['content'])
        self.assertNotEqual(self.s.run_id, other.run_id)
        other.db.close()

    def test_append_needs_no_version(self):
        # Real case (memory-ops events 780, 853): appends to a big 'Accepted' parent refused as version_conflict.
        acc = self.s.read('demo/decisions', heading='Accepted')
        self.s.patch_section('append', '# D03 — First (2026-10-12)\n- **Status:** accepted', acc['version'], section_id=acc['section_id'])
        out = self.s.patch_section('append', '# D04 — Second (2026-10-12)\n- **Status:** accepted', acc['version'], section_id=acc['section_id'])
        self.assertTrue(out['ok'])  # a stale version is not enforced for append
        self.assertTrue(self.s.patch_section('append', 'No version given.', path='demo/topic', heading='Rules')['ok'])
        self.assertIn('accepted: D01, D02, D03, D04', self.s.brief('demo')['brief'])
        for op in ('replace', 'prepend'):
            self.refused('version_required', self.s.patch_section, op, 'x', path='demo/topic', heading='Rules')
        b = Store(self.db, 'other')
        self.addCleanup(b.db.close)
        b.lease('take', path='demo/topic', heading='Rules')
        self.refused('leased', self.s.patch_section, 'append', 'x', path='demo/topic', heading='Rules')  # leases still apply

    def test_write_events_record_the_shape_not_the_text(self):
        # Real case (memory-ops event 971): a page with an empty body could not be traced; events did not say what was written.
        self.s.create_note('demo/empty', 'Empty', 'a page', '', 'a', 'b', '2026-10-31 — t')
        e = json.loads(self.s._one("SELECT body FROM events WHERE op = 'tool:create_note'")[0])
        self.assertEqual(e, {'note_created': 'demo/empty', 'shape': {'chars': 0, 'lines': 0, 'headings': 0}, 'sections': 1})
        r = self.s.read('demo/topic', heading='Rules')
        self.s.patch_section('append', '# Secret one\n\nsecret text\n\n# Secret two\n\nmore', section_id=r['section_id'])
        e = json.loads(self.s._one("SELECT body FROM events WHERE op = 'tool:patch_section' ORDER BY id DESC LIMIT 1")[0])
        self.assertEqual((e['op'], e['shape'], len(e['sections']['created']), e['sections']['removed']),
                         ('append', {'chars': 45, 'lines': 7, 'headings': 2}, 2, []))
        self.assertEqual(e['sections']['changed'], [])  # the parent and its other subsections kept their text
        self.assertNotIn('secret', json.dumps({k: v for k, v in e.items() if k != 'before'}))
        r = self.s.read('demo/topic', heading='Rules')
        self.s.patch_section('replace', r['content'].split('# Secret one')[0], r['version'], section_id=r['section_id'])
        e = json.loads(self.s._one("SELECT body FROM events WHERE op = 'tool:patch_section' ORDER BY id DESC LIMIT 1")[0])
        self.assertEqual((len(e['sections']['removed']), e['sections']['created']), (2, []))
        n = self.s.read('demo/topic')
        self.s.patch_header('demo/topic', n['version'], impl_status='built')
        e = json.loads(self.s._one("SELECT body FROM events WHERE op = 'tool:patch_header'")[0])
        self.assertEqual(e['shape'], {'impl_status': {'chars': 5, 'lines': 1, 'headings': 0}})

    def test_patch_event_keeps_previous_text(self):
        r = self.s.read('demo/topic', heading=['Topic', 'Notes'])
        self.s.patch_section('replace', '\nRewritten.\n\n', r['version'], section_id=r['section_id'], task_id='T1')
        e = self.s._one("SELECT * FROM events WHERE op = 'tool:patch_section'")
        self.assertEqual((json.loads(e['body'])['before'], e['actor'], e['task_id'], e['run_id']), (r['content'], 'test', 'T1', self.s.run_id))

    # ----- smarter checks -----

    PARA = 'This paragraph is long enough to count as a claim for the duplicate check of the store, because it has more than two hundred characters of text in it, and it was pasted into several notes of one domain. '

    def test_removing_one_copy_of_a_duplicate_is_allowed(self):
        # Real case (memory-ops event 291): de-duplicating a paragraph pasted into 10 pages was refused step by step.
        self.import_extra('dup', {'index.md': '# Dup\n\n## Catalog\n\n| [[dup/a]] | a |\n| [[dup/b]] | b |\n| [[dup/c]] | c |\n| [[dup/d]] | d |\n',
                                  **{f'{n}.md': f'# {n.upper()}\n\n{self.PARA}\n' for n in 'abc'}, 'd.md': '# D\n\nOther text.\n'})
        self.assertEqual(len(self.s.check('dup')['problems']), 2)  # the paragraph, and the index without a description
        a = self.s.read('dup/a', heading='A')
        self.assertTrue(self.s.patch_section('replace', '\nSee [[dup/c]].\n', a['version'], section_id=a['section_id'])['ok'])
        self.assertIn('same paragraph in dup/b, dup/c:', self.s.check('dup')['problems'][1])
        d = self.s.read('dup/d', heading='D')
        self.refused('check_failed', self.s.patch_section, 'append', self.PARA, section_id=d['section_id'])  # one more copy
        self.assertTrue(self.s.patch_section('append', self.PARA.replace('long', 'very long'), section_id=a['section_id'])['ok'])  # one copy
        self.refused('check_failed', self.s.patch_section, 'append', self.PARA.replace('long', 'very long'), section_id=d['section_id'])  # a new duplicate
        b = self.s.read('dup/b', heading='B')
        self.assertTrue(self.s.patch_section('replace', '\nSee [[dup/c]].\n', b['version'], section_id=b['section_id'])['ok'])
        self.assertEqual(self.s.check('dup')['problems'], [self.no_desc('dup')])

    def test_root_only_page_gets_headings(self):
        # Real case (memory-ops events 984-995): a page whose only section is the root could not get any heading.
        self.s.create_note('demo/flat', 'Flat', 'a page without headings', 'Only text.\n', 'a', 'b', '2026-10-24 — t')
        root = self.s.read('demo/flat')['outline'][0]
        out = self.s.patch_section('append', '# Area one\n\nFacts.\n\n## Detail\n\nMore.', section_id=root['section_id'])
        self.assertTrue(out['ok'])
        r = self.s.read('demo/flat')
        self.assertEqual([o['heading_path'] for o in r['outline']], [[], ['Area one'], ['Area one', 'Detail']])
        self.assertIn('Only text.\n\n# Area one\n\nFacts.\n\n## Detail\n\nMore.', r['content'])
        self.assertEqual(self.s.read('demo/flat', heading='Detail')['content'], '\nMore.\n\n')
        self.refused('headings_not_allowed', self.s.patch_section, 'append', '# Area two', section_id=root['section_id'])  # no longer root-only
        self.s.create_note('demo/flat2', 'Flat 2', 'another', 'Text.\n', 'a', 'b', '2026-10-24 — t')
        root2 = self.s.read('demo/flat2')['outline'][0]
        self.s.patch_section('replace', '\nNew text.\n\n# Part\n\nBody.\n', root2['version'], section_id=root2['section_id'])
        self.assertEqual([o['heading_path'] for o in self.s.read('demo/flat2')['outline']], [[], ['Part']])

    # ----- new tools -----

    def test_replace_own_text_keeps_subsections(self):
        # Real case (memory-ops event 160): one sentence in a 30k-char index intro needed the whole subtree resent.
        r = self.s.read('demo/topic', heading='Topic')
        ids = [o['section_id'] for o in r['outline']]
        self.refused('version_required', self.s.patch_section, 'replace_own_text', 'x', section_id=r['section_id'])
        self.refused('headings_not_allowed', self.s.patch_section, 'replace_own_text', 'x\n\n# Sub', r['version'], section_id=r['section_id'])
        self.refused('check_failed', self.s.patch_section, 'replace_own_text', 'See [[demo/nowhere]].', r['version'], section_id=r['section_id'])
        out = self.s.patch_section('replace_own_text', 'New intro, still linking [[demo/sub/deep]].', r['version'], section_id=r['section_id'])
        r2 = self.s.read('demo/topic', heading='Topic')
        self.assertEqual([o['section_id'] for o in r2['outline']], ids)
        self.assertEqual(r2['content'], r['content'].replace(r['content'][:r['content'].index('# Rules')], '\nNew intro, still linking [[demo/sub/deep]].\n\n'))
        self.assertEqual(out['version'], r2['version'])
        d01 = self.s.read('demo/decisions', heading='D01 — Use SQLite (2026-10-01)')
        self.refused('decision_overwritten', self.s.patch_section, 'replace_own_text', '- **Status:** accepted\n- **Decision:** Postgres.',
                     d01['version'], section_id=d01['section_id'])
        idx = self.s.read('demo/index', heading='Demo knowledge index')  # its subtree holds the generated catalog
        self.assertTrue(self.s.patch_section('replace_own_text', 'The demo domain.', idx['version'], section_id=idx['section_id'])['ok'])

    def test_replace_own_text_preserves_generated_rows_and_later_updates(self):
        for path, heading in (('demo/index', 'Demo knowledge index'), ('demo/status', 'Demo — status')):
            r = self.s.read(path, heading=heading)
            descendants = [dict(s) for s in self.s._sections(path) if s['level'] and s['level'] > 1]
            self.s.patch_section('replace_own_text', 'New introduction.', r['version'], section_id=r['section_id'])
            self.assertEqual([dict(s) for s in self.s._sections(path) if s['level'] and s['level'] > 1], descendants)
        rules = self.s.read('demo/topic', heading='Rules')
        self.s.patch_section('append', 'More rules.', section_id=rules['section_id'], summary='Updated topic summary')
        index = self.s.render_note('demo/index')
        self.assertEqual(index.count('| [[demo/topic]] |'), 1)
        self.assertIn('| [[demo/topic]] | Updated topic summary |', index)
        self.assertNotIn('**Owner:** the topic', index)
        item = self.s.status_item('demo', 'add', kind='open', text='Temporary item')['item_id']
        self.s.status_item('demo', 'close', item_id=item)
        self.assertNotIn('Temporary item', self.s.render_note('demo/status'))
        self.assertEqual(self.s.render_note('demo/status').count('- Write the tests.'), 1)

    def test_replace_own_text_with_open_fence_keeps_subsections(self):
        r = self.s.read('demo/topic', heading='Topic')
        descendants = [dict(s) for s in self.s._subtree(self.s._target(section_id=r['section_id']))[1:]]
        self.s.patch_section('replace_own_text', 'Example:\n```text\nunclosed', r['version'], section_id=r['section_id'])
        self.assertEqual([dict(s) for s in self.s._subtree(self.s._target(section_id=r['section_id']))[1:]], descendants)
        self.assertIn('Detailed rules live here.', self.s.read('demo/topic', heading='Details')['content'])

    def test_create_domain_and_list_domains(self):
        # Real cases: no tool created a domain (event 511); an agent could not find a domain (event 296, brief('__list__')).
        w = self.worker()
        self.refused('owner_only', w.create_domain, 'newdom', 'New domain', 'Holds X.')
        self.refused('bad_name', self.s.create_domain, 'New Dom', 'New domain', 'Holds X.')
        self.refused('description_required', self.s.create_domain, 'newdom', 'New domain', ' ')
        self.refused('domain_exists', self.s.create_domain, 'demo', 'Demo', 'Holds demo.')
        out = self.s.create_domain('newdom', 'New domain', 'The new product: its plans and contracts. Not for the demo; use demo.')
        self.assertEqual(out['pages'], ['newdom/index', 'newdom/status', 'newdom/log', 'newdom/decisions'])
        self.assertEqual(self.s.check('newdom')['problems'], [])
        self.assertEqual([l for l in self.s.render_note('newdom/index').splitlines() if l.startswith('| [[')],
                         ['| [[newdom/decisions]] | Decision records |', '| [[newdom/status]] | Current state |', '| [[newdom/log]] | Append-only activity |'])
        self.s.create_note('newdom/plan', 'Plan', 'the plan', '# Plan\n', 'a', 'b', '2026-10-31 — t')
        self.s.status_item('newdom', 'add', kind='next', text='Write the plan')
        self.s.log_append('newdom', 'START', ['newdom/plan'], 's', 'o')
        self.s.patch_section('append', '# D01 — Start (2026-10-31)\n- **Status:** accepted', path='newdom/decisions', heading='New domain decisions')
        b = self.s.brief('newdom')['brief']
        for text in ('accepted: D01', 'Write the plan', '— START', '| [[newdom/plan]] | the plan |'):
            self.assertIn(text, b)
        self.s.create_domain('memory-ops', 'Memory ops', 'Failures and friction of the memory system itself.')
        self.refused('not_found', self.s.read, 'demo/nowhere')
        self.assertEqual(self.s._one("SELECT count(*) FROM events WHERE domain = 'memory-ops' AND op = 'tool:refused'")[0], 1)
        doms = {d['domain']: d for d in w.list_domains()['domains']}
        self.assertEqual(sorted(doms), ['demo', 'memory-ops', 'newdom'])
        self.assertEqual(doms['newdom']['description'], 'The new product: its plans and contracts. Not for the demo; use demo.')
        self.assertEqual((doms['newdom']['notes'], doms['newdom']['ops'], doms['memory-ops']['ops']), (5, False, True))
        self.assertEqual(doms['newdom']['last_log'][:10], datetime.date.today().isoformat())
        self.assertNotIn('description_missing', doms['newdom'])
        demo = doms['demo']  # imported: no description yet, so the first paragraph of the index stands in
        self.assertEqual((demo['description'], demo['description_missing'], demo['notes']), ('Primary knowledge base for the demo.', True, 7))
        self.assertEqual(demo['last_log'], local('2026-10-01T05:30:00.000Z'))

    def test_index_without_summary_is_a_problem(self):
        problem = 'demo/index has no summary: the domain description that list_domains shows'
        self.assertIn(problem, self.s.check('demo')['problems'])
        r = self.s.read('demo/topic', heading='Rules')
        self.assertTrue(self.s.patch_section('append', 'More.', section_id=r['section_id'])['ok'])  # existing debt does not refuse
        idx = self.s.read('demo/index', heading='Demo knowledge index')
        self.s.patch_section('replace_own_text', 'Primary knowledge base for the demo.', idx['version'], section_id=idx['section_id'],
                             summary='The demo: a fixture domain for the tests.')
        self.assertNotIn(problem, self.s.check('demo')['problems'])
        self.assertNotIn('[[demo/index]]', self.s.render_note('demo/index'))  # the index is not a row of its own catalog
        self.assertEqual(self.s.list_domains()['domains'][0]['description'], 'The demo: a fixture domain for the tests.')

    def test_void_an_accepted_decision(self):
        # Real case (memory-ops events 829-833): a mistaken acceptance could not be undone honestly.
        self.refused('reason_required', self.s.set_decision_status, 'demo', 'D02', 'void')
        self.refused('owner_only', self.worker().set_decision_status, 'demo', 'D02', 'void', reason='x')
        self.refused('bad_transition', self.s.set_decision_status, 'demo', 'P01', 'void', reason='x')
        out = self.s.set_decision_status('demo', 'D02', 'void', reason='accepted from a question, not a decision')
        self.assertEqual(out['status'], 'void')
        content = self.s.read('demo/decisions', heading='D02 — No export (2026-10-02)')['content']
        self.assertIn(f'- **Status:** void\n- **Void reason:** accepted from a question, not a decision ({datetime.date.today()})\n', content)
        self.assertIn('- **Decision:** no markdown export.', content)  # the record stays
        b = self.s.brief('demo')['brief']
        self.assertIn('accepted: D01\n', b)
        self.assertIn('void: D02', b)
        self.assertEqual(self.s._one("SELECT status FROM decisions WHERE id = 'D02'")[0], 'void')
        self.refused('bad_transition', self.s.set_decision_status, 'demo', 'D02', 'accepted')
        self.refused('superseded_by_required', self.s.set_decision_status, 'demo', 'D01', 'superseded', superseded_by='D02')
        self.assertIn('void', json.loads(self.s._one("SELECT body FROM events WHERE op = 'tool:set_decision_status'")[0])['to'])

    def test_void_reason_links_are_checked_and_indexed(self):
        before = self.snapshot(self.s)
        info = self.refused('check_failed', self.s.set_decision_status, 'demo', 'D02', 'void', reason='See [[demo/missing]].')
        self.assertIn('broken link [[demo/missing]]', info['problems'][0])
        self.assertEqual(self.snapshot(self.s), before)
        self.s.set_decision_status('demo', 'D02', 'void', reason='See [[demo/topic#Details]].')
        links = self.s.links('demo/topic', 'in')['links']
        self.assertTrue(any(l['from'] == 'demo/decisions' and l['anchor'] == '#Details' and not l['broken'] for l in links))

    def test_status_item_update(self):
        # Real case (memory-ops event 796): an open item's text could not be changed.
        item = self.s.status_item('demo', 'add', kind='open', text='Old text')['item_id']
        self.refused('owner_only', self.worker().status_item, 'demo', 'update', item_id=item, text='x')
        self.refused('bad_item', self.s.status_item, 'demo', 'update', item_id=item, text=' ')
        self.refused('bad_item', self.s.status_item, 'demo', 'update', item_id=item, text='x', kind='wish')
        self.s.status_item('demo', 'update', item_id=item, text='New text', kind='blocker')
        self.assertIn(f'[#{item} blocker] New text', self.s.brief('demo')['brief'])
        self.assertNotIn('Old text', self.s.brief('demo')['brief'])
        e = json.loads(self.s._one("SELECT body FROM events WHERE op = 'tool:status_item' ORDER BY id DESC LIMIT 1")[0])
        self.assertEqual((e['old_text'], e['old_kind'], e['text'], e['kind']), ('Old text', 'open', 'New text', 'blocker'))
        self.s.status_item('demo', 'close', item_id=item)
        self.refused('not_open', self.s.status_item, 'demo', 'update', item_id=item, text='y')

    def test_older_schema_migrates_and_allows_void(self):
        from apex_memory.backends.base import SQL
        url = self.new_url('older')
        old = open_backend(url)
        files = sorted((SQL / old.name).glob('*.sql'))
        with old.transaction():
            for f in files[:-1]:
                old.run_script(f.read_text())
            old.set_schema_version(len(files) - 1)
        copy_store(self.s.db, old)
        old.close()
        before = self.snapshot(self.s)
        s = Store(url, 'test')
        self.addCleanup(s.db.close)
        self.assertEqual((s.db.schema_version(), self.snapshot(s)), (len(files), before))
        self.assertTrue(s.set_decision_status('demo', 'D02', 'void', reason='test')['ok'])

    def test_server_instructions_start_with_domain_discovery(self):
        from apex_memory.server import build
        text = build(self.s).instructions
        self.assertTrue(text.startswith('Call list_domains, choose the domains whose description matches the task, then brief(domain); '
                                        'if none matches, ask the owner before create_domain.'), text)

    # ----- read suggestions -----

    def test_not_found_suggests_candidates(self):
        # Real cases (memory-ops events 350, 523, 735, 754, 976, 992): reads that missed gave no way forward.
        info = self.refused('not_found', self.s.read, 'demo/decisions', heading='D01 — Use SQLite')  # date suffix missing
        self.assertEqual(info['candidates'][0]['heading_path'], ['Decisions', 'Accepted', 'D01 — Use SQLite (2026-10-01)'])
        self.assertIn('section_id', info['candidates'][0])
        info = self.refused('not_found', self.s.read, 'demo/decisions', heading=['Proposals', 'P01 — Vectors later'])  # wrong parent
        self.assertEqual(info['candidates'][0]['heading_path'], ['Decisions', 'Proposals (not yet accepted)', 'P01 — Vectors later'])
        info = self.refused('not_found', self.s.read, 'demo/topic', heading='Detail')
        self.assertEqual(info['candidates'][0]['heading_path'], ['Topic', 'Rules', 'Details'])
        self.assertLessEqual(len(info['candidates']), 5)
        self.assertEqual(self.refused('not_found', self.s.read, 'demo/topic', heading='Zebra crossing')['candidates'], [])
        self.assertEqual(self.refused('not_found', self.s.read, 'demo/topik')['candidates'][0], 'demo/topic')
        self.assertIn('demo/sub/deep', self.refused('not_found', self.s.read, 'demo/deep')['candidates'])
        info = self.refused('path_required', self.s.read)
        self.assertIn('section_id', info['hint'])
        self.assertIn('heading=[]', self.refused('heading_required', self.s.patch_section, 'append', 'x', path='demo/topic')['hint'])
        self.assertEqual(self.refused('not_found', self.s.patch_section, 'append', 'x', path='demo/topic', heading='Detail')['candidates'][0]['heading_path'],
                         ['Topic', 'Rules', 'Details'])

    # ----- batch -----

    def test_batch_writes_cross_linked_pages(self):
        # Real case (memory-ops events 914-917): two notes that link to each other, and a log entry naming both.
        hdr = {'decision_status': 'proposal', 'impl_status': 'none', 'evidence': '2026-10-23 — test'}
        ops = [{'tool': 'create_note', 'args': {'path': 'demo/a', 'title': 'A', 'summary': 'note a', 'body': '# A\n\nSee [[demo/b#Details B]].\n', **hdr}},
               {'tool': 'log_append', 'args': {'domain': 'demo', 'op': 'LINK A AND B', 'pages': ['demo/a', 'demo/b'], 'source': 's', 'outcome': 'o'}},
               {'tool': 'create_note', 'args': {'path': 'demo/b', 'title': 'B', 'summary': 'note b', 'body': '# B\n\n## Details B\n\nBack to [[demo/a]].\n', **hdr}}]
        self.refused('check_failed', self.s.create_note, **ops[0]['args'])
        self.refused('unknown_pages', self.s.log_append, **ops[1]['args'])
        out = self.s.batch(ops, task_id='T7')
        self.assertEqual([r['ok'] for r in out['results']], [True, True, True])
        self.assertEqual(out['results'][2]['path'], 'demo/b')
        self.assertEqual(self.s.check('demo')['problems'], [self.no_desc('demo')] + ["decision P02 needs exactly one '- **Status:**' line (has 0)"])
        self.assertEqual([e['op'] for e in self.s.log_query('demo', task_id='T7')['entries']], ['LINK A AND B'])
        self.assertEqual(len(self.s.log_query('demo', task_id='T7', include_tool=True)['entries']), 3)

    def test_batch_is_all_or_nothing(self):
        before = self.snapshot(self.s)
        r = self.s.read('demo/topic', heading='Rules')
        good = {'tool': 'create_note', 'args': {'path': 'demo/c', 'title': 'C', 'summary': 'c', 'body': '# C\n', 'decision_status': 'a',
                                               'impl_status': 'b', 'evidence': '2026-10-23 — t'}}
        info = self.refused('version_conflict', self.s.batch, [good, {'tool': 'patch_section', 'args': {
            'op': 'replace', 'content': 'x', 'if_version': r['version'] + 9, 'section_id': r['section_id']}}])
        self.assertEqual((info['op_index'], info['tool']), (1, 'patch_section'))
        info = self.refused('check_failed', self.s.batch, [good, {'tool': 'patch_section', 'args': {
            'op': 'append', 'content': 'See [[demo/nowhere]].', 'section_id': r['section_id']}}])
        self.assertIn('broken link [[demo/nowhere]]', info['problems'][0])
        self.assertEqual(self.refused('bad_batch', self.s.batch, [good, {'tool': 'move', 'args': {}}])['op_index'], 1)
        self.assertEqual(self.refused('bad_args', self.s.batch, [{'tool': 'log_append', 'args': {'x': 1}}])['op_index'], 0)
        self.assertEqual(self.refused('not_allowed', self.worker().batch, [good])['op_index'], 0)  # role rules per op
        self.assertEqual(self.snapshot(self.s), before)

    def test_batch_cannot_reuse_another_domains_decision_problem(self):
        self.import_extra('aaa', {'index.md': '# A\n', 'decisions.md': '# Decisions\n\n## D03 — Legacy\n\nNo status line.\n'})
        args = {'op': 'append', 'content': '# D03 — New\n\nNo status line.', 'path': 'demo/decisions', 'heading': 'Accepted'}
        self.refused('check_failed', self.s.patch_section, **args)
        before = self.snapshot(self.s)
        info = self.refused('check_failed', self.s.batch, [{'tool': 'patch_section', 'args': args}])
        self.assertEqual(info['problems'], ["decision D03 needs exactly one '- **Status:**' line (has 0)"])
        self.assertEqual(self.snapshot(self.s), before)

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

    def test_bm25_prefers_hits_that_hold_more_query_terms(self):
        # Real case (memory-ops IDEA 1052): weak one-word matches outranked real answers.
        self.s.create_note('demo/weak', 'Weak', 'cache cache', '# Cache\n\nCache cache cache. Cache.\n', 'a', 'b', '2026-10-31 — t')
        filler = ' '.join(f'word{i}' for i in range(120))
        self.s.create_note('demo/strong', 'Strong', 'a long note', f'# Notes\n\n{filler} the tool cache invalidation rule. {filler}\n',
                           'a', 'b', '2026-10-31 — t')
        hits = self.s.search('tool cache invalidation', scope='demo')['hits']
        self.assertEqual(hits[0]['path'], 'demo/strong')
        self.assertNotIn('demo/weak', [h.get('path') for h in hits])  # below the floor: 1 of 3 terms
        self.assertIn('demo/weak', [h.get('path') for h in self.s.search('cache zebra', scope='demo')['hits']])  # none passes: all stay

    def test_hidden_working_notes_do_not_set_the_search_floor(self):
        self.s.create_note('demo/answer', 'Answer', 'public answer', '# Answer\n\nCache rules.\n', 'a', 'b', '2026-10-31 — test')
        other = self.worker('other')
        other.create_note('demo/working/other/scratch', 'Scratch', body='# Scratch\n\nCache invalidation.\n')
        self.assertEqual([h['path'] for h in self.s.search('cache invalidation', scope='demo')['hits']], ['demo/answer'])
        self.assertEqual([h['path'] for h in other.search('cache invalidation', scope='demo')['hits']], ['demo/working/other/scratch'])
        self.assertEqual([h['path'] for h in self.s.search('cache invalidation', scope='demo/working')['hits']], ['demo/working/other/scratch'])

    def test_exact_matches_whole_words_and_snippets_end_on_words(self):
        # Real case: exact 'D51' matched inside commit hashes.
        filler = ' '.join(f'alpha{i}' for i in range(40))
        self.s.create_note('demo/ids', 'Ids', 'ids', f'# Ids\n\n{filler} decision D51 applies. {filler}\n', 'a', 'b', '2026-10-31 — t')
        self.s.create_note('demo/hash', 'Hash', 'hash', '# Hash\n\nCommit 9ad51f0 and 3d51aa.\n', 'a', 'b', '2026-10-31 — t')
        hits = self.s.search('d51', mode='exact', scope='demo')['hits']
        self.assertEqual([h['path'] for h in hits], ['demo/ids'])
        snip = hits[0]['snippet']
        self.assertIn('[D51]', snip)
        self.assertRegex(snip, r'^…alpha\d+ ')
        self.assertRegex(snip, r' alpha\d+…$')
        self.assertEqual([h['path'] for h in self.s.search('9ad51f0', mode='exact', scope='demo')['hits']], ['demo/hash'])
        self.assertEqual(self.s.search('9ad51', mode='exact', scope='demo')['hits'], [])
        self.assertEqual([h['path'] for h in self.s.search('decision D51 applies.', mode='exact', scope='demo')['hits']], ['demo/ids'])

    # ----- log -----

    def test_log_is_append_only_and_database_stamped(self):
        with self.assertRaisesRegex(Exception, 'append-only'):
            self.s.db.execute('UPDATE events SET op = ? WHERE id = 1', ('X',))
        with self.assertRaisesRegex(Exception, 'append-only'):
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

    # ----- links, log_query, move, layers, roles, sources, leases -----

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
        self.assertEqual(self.s.check('demo')['problems'], [self.no_desc('demo')] + ["decision P02 needs exactly one '- **Status:**' line (has 0)"])
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
        self.assertEqual(self.s.check('demo')['problems'], [self.no_desc('demo')] + ["decision P02 needs exactly one '- **Status:**' line (has 0)"])
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
        self.addCleanup(s.db.close)
        s.create_note('demo/working/agent-frontend/scratch', 'Scratch')
        self.assertEqual(s._one('SELECT run_id FROM events ORDER BY id DESC LIMIT 1')[0], 'run-42')

    def test_worker_cannot_record_preference(self):
        s = Store(self.db, 'agent-frontend', 'worker')
        self.addCleanup(s.db.close)
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

    # ----- regression tests -----

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
            params = StdioServerParameters(command=sys.executable, args=['-m', 'apex_memory', 'serve', '--url', self.db, '--actor', 'smoke'])
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
        self.assertEqual(self.s.check('demo2')['problems'], [self.no_desc('demo2')])

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
        out = cli('verify', str(self.src), '--url', self.db, '--fresh')
        self.assertNotEqual(out.returncode, 0)
        self.assertIn('--fresh applies to import only', out.stderr)
        self.assertEqual(self.s._one('SELECT count(*) FROM notes')[0], 7)

    # ----- legacy formats and the ops domain -----

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
        self.assertEqual(rep['problems'], [self.no_desc('legacy')])  # linked-only note counts as indexed; D1/D2 not yet on the Status-line rule
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
        # Real case (memory-ops event 199): the first Status line made every older record a new problem.
        d1 = self.s.read('legacy/decisions', heading='D1 — First, 2026-09-13')
        self.assertTrue(self.s.patch_section('append', '- **Status:** accepted', section_id=d1['section_id'])['ok'])
        self.assertEqual(self.s.check('legacy')['problems'], [self.no_desc('legacy'), "decision D2 needs exactly one '- **Status:**' line (has 0)"])  # existing debt
        top = self.s.read('legacy/decisions', heading='Decisions')
        info = self.refused('check_failed', self.s.patch_section, 'append', '# D3 — Third, 2026-10-12\n\nNo status line.', section_id=top['section_id'])
        self.assertEqual(info['problems'], ["decision D3 needs exactly one '- **Status:**' line (has 0)"])  # a new record is checked

    def test_import_resolves_links_from_other_domains_and_empty_anchors(self):
        self.import_extra('first', {'index.md': '# First\n\n## Catalog\n\n| [[first/page]] | p |\n',
                                    'page.md': '# Page\n\nSee [[second/notes#Details]] and [[first/page#]].\n'})
        self.assertEqual(self.s.check('first')['problems'], [self.no_desc('first')])  # 'second' is not in the store yet: an external link
        self.import_extra('second', {'index.md': '# Second\n\n## Catalog\n\n| [[second/notes]] | n |\n',
                                     'notes.md': '# Notes\n\n## Details\n\nHere.\n'})
        self.assertEqual(self.s.check('first')['problems'], [self.no_desc('first')])

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

    def test_batch_refusal_redacts_nested_content_and_body(self):
        self.s.create_domain('memory-ops', 'Memory ops', 'Test refusals')
        r = self.s.read('demo/topic', heading='Rules')
        ops = [{'tool': 'create_note', 'args': {'path': 'demo/working/test/new', 'title': 'New', 'body': 'private body marker'}},
               {'tool': 'patch_section', 'args': {'op': 'replace', 'content': 'private content marker',
                                                'if_version': r['version'] + 1, 'section_id': r['section_id']}}]
        self.refused('version_conflict', self.s.batch, ops)
        event = self.s._one("SELECT body FROM events WHERE domain = 'memory-ops' AND op = 'tool:refused'")[0]
        self.assertNotIn('private body marker', event)
        self.assertNotIn('private content marker', event)
        args = json.loads(event)['args']['ops']
        self.assertEqual(args[0]['args']['body'], '<19 chars>')
        self.assertEqual(args[1]['args']['content'], '<22 chars>')
        self.assertFalse(self.s._one("SELECT 1 FROM notes WHERE path = 'demo/working/test/new'"))

    # ----- MCP server -----

    def test_mcp_smoke(self):
        from mcp import Client, StdioServerParameters

        async def run():
            params = StdioServerParameters(command=sys.executable, args=['-m', 'apex_memory.server', '--url', self.db, '--actor', 'smoke', '--role', 'owner'])
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
        path = backup(self.s.db, to, keep=14)
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

    # ----- searching every domain, the ops domain name, schema versions -----

    def test_regex_inline_flags_keep_multiline_matching(self):
        hits = self.s.search('(?i)^the revert detector', mode='regex', scope='demo/topic')['hits']
        self.assertEqual([h['path'] for h in hits], ['demo/topic'])
        self.assertIn('[The revert detector]', hits[0]['snippet'])

    def test_search_scope_preserves_sqlite_like_semantics(self):
        for path in ('API/guide', 'Ä/guide', 'api_extra/guide', 'api%/guide', r'api\back/guide'):
            self.s.create_note('demo/' + path, 'Scope probe', 'scopeprobe', '# Probe\n\nscopeprobe\n', 'a', 'b', '2026-10-30 — t')
        for mode in ('bm25', 'exact', 'regex'):
            for scope, expected in [('demo/api', {'demo/API/guide'}), ('demo/ä', set()),
                                    ('demo/api_', {'demo/api%/guide'}),
                                    ('demo/api%', {'demo/API/guide', 'demo/api_extra/guide', 'demo/api%/guide', r'demo/api\back/guide'}),
                                    (r'demo/api\back', {r'demo/api\back/guide'})]:
                with self.subTest(mode=mode, scope=scope):
                    paths = {h.get('path') for h in self.s.search('scopeprobe', mode=mode, scope=scope)['hits']}
                    self.assertEqual(paths, expected)

    def test_keyword_search_normalizes_diacritics_in_all_indexes(self):
        self.s.create_note('demo/accents', 'Café', 'A café', '# Café\n\nCafé résumé cafe\u0301 æ ộ\n', 'a', 'b', '2026-10-30 — t')
        event = self.s.log_append('demo', 'VISIT CAFÉ', ['demo/accents'], 'café', 'résumé')
        for query in ('cafe', 'CAFÉ', 'cafe\u0301'):
            hits = self.s.search(query, scope='demo', limit=100)['hits']
            self.assertEqual({h['kind'] for h in hits}, {'note', 'section', 'event'})
            self.assertIn(event['event_id'], [h.get('event_id') for h in hits])
        self.assertTrue(self.s.search('resume', scope='demo/accents')['hits'])
        # unicode61 does not expand ligatures or strip multiple diacritics from a precomposed letter.
        self.assertEqual(self.s.search('ae', scope='demo/accents')['hits'], [])
        self.assertEqual(self.s.search('o', scope='demo/accents')['hits'], [])

    def test_copy_store_refuses_itself_before_starting_transactions(self):
        alias = open_backend(self.db)
        self.addCleanup(alias.close)
        before = self.snapshot(self.s)
        for src in (self.s.db, alias):
            with self.subTest(same_connection=src is self.s.db):
                with mock.patch.object(self.s.db, 'transaction', side_effect=AssertionError('transaction started')):
                    with self.assertRaisesRegex(ValueError, 'itself'):
                        copy_store(src, self.s.db)
        self.assertEqual(self.snapshot(self.s), before)

    def test_search_without_scope_covers_every_domain(self):
        self.import_extra('other', {'index.md': '# Other\n\n## Catalog\n\n| [[other/page]] | p |\n',
                                    'page.md': '# Page\n\nThe revert detector, again.\n'})
        w = self.worker('bot')
        w.create_note('other/working/bot/mine', 'Mine', body='# Mine\n\nrevert detector scratch\n')
        paths = {h.get('path') for h in w.search('revert detector')['hits']}
        self.assertTrue({'demo/topic', 'other/page', 'other/working/bot/mine'} <= paths, paths)
        self.assertNotIn('other/working/bot/mine', {h.get('path') for h in self.s.search('revert detector')['hits']})
        self.assertEqual({h['path'] for h in self.s.search('again', mode='exact')['hits']}, {'other/page'})

    def test_ops_domain_is_configurable(self):
        self.import_extra('feedback', {'index.md': '# Feedback\n\n## Catalog\n\n| [[feedback/log]] | the log |\n',
                                       'log.md': '# Feedback log\n'})
        s = Store(self.db, 'test', ops_domain='feedback')
        self.addCleanup(s.db.close)
        self.refused('not_found', s.read, 'demo/nowhere')
        self.assertEqual(s._one("SELECT count(*) FROM events WHERE domain = 'feedback' AND op = 'tool:refused'")[0], 1)
        self.refused('not_found', self.s.read, 'demo/nowhere')  # the default ops domain, memory-ops, is not in this store
        self.assertEqual(self.s._one("SELECT count(*) FROM events WHERE op = 'tool:refused'")[0], 1)

    def test_newer_schema_is_refused(self):
        self.s.db.set_schema_version(99)
        with self.assertRaisesRegex(RuntimeError, 'schema version 99'):
            Store(self.db, 'test')

    def test_init_cli(self):
        url = self.new_url('init')
        first, again = cli('init', '--url', url), cli('init', '--url', url)
        self.assertEqual((first.returncode, again.returncode), (0, 0), first.stderr + again.stderr)
        store_id = re.search(r'store (\S+)', first.stdout).group(1)
        self.assertIn(store_id, again.stdout)
        password = urllib.parse.urlsplit(url).password
        if password:
            self.assertNotIn(password, first.stdout)

    # ----- moving a store: copy into a new store on this backend -----

    def snapshot(self, s):
        notes = [r['path'] for r in s._q('SELECT path FROM notes ORDER BY path')]
        tables = {t: sorted(tuple(map(str, r)) for r in s._q(f'SELECT * FROM {t}'))
                  for t in ('notes', 'sections', 'links', 'decisions', 'status_items', 'sources', 'note_sources', 'preferences', 'events')}
        return {'pages': {p: s.render_note(p) for p in notes}, 'tables': tables, 'store_id': s.store_id,
                'brief': s.brief('demo')['brief'].split('\n', 1)[1]}

    def test_move_a_sqlite_store_with_the_cli(self):
        src = Store(str(self.dir / 'old.db'), 'test')
        self.addCleanup(src.db.close)
        import_domain(src, self.src)
        src.preference('add', text='Prefer numbers', owner_quote='numbers please')
        src.status_item('demo', 'add', kind='open', text='Moved item')
        before = self.snapshot(src)
        url = self.new_url('moved')
        out = cli('import', str(self.dir / 'old.db'), '--url', url)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)['notes'], 7)
        dst = Store(url, 'test')
        self.addCleanup(dst.db.close)
        self.assertEqual(self.snapshot(dst), before)
        self.assertEqual([h['path'] for h in dst.search('Immutable', scope='demo/sources')['hits']], ['demo/sources/2026-10-01-snapshot'])
        # new rows get new ids after the copied ones
        item = dst.status_item('demo', 'add', kind='next', text='After the move')['item_id']
        self.assertGreater(item, max(r[0] for r in src._q('SELECT id FROM status_items')))
        self.assertTrue(dst.create_note('demo/after', 'After', 'written after the move', '# After\n', 'a', 'b', '2026-10-30 — t')['ok'])
        self.assertIn('copy into a new, empty store', cli('import', str(self.dir / 'old.db'), '--url', url).stderr)

    def test_backup_restores_into_a_new_store(self):
        to = self.dir / 'restore'
        path = backup(self.s.db, to, keep=1)
        before = self.snapshot(self.s)
        url = self.new_url('restored')
        out = cli('import', f'sqlite://{path}', '--url', url)
        self.assertEqual(out.returncode, 0, out.stderr)
        dst = Store(url, 'test')
        self.addCleanup(dst.db.close)
        self.assertEqual(self.snapshot(dst), before)


@unittest.skipUnless(PG_URL, 'set APEX_MEMORY_TEST_POSTGRES_URL to run the tests on Postgres')
class PostgresMemoryTest(MemoryTest):
    def test_diacritic_upgrade_reindexes_the_resolved_store_schema(self):
        from apex_memory.backends.base import SQL
        url = self.new_url('upgrade')
        legacy = open_backend(url)
        self.addCleanup(legacy.close)
        with legacy.transaction():
            legacy.run_script((SQL / 'postgres' / '001_schema.sql').read_text())
            legacy.set_schema_version(1)
            legacy.execute("INSERT INTO meta VALUES ('store_id', 'legacy')")
            legacy.insert('notes', {'path': 'demo/cafe', 'domain': 'demo', 'title': 'Café'})
        schema = legacy.one('SELECT current_schema()')[0]
        scratch = 'scratch_' + uuid.uuid4().hex[:12]
        legacy.execute(f'CREATE SCHEMA {scratch}')
        self.addCleanup(self.drop_schema, scratch)
        alias = open_backend(url)
        self.addCleanup(alias.close)
        alias.execute("SELECT set_config('search_path', ?, false)", (f'{scratch},{schema}',))
        with alias.transaction():
            alias.migrate()
            self.assertEqual(alias.one('SELECT current_schema()')[0], scratch)
        self.assertEqual([r['path'] for r in legacy.search('notes', 'cafe')], ['demo/cafe'])
        self.assertEqual(legacy.schema_version(), len(list((SQL / 'postgres').glob('*.sql'))))

    def test_search_path_aliases_share_the_writer_lock(self):
        import psycopg
        schema = self.s.db.one('SELECT current_schema()')[0]
        scratch = 'scratch_' + uuid.uuid4().hex[:12]
        self.s.db.execute(f'CREATE SCHEMA {scratch}')
        self.addCleanup(self.drop_schema, scratch)
        alias = open_backend(self.db)
        self.addCleanup(alias.close)
        alias.execute("SELECT set_config('search_path', ?, false)", (f'{scratch},{schema}',))
        alias.execute("SET lock_timeout = '200ms'")
        self.assertEqual(alias.one("SELECT value FROM meta WHERE key = 'store_id'")[0], self.s.store_id)
        with self.s.db.transaction():
            with self.assertRaises(psycopg.errors.LockNotAvailable):
                with alias.transaction():
                    pass

    def new_url(self, name='test'):
        import psycopg
        schema = f'{name}_{uuid.uuid4().hex[:12]}'
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(f'CREATE SCHEMA {schema}')
        self.addCleanup(self.drop_schema, schema)
        return PG_URL + ('&' if '?' in PG_URL else '?') + f'options=-csearch_path%3D{schema}'

    def drop_schema(self, schema):
        import psycopg
        with psycopg.connect(PG_URL, autocommit=True) as c:
            c.execute(f'DROP SCHEMA {schema} CASCADE')

    def test_location_hides_the_password(self):
        self.assertNotIn('password', self.s.location)
        self.assertIn('search_path', self.s.location)


class PortabilityTest(unittest.TestCase):
    """The store, importer and server reach the database only through the Backend interface."""

    TOKENS = [r'\bfts5\b', r'\bMATCH\b', r'\bbm25\(', r'\bsnippet\(', r'\bPRAGMA\b', r'\bVACUUM\b', r'\bsqlite3?\b', r'\blastrowid\b',
              r'\browid\b', r'(?<!\.)\bstrftime\(', r'\bjson_tree\b', r'\binstr\(', r'\bregexp\(', r'\bINSERT OR\b', r'\bON CONFLICT\b',
              r'\bRETURNING\b', r'::', r'\btsvector\b', r'\btsquery\b', r'\bts_rank\b', r'\bILIKE\b', r'\bpsycopg\b', r'\bpg_\w+',
              r'\bto_char\b', r'\bstrpos\(', r'\bSERIAL\b']

    @classmethod
    def dialect_sql(cls, source):
        literals = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.append(node.value)
            elif isinstance(node, ast.JoinedStr):
                literals.append(''.join(v.value if isinstance(v, ast.Constant) else '?' for v in node.values))
        tokens = cls.TOKENS + [r'\bifnull\s*\(', r'\bcollate\s+nocase\b']
        return [sql for sql in literals if re.match(r'\s*(select|insert|update|delete|create|alter|drop|with|pragma|vacuum)\b', sql, re.I)
                and any(re.search(tok, sql, re.I) for tok in tokens)]

    def test_no_database_specific_sql(self):
        for name in ('store.py', 'importer.py', 'server.py'):
            text = (ROOT / 'apex_memory' / name).read_text()
            found = [tok for tok in self.TOKENS if re.search(tok, text)]
            self.assertEqual(found, [], f'{name} contains database-specific tokens')
            self.assertEqual(self.dialect_sql(text), [], f'{name} contains database-specific SQL')

    def test_registry(self):
        self.assertEqual(type(open_backend(str(pathlib.Path(tempfile.mkdtemp()) / 'x.db'))).__name__, 'SqliteBackend')
        with self.assertRaisesRegex(ValueError, 'unknown database scheme'):
            open_backend('mysql://localhost/x')

    def test_sql_lint_rejects_dialect_constructs(self):
        for sql in ('insert into notes (title) values (?) returning id', 'SELECT IFNULL(title, ?) FROM notes',
                    'SELECT title FROM notes ORDER BY title COLLATE NOCASE'):
            with self.subTest(sql=sql):
                self.assertTrue(self.dialect_sql('db.execute(' + repr(sql) + ')'))
        self.assertTrue(self.dialect_sql('db.execute(f"insert into {table} values (?) returning id")'))
        self.assertFalse(self.dialect_sql('rx.match(value)\ntext = "SQLite file"\ndb.execute("SELECT coalesce(title, ?) FROM notes")'))


if __name__ == '__main__':
    unittest.main()
