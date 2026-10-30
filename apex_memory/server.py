"""MCP stdio server exposing the memory tools to Claude Code and Codex.

  apex-memory serve --db ~/.apex-memory/memory.db --actor claude-code --role owner

Roles are declared here, at registration: the owner's own sessions are owner; agents you run are workers.
"""
import argparse, functools, pathlib

from mcp.server.mcpserver import MCPServer

from .store import TOOLS, Refused, Store

DEFAULT_DB = pathlib.Path('~/.apex-memory/memory.db').expanduser()


def build(store):
    server = MCPServer('apex-memory', instructions=(
        f'Memory store {store.store_id} at {store.path} (actor {store.actor}, role {store.role}). '
        'Start with brief(domain); search, then read one section; edit with patch_section using the version read returned '
        '(replace accepts exactly what read returned). Writes are all-or-nothing and run the checks; a refusal says why. '
        'Log meaningful work with log_append; status changes with status_item; decision status only with set_decision_status. '
        f'Scratch notes go under <domain>/working/{store.actor}/ (a worker writes only there); record the owner\'s preferences '
        'with preference, quoting the owner; take a lease before a long edit.'))
    for name in TOOLS:
        server.add_tool(_safe(getattr(store, name)), name=name, description=getattr(Store, name).__doc__)
    return server


def _safe(method):
    @functools.wraps(method)
    def tool(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except Refused as e:
            return e.info
    return tool


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--db', default=str(DEFAULT_DB))
    p.add_argument('--actor', default='unknown')
    p.add_argument('--role', choices=['owner', 'worker'], default='worker')
    p.add_argument('--run-id', default=None)
    args = p.parse_args(argv)
    build(Store(args.db, args.actor, args.role, args.run_id)).run('stdio')


if __name__ == '__main__':
    main()
