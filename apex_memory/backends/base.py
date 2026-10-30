"""The one way the store reaches a database.

The store writes neutral SQL: '?' placeholders, standard functions (lower, coalesce, count, max), LIKE ... ESCAPE,
'||', LIMIT. Everything else a database does differently lives behind these methods.
"""
import abc, contextlib, pathlib

SQL = pathlib.Path(__file__).with_name('sql')


class Backend(abc.ABC):
    name: str            # the folder under sql/ holding this backend's ordered migrations
    location: str        # where the store lives, safe to show (no password)

    @abc.abstractmethod
    def __init__(self, url: str): """Connect to the database the URL names."""

    @abc.abstractmethod
    def transaction(self) -> contextlib.AbstractContextManager:
        """One write transaction; only one writer at a time across every process using the database.
        Commits on success, rolls back on any exception."""

    @abc.abstractmethod
    def query(self, sql: str, args=()) -> list:
        """Rows (indexable by position and by column name; dict(row) works) for neutral SQL with '?' placeholders."""

    def one(self, sql: str, args=()):
        rows = self.query(sql, args)
        return rows[0] if rows else None

    @abc.abstractmethod
    def execute(self, sql: str, args=()) -> int:
        """Run one neutral statement; returns the number of rows it changed."""

    @abc.abstractmethod
    def insert(self, table: str, values: dict) -> int:
        """Insert one row into a table with an integer id; returns the id (given or generated)."""

    @abc.abstractmethod
    def search(self, table: str, query: str, where: str = '1 = 1', args=(), exact: bool = False) -> list:
        """Rows of table (aliased t) matching query in its full-text index and the neutral `where` condition.
        Ranked (exact=False): any word of query; each row adds `score` (lower is better) and `snippet` (matches in
        [brackets]). exact=True (sections only): rows whose body contains query, ignoring case; no score or snippet.
        The indexes are kept up to date by the database itself (triggers or index expressions)."""

    @abc.abstractmethod
    def regex(self, expr: str) -> str:
        """A condition that is true when `expr` matches the regular expression given as the next '?' argument."""

    @abc.abstractmethod
    def prefix(self, expr: str) -> str:
        """Match the next '?' path-prefix LIKE pattern, folding ASCII case only; backslash is literal."""

    @abc.abstractmethod
    def now(self, minutes: int = 0) -> str:
        """SQL for the database's current UTC time plus `minutes`, as text 'YYYY-MM-DDTHH:MM:SS.mmmZ'."""

    @abc.abstractmethod
    def schema_version(self) -> int: """The number of migrations applied; 0 for an empty database."""

    @abc.abstractmethod
    def set_schema_version(self, version: int): ...

    @abc.abstractmethod
    def run_script(self, sql: str):
        """Run one migration file (several statements, no arguments)."""

    def migrate(self) -> bool:
        """Apply the pending migrations in order, inside transaction(). Returns True when the database was empty."""
        files = sorted((SQL / self.name).glob('*.sql'))
        version = self.schema_version()
        if version > len(files):
            raise RuntimeError(f'{self.location} has schema version {version}; this apex-memory knows {len(files)}: upgrade it')
        for f in files[version:]:
            self.run_script(f.read_text())
        if version < len(files):
            self.set_schema_version(len(files))
        return version == 0

    @abc.abstractmethod
    def backup(self, destination: pathlib.Path):
        """Write a complete, verified copy of the store to a new SQLite file at destination."""

    @abc.abstractmethod
    def close(self): ...
