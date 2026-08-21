# Commands (Windows / PowerShell)

Package/test (repo or worktree root):
- `uv sync`
- `uv run pytest`
- `uv run pytest tests/test_foo.py -v`
- `uv run ruff check src tests`
- `uv run mypy src tests`

API: `uv run uvicorn signalbench.api.main:app --reload`
CLI: `uv run signalbench --help` (`seed-watchlist`, `ingest filings`, `ingest prices`)

Postgres: `docker compose up -d` (needs Docker Desktop running). Env: `.env.example` → `DATABASE_URL`, `SEC_USER_AGENT`.

Git worktrees:
- `git worktree add .worktrees/<name> -b feat/<name>`
- `.worktrees/` is ignored in `.git/info/exclude`, not `.gitignore`

Serena: from project root, `serena memories check` after memory edits.

See `mem:task_completion` for the done-gate.
