# SignalBench (repo: macrocite)

Research console: ingest filings/prices → extract versioned LLM signals → eval CI → backtest → dashboard.
Not a trading bot. No broker APIs. Equities only.

**Serena:** this MCP process is global. After `activate_project`, verify cwd is `C:\Users\samin\Documents\GitHub\macrocite`. If cwd is SolomindLM or anything else, re-activate before any read/write/memory/symbol call. Never `write_memory` until cwd is confirmed.

Entry memories:
- stack, commands, gate: `mem:tech_stack`, `mem:suggested_commands`, `mem:task_completion`
- code style and schema invariants: `mem:conventions`, `mem:schema`
- how to implement phases: `mem:workflow`

Layout:
- `src/signalbench/` — FastAPI (`api/main.py`), Typer CLI (`cli.py`), SQLModel (`db/`), ingest (`ingest/`)
- `tests/` — pytest; SQLite `create_all` via `tests/conftest.py` (not Alembic)
- `alembic/versions/` — Postgres migrations (`0001_phase0`, …)
- `docs/superpowers/plans/` — phase plans; execute one phase at a time
- `data/watchlist.yaml` — 20-ticker seed
- Isolated work in `.worktrees/<phase>/` (gitignored via `.git/info/exclude`). Do not register worktrees as Serena projects.

Phases 0–2 are on `main`. Phase 2 eval harness: git labels, metrics, 5-point CI gate, `eval_runs` (Alembic `0004_eval_runs`), `python -m signalbench.eval` calls Together on frozen `raw_text` (pytest injects a fake LLM). CI needs GitHub secret `TOGETHER_API_KEY`. Next: Phase 3 backtest.
