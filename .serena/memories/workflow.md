# Superpowers workflow

Plans: `docs/superpowers/plans/`. Execute **one phase at a time**.

SDD: isolated git worktree, fresh implementer per task, spec review then quality review, then next task. Never implement on `main`.

Subagent models (user override; no `*-fast`):
- Cheap (1–2 files, code in the plan): `composer-2.5`
- Mid-tier implementers, reviewers, fix loops, final review: `cursor-grok-4.6-high`

TDD: failing test first, then minimal code. Implementer commits per plan; do not push unless asked.

Serena: memory + symbol nav (`find_symbol`, `get_symbols_overview`). Do not use Serena to edit until cwd is this repo (`mem:core`).

See `mem:task_completion`.
