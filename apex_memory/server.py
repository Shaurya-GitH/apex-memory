"""MCP stdio server exposing the memory tools to agents (Claude Code, Codex, any MCP client).

  apex-memory serve --actor claude-code --role owner

Roles are declared here, at registration: the owner's own sessions are owner; agents you run on the owner's behalf
are workers.
"""
import argparse, functools

from mcp.server.mcpserver import MCPServer

from .backends import default_url
from .store import OPS_DOMAIN, TOOLS, Refused, Store


def build(store):
    server = MCPServer('apex-memory', instructions=(
        'At the start and when the subject of the work changes, call list_domains and choose the domains whose description '
        'matches the work by subject (not repository or tool), then brief(domain). ' + (
            'If none matches and the work will produce knowledge worth keeping, ask the owner once, at the start of that work: '
            'create a knowledge base for <topic> (propose a name and a one-line description) or not. Yes: create_domain, then brief. '
            '"Never": preference(add, no domain, the owner\'s words as quote, text "No knowledge base for <topic>"); list_domains '
            'returns these preferences, so never ask again for that topic. "Not now": record nothing; do not ask again this session. '
            if store.role == 'owner' else
            "If none matches, do not create a domain or record a preference: report the gap to the owner's session. ") +
        f'Memory store {store.store_id} at {store.location} (actor {store.actor}, role {store.role}). '
        'Search, then read one section; edit with patch_section using the version read returned '
        '(replace accepts exactly what read returned). Writes are all-or-nothing and run the checks; a refusal says why. '
        'Log meaningful work with log_append; status changes with status_item; decision status only with set_decision_status. '
        f'Scratch notes go under <domain>/working/{store.actor}/ (a worker writes only there); ' + (
            "record the owner's preferences with preference, quoting the owner; " if store.role == 'owner' else '') +
        'take a lease before a long edit.'))
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
    p.add_argument('--url', default=default_url(), help='database URL (default: $APEX_MEMORY_URL or ~/.apex-memory/memory.db)')
    p.add_argument('--actor', default='unknown')
    p.add_argument('--role', choices=['owner', 'worker'], default='worker')
    p.add_argument('--run-id', default=None)
    p.add_argument('--ops-domain', default=OPS_DOMAIN, help='domain that receives automatic records of refusals and errors')
    args = p.parse_args(argv)
    try:
        store = Store(args.url, args.actor, args.role, args.run_id, args.ops_domain)
    except (ConnectionError, RuntimeError) as e:  # unreachable, newer schema, failed safety backup: say why, on stderr
        raise SystemExit(f'apex-memory: {e}')
    build(store).run('stdio')


if __name__ == '__main__':
    main()
