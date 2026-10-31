"""Postgres (psycopg 3): tsvector GIN indexes ranked with ts_rank and ts_headline, regexp_like for regular expressions,
RETURNING id, and a transaction-level advisory lock per schema for the single-writer guarantee.

URL: postgresql://user:password@host:port/database (postgres:// works too). Every libpq parameter is accepted, e.g.
?options=-csearch_path%3Dmyschema to keep the store in its own schema. Needs `pip install 'apex-memory[postgres]'`.
"""
import contextlib, functools, math, re

import psycopg
from psycopg import conninfo

from .base import Backend

FTS = {  # table: (indexed document, text for the snippet, ts_rank weights {D, C, B, A}); the documents match sql/postgres
    'notes': ("setweight(to_tsvector('simple', unicode61_fold(coalesce(t.title, ''))), 'A') || setweight(to_tsvector('simple', unicode61_fold(coalesce(t.summary, ''))), 'B')",
              "coalesce(t.title, '') || ' … ' || coalesce(t.summary, '')", '{0, 0, 0.5, 1}'),
    'sections': ("setweight(to_tsvector('simple', unicode61_fold(coalesce(t.heading, ''))), 'A') || setweight(to_tsvector('simple', unicode61_fold(t.body)), 'B')",
                 "coalesce(t.heading, '') || ' … ' || t.body", '{0, 0, 0.33, 1}'),
    'events': ("setweight(to_tsvector('simple', unicode61_fold(t.op)), 'A') || setweight(to_tsvector('simple', unicode61_fold(coalesce(t.body, ''))), 'B')",
               "t.op || ' … ' || coalesce(t.body, '')", '{0, 0, 0.5, 1}'),
}
NORMALIZATION = 32  # ts_rank: rank / (rank + 1), saturating like bm25 (closest to it on a real store)
HEADLINE = 'StartSel=[, StopSel=], MaxWords=12, MinWords=4, ShortWord=0, MaxFragments=1, FragmentDelimiter=…'
NOW = """to_char(clock_timestamp() AT TIME ZONE 'UTC' + interval '{} minutes', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')"""


@functools.lru_cache(maxsize=512)
def _sql(sql):
    """Neutral '?' placeholders to psycopg's %s: '%' doubled, '?' converted outside quoted strings and identifiers."""
    return ''.join(p.replace('%', '%%') if i % 2 else p.replace('%', '%%').replace('?', '%s')
                   for i, p in enumerate(re.split(r"""('[^']*'|"[^"]*")""", sql)))


class Row(tuple):
    """A row indexable by position and by column name, like sqlite3.Row."""
    def __new__(cls, values, index):
        row = super().__new__(cls, values)
        row._index = index
        return row

    def __getitem__(self, key):
        return super().__getitem__(self._index[key] if isinstance(key, str) else key)

    def keys(self):
        return list(self._index)


def _rows(cursor):
    index = {c.name: i for i, c in enumerate(cursor.description or [])}
    return lambda values: Row(values, index)


