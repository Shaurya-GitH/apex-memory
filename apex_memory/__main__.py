"""Memory store CLI.

  python -m apex_memory import ~/dev/kb/office-ai --db var/memory-test.db [--fresh]
  python -m apex_memory verify ~/dev/kb/office-ai --db var/memory-test.db
  python -m apex_memory brief office-ai --db …        python -m apex_memory check office-ai --db …
  python -m apex_memory backup --db … [--to ~/.apex-memory/backups] [--keep 14]
  python -m apex_memory serve --db … --actor claude-code --role owner
"""
import argparse, json, pathlib, sys

from .importer import import_domain, verify_domain
from .server import DEFAULT_DB, main as serve
from .store import Refused, Store, backup


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['import', 'verify', 'brief', 'check', 'backup', 'serve'])
    p.add_argument('target', nargs='?', help='source folder (import, verify) or domain (brief, check)')
    p.add_argument('--db', default=str(DEFAULT_DB))
    p.add_argument('--fresh', action='store_true', help='import: delete and recreate the database (test databases only)')
    p.add_argument('--to', default='~/.apex-memory/backups')
    p.add_argument('--keep', type=int, default=14)
    p.add_argument('--actor', default='cli')
    p.add_argument('--role', choices=['owner', 'worker'], help='default: worker for serve (as the MCP server), owner otherwise')
    a = p.parse_args()
    if a.fresh and a.command != 'import':
        sys.exit('--fresh applies to import only')
    if a.command == 'serve':
        return serve(['--db', a.db, '--actor', a.actor, '--role', a.role or 'worker'])
    if a.command == 'backup':
        return print(backup(a.db, a.to, a.keep))
    if a.fresh:
        if 'test' not in pathlib.Path(a.db).name:
            sys.exit('--fresh only deletes databases whose file name contains "test"')
        for suffix in ('', '-wal', '-shm'):
            pathlib.Path(a.db + suffix).unlink(missing_ok=True)
    store = Store(a.db, a.actor, a.role or 'owner')
    try:
        if a.command == 'import':
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


if __name__ == '__main__':
    main()
