"""SQLite: one file, WAL, FTS5 with bm25 ranking, a Python REGEXP function, VACUUM INTO backups.

URL: sqlite:///absolute/path.db, sqlite://relative/or/~/path.db, or a bare file path.
"""
import contextlib, functools, os, pathlib, re, sqlite3

from .base import Backend

FTS = {  # table: (FTS5 table, bm25 column weights)
    'notes': ('notes_fts', '4.0, 2.0'),
    'sections': ('sections_fts', '3.0, 1.0'),
    'events': ('events_fts', '2.0, 1.0'),
}


@functools.lru_cache(maxsize=64)
def _rx(pattern):
    return re.compile(pattern, re.M)


def _regexp(pattern, text):
    return text is not None and _rx(pattern).search(text) is not None


def check_integrity(path):
    db = sqlite3.connect(path)
    try:
        ok = db.execute('PRAGMA integrity_check').fetchone()[0]
    finally:
        db.close()
    if ok != 'ok':
        raise RuntimeError(f'{path} failed integrity_check: {ok}')


class SqliteBackend(Backend):
    name = 'sqlite'

    def __init__(self, url):
        path = str(url).removeprefix('sqlite://')
        self.location = os.path.abspath(os.path.expanduser(path))
        pathlib.Path(self.location).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.location, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.create_function('regexp', 2, _regexp, deterministic=True)

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')  # takes the write lock now: one writer across processes
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def query(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def execute(self, sql, args=()):
        return self.db.execute(sql, args).rowcount

    def insert(self, table, values):
        cols = ', '.join(values)
        return self.db.execute(f'INSERT INTO {table} ({cols}) VALUES ({", ".join("?" * len(values))})', list(values.values())).lastrowid

    def search(self, table, query, where='1 = 1', args=(), exact=False):
        if exact:
            if len(query) >= 3:  # the trigram index, like the fallback below, ignores case
                cond, q = 't.id IN (SELECT rowid FROM sections_exact WHERE sections_exact MATCH ?)', '"' + query.replace('"', '""') + '"'
            else:
                cond, q = 'instr(lower(t.body), lower(?)) > 0', query
            return self.query(f'SELECT t.* FROM sections t WHERE {cond} AND {where}', (q, *args))
        terms = re.findall(r'\w+', query)
        if not terms:
            return []
        fts, weights = FTS[table]
        return self.query(f"SELECT t.*, bm25({fts}, {weights}) AS score, snippet({fts}, -1, '[', ']', '…', 12) AS snippet "
                          f'FROM {fts} JOIN {table} t ON t.id = {fts}.rowid WHERE {fts} MATCH ? AND {where}',
                          (' OR '.join('"%s"' % t for t in terms), *args))

    def regex(self, expr):
        return f'regexp(?, {expr})'

    def prefix(self, expr):
        return f'{expr} LIKE ?'

    def now(self, minutes=0):
        return f"strftime('%Y-%m-%dT%H:%M:%fZ', 'now', '{int(minutes):+d} minutes')"

    def schema_version(self):
        return self.db.execute('PRAGMA user_version').fetchone()[0]

    def set_schema_version(self, version):
        self.db.execute(f'PRAGMA user_version = {int(version)}')

    def run_script(self, sql):
        buf = ''  # statement by statement: executescript would commit the open transaction
        for line in sql.splitlines(keepends=True):
            if line.lstrip().startswith('--') and not buf.strip():
                continue
            buf += line
            if sqlite3.complete_statement(buf):
                self.db.execute(buf)
                buf = ''

    def backup(self, destination):
        # Its own connection: VACUUM INTO cannot run inside a transaction, and the pre-migration backup runs while
        # this connection holds the write lock (WAL lets the reader see the last commit).
        db = sqlite3.connect(self.location, isolation_level=None)
        try:
            db.execute('PRAGMA busy_timeout=5000')
            db.execute('VACUUM INTO ?', (str(destination),))
        finally:
            db.close()
        check_integrity(destination)

    def close(self):
        self.db.close()
