# Task completion gate

From the worktree/repo root:

```
uv run pytest
uv run ruff check src tests
uv run mypy src tests
```

Phase 0 also: `GET /health` → `{"status": "ok"}`.

Do not claim a phase done until that phase plan’s gate bullets pass. Phase N+1 starts only after Phase N is green.

See `mem:suggested_commands`, `mem:workflow`.
