"""apex-memory: a knowledge store for AI agents, served over MCP.

  apex-memory init                                   create the store (or show the existing one)
  apex-memory serve --actor claude-code --role owner the MCP stdio server
  apex-memory import ~/notes/project [--fresh]       import a folder of markdown notes as domain 'project'
  apex-memory import ~/old/memory.db                 copy a whole store (a SQLite file or URL, e.g. a backup) into a new one
  apex-memory verify ~/notes/project                 check every imported page renders back identically
  apex-memory brief project | check project
  apex-memory backup [--to ~/.apex-memory/backups] [--keep 14]

The database is --url, else $APEX_MEMORY_URL, else ~/.apex-memory/memory.db (SQLite).
URLs: sqlite:///absolute/path.db or a file path; postgresql://user:password@host/database (apex-memory[postgres]).
"""
import argparse, json, pathlib, sys

from .backends import backup, copy_store, default_url, open_backend
from .importer import import_domain, verify_domain
from .server import main as serve
from .store import OPS_DOMAIN, Refused, Store


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['init', 'serve', 'import', 'verify', 'brief', 'check', 'backup'])
    p.add_argument('target', nargs='?', help='folder or store (import), folder (verify) or domain (brief, check)')
    p.add_argument('--url', default=default_url(), help='database URL (default: $APEX_MEMORY_URL or ~/.apex-memory/memory.db)')
    p.add_argument('--fresh', action='store_true', help='import: delete and recreate the database (SQLite files named *test* only)')
    p.add_argument('--to', default='~/.apex-memory/backups')
    p.add_argument('--keep', type=int, default=14)
    p.add_argument('--actor', default='cli')
    p.add_argument('--role', choices=['owner', 'worker'], help='default: worker for serve, owner otherwise')
    p.add_argument('--run-id', default=None)
    p.add_argument('--ops-domain', default=OPS_DOMAIN)
    a = p.parse_args()
    if a.fresh and a.command != 'import':
        sys.exit('--fresh applies to import only')
    if a.command == 'serve':
        return serve(['--url', a.url, '--actor', a.actor, '--role', a.role or 'worker', '--ops-domain', a.ops_domain]
                     + (['--run-id', a.run_id] if a.run_id else []))
    if a.fresh:
        path = a.url.removeprefix('sqlite://')
        if '://' in path or 'test' not in pathlib.Path(path).name:
            sys.exit('--fresh only deletes SQLite databases whose file name contains "test"')
        for suffix in ('', '-wal', '-shm'):
            pathlib.Path(path + suffix).expanduser().unlink(missing_ok=True)
    store = Store(a.url, a.actor, a.role or 'owner', a.run_id, a.ops_domain)
    try:
        if a.command == 'init':
            print(f'store {store.store_id} at {store.location}')
        elif a.command == 'backup':
            print(backup(store.db, a.to, a.keep))
        elif a.command == 'import' and not pathlib.Path(a.target or '').expanduser().is_dir():
            path = (a.target or '').removeprefix('sqlite://')
            if '://' not in path and not pathlib.Path(path).expanduser().is_file():
                sys.exit(f'{a.target!r} is neither a folder of notes nor a store')
            src = open_backend(a.target)
            try:
                print(json.dumps(copy_store(src, store.db), indent=2))
            except ValueError as e:
                sys.exit(str(e))
            finally:
                src.close()
        elif a.command == 'import':
            print(json.dumps(import_domain(store, a.target), indent=2, ensure_ascii=False))
        elif a.command == 'verify':
            bad = verify_domain(store, a.target)
            print('\n\n'.join(bad) if bad else f'{a.target}: every page renders back identically')
            sys.exit(1 if bad else 0)
        elif a.command == 'brief':
            print(store.brief(a.target)['brief'])
        else:
            probs = store.check(a.target)['problems']
            print('\n'.join(probs) if probs else f'{a.target}: no problems found')
            sys.exit(1 if probs else 0)
    except Refused as e:
        sys.exit(json.dumps(e.info, ensure_ascii=False))
    finally:
        store.db.close()


if __name__ == '__main__':
    main()
