"""Backends by URL scheme. A new database = one module implementing base.Backend, its sql/<name>/ migrations
and one line in BACKENDS."""
import datetime, importlib, os, pathlib

from .base import Backend

BACKENDS = {  # URL scheme: (module, class)
    'sqlite': ('.sqlite', 'SqliteBackend'),
    'postgresql': ('.postgres', 'PostgresBackend'),
    'postgres': ('.postgres', 'PostgresBackend'),
}

# The logical tables in an order that satisfies the foreign keys, and those with an integer id.
TABLES = ['meta', 'notes', 'sections', 'links', 'decisions', 'status_items', 'sources', 'note_sources', 'preferences', 'leases', 'events']
ID_TABLES = {'notes', 'sections', 'status_items', 'preferences', 'events'}


def default_url() -> str:
    """$APEX_MEMORY_URL, else a SQLite file in ~/.apex-memory/."""
    return os.environ.get('APEX_MEMORY_URL') or 'sqlite://' + str(pathlib.Path('~/.apex-memory/memory.db').expanduser())


def open_backend(url: str) -> Backend:
    """A connected backend for a URL; a bare path is a SQLite file."""
    scheme = str(url).split('://', 1)[0] if '://' in str(url) else 'sqlite'
    if scheme not in BACKENDS:
        raise ValueError(f'unknown database scheme {scheme!r}; known: {", ".join(BACKENDS)}')
    module, cls = BACKENDS[scheme]
    try:
        mod = importlib.import_module(module, __name__)
    except ModuleNotFoundError as e:
        raise SystemExit(f'{scheme} needs the {e.name} package: install apex-memory[postgres]') from e
    return getattr(mod, cls)(url)


def copy_store(src: Backend, dst: Backend) -> dict:
    """Copy every row of one store into another whose schema exists and that holds no knowledge yet, ids and store
    identity included. One read transaction on the source, one write transaction on the target. Returns row counts."""
    src_id = src.one("SELECT value FROM meta WHERE key = 'store_id'")
    dst_id = dst.one("SELECT value FROM meta WHERE key = 'store_id'")
    if src is dst or (src_id and dst_id and src_id[0] == dst_id[0]):
        raise ValueError('cannot copy a store into itself; copy into a new, empty store')
    counts = {}
    with src.transaction(), dst.transaction():
        if dst.one('SELECT 1 FROM notes') or dst.one('SELECT 1 FROM events'):
            raise ValueError(f'{dst.location} already holds knowledge; copy into a new, empty store')
        dst.execute('DELETE FROM meta')
        for table in TABLES:
            rows = src.query(f'SELECT * FROM {table}')
            for r in rows:
                values = dict(r)
                if table in ID_TABLES:
                    dst.insert(table, values)
                else:
                    dst.execute(f'INSERT INTO {table} ({", ".join(values)}) VALUES ({", ".join("?" * len(values))})', list(values.values()))
            counts[table] = len(rows)
    return counts


def backup(db: Backend, to_dir, keep: int = 14) -> pathlib.Path:
    """A verified copy of the store as to_dir/memory-YYYY-MM-DD.db (a SQLite file); keeps the newest `keep`."""
    to = pathlib.Path(to_dir).expanduser()
    to.mkdir(parents=True, exist_ok=True)
    final = to / f'memory-{datetime.date.today().isoformat()}.db'
    tmp = to / f'.{final.name}.tmp'
    tmp.unlink(missing_ok=True)
    try:
        db.backup(tmp)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, final)
    for old in sorted(to.glob('memory-*.db'))[:-keep]:
        old.unlink()
    return final