class PostgresBackend(Backend):
    name = 'postgres'

    def __init__(self, url):
        info = conninfo.conninfo_to_dict(url)
        self.location = conninfo.make_conninfo(**{k: v for k, v in info.items() if k != 'password'})
        try:
            self.db = psycopg.connect(url, autocommit=True, row_factory=_rows)
        except psycopg.OperationalError as e:
            raise ConnectionError(f'cannot connect to {self.location}: {str(e).strip()}') from None

    @contextlib.contextmanager
    def transaction(self):
        with self.db.transaction():
            # Search-path aliases resolving the same store must acquire the same writer lock.
            self.db.execute("SELECT pg_advisory_xact_lock(hashtext(coalesce("
                            "(SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE c.oid = to_regclass('meta')), current_schema())))")
            yield

    def _run(self, sql, args):
        try:
            return self.db.execute(_sql(sql), args) if args else self.db.execute(sql)
        except psycopg.errors.InvalidRegularExpression as e:  # the contract of regex(): a refused pattern is re.error
            raise re.error(str(e).strip()) from None

    def query(self, sql, args=()):
        return self._run(sql, args).fetchall()

    def execute(self, sql, args=()):
        return self._run(sql, args).rowcount

    def insert(self, table, values):
        cols = ', '.join(values)
        new = self.query(f'INSERT INTO {table} ({cols}) VALUES ({", ".join("?" * len(values))}) RETURNING id', list(values.values()))[0][0]
        if 'id' in values:  # an explicit id (a copied store): move the identity past it
            self.db.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}))")
        return new

    def search(self, table, query, where='1 = 1', args=(), exact=False):
        if exact:  # ponytail: a sequential scan; add a pg_trgm GIN index on sections.body if stores grow large
            return self.query(f'SELECT t.* FROM sections t WHERE strpos(lower(t.body), lower(?)) > 0 AND {where}', (query, *args))
        terms = list(dict.fromkeys(w.lower() for w in re.findall(r'\w+', query)))
        if not terms:
            return []
        doc, text, weights = FTS[table]
        word = "phraseto_tsquery('simple', unicode61_fold(?))"
        # ts_rank knows no term rarity; weight each word by its BM25 inverse document frequency, as FTS5's bm25 does.
        # ponytail: one count per word and one count(*) per search; keep a statistics table if tables grow very large
        n = self.one(f'SELECT count(*) FROM {table}')[0]
        idf = [math.log((n - df + 0.5) / (df + 0.5) + 1)
               for df in (self.one(f'SELECT count(*) FROM {table} t WHERE {doc} @@ {word}', (w,))[0] for w in terms)]
        score = ' + '.join(f"{i!r} * ts_rank('{weights}', {doc}, {word}, {NORMALIZATION})" for i in idf)
        return self.query(f"SELECT t.*, -({score}) AS score, ts_headline('simple', {text}, q.q, '{HEADLINE}') AS snippet "
                          f"FROM {table} t, (SELECT {' || '.join([word] * len(terms))} AS q) q WHERE {doc} @@ q.q AND {where}",
                          (*terms, *terms, *args))

    def regex(self, expr):
        return f"regexp_like({expr}, ?, 'n')"  # line anchors, without displacing the pattern's inline flags

    def prefix(self, expr):
        upper, lower = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'
        return f"translate({expr}, '{upper}', '{lower}') LIKE translate(?, '{upper}', '{lower}') ESCAPE ''"

    def now(self, minutes=0):
        return NOW.format(int(minutes))

    def schema_version(self):
        if self.one("SELECT to_regclass('schema_version')")[0] is None:
            return 0
        return self.one('SELECT max(version) FROM schema_version')[0] or 0

    def set_schema_version(self, version):
        self.db.execute('CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())')
        self.db.execute('INSERT INTO schema_version (version) VALUES (%s)', (version,))

    def migrate(self):
        # An alias search path can resolve the store in a later schema. New functions and
        # indexes in an upgrade belong beside its tables, not in the first empty schema.
        original = self.one("SELECT current_setting('search_path')")[0]
        schema = self.one("SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                          "WHERE c.oid = to_regclass('meta')")
        if schema:
            self.db.execute("SELECT set_config('search_path', quote_ident(%s), true)", (schema[0],))
        fresh = super().migrate()
        if schema:
            self.db.execute("SELECT set_config('search_path', %s, true)", (original,))
        return fresh

    def run_script(self, sql):
        self.db.execute(sql)

    def backup(self, destination):
        """A logical copy into a new SQLite file: portable, integrity-checked, restored with `apex-memory import`."""
        from . import copy_store
        from .sqlite import SqliteBackend, check_integrity
        dst = SqliteBackend(destination)
        try:
            with dst.transaction():
                dst.migrate()
            copy_store(self, dst)
        finally:
            dst.close()
        check_integrity(destination)

    def close(self):
        self.db.close()
