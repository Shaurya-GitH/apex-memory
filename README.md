# apex-memory

A knowledge store for AI agents, served over MCP. The full README is written separately.

```sh
uv tool install apex-memory          # or: pip install apex-memory
apex-memory init                     # creates ~/.apex-memory/memory.db
apex-memory serve --actor claude-code --role owner
```

Set `APEX_MEMORY_URL` (`sqlite:///path/to/memory.db` or `postgresql://…`) to choose the database;
Postgres needs `apex-memory[postgres]`.
