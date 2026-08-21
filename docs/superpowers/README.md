# SignalBench Superpowers docs

Phase plans live in [`plans/`](plans/). Execute **one phase at a time**. Do not start Phase N+1 until Phase N’s gate is green.

| Phase | Plan | Gate (short) |
| --- | --- | --- |
| 0 Ingest | [2026-08-19-phase-0-ingest.md](plans/2026-08-19-phase-0-ingest.md) | Mocked 8-K + prices with `adj_close` in Postgres; pytest/ruff/mypy green; `GET /health` 200 |
| 1 Extraction | [2026-08-19-phase-1-extraction.md](plans/2026-08-19-phase-1-extraction.md) | Fixture extract writes versioned signals; new prompt version does not overwrite |
| 2 Eval harness | [2026-08-19-phase-2-eval-harness.md](plans/2026-08-19-phase-2-eval-harness.md) | CI fails when accuracy drops more than 5 points vs baseline |
| 3 Backtest | [2026-08-19-phase-3-backtest.md](plans/2026-08-19-phase-3-backtest.md) | Frozen fixtures produce identical metrics; no look-ahead |
| 4 Dashboard | [2026-08-19-phase-4-dashboard.md](plans/2026-08-19-phase-4-dashboard.md) | Read-only UI on fixture data; research-tool disclaimer |
| 5 Stretch | [2026-08-19-phase-5-stretch.md](plans/2026-08-19-phase-5-stretch.md) | News + 10-K/10-Q + sweep + calibration + similar-signal test |

Start coding with Phase 0 only. Use `subagent-driven-development` (recommended) or `executing-plans`. Work on a feature branch, not `main`.

## Subagent models

When dispatching SDD subagents, set the model explicitly. Do not inherit the parent session. Do not use `*-fast` models.

| Role | Model |
| --- | --- |
| Cheap implementer — 1–2 files, complete code in the plan (transcription + TDD) | `composer-2.5` |
| Mid-tier implementer — multi-file, integration, or prose spec | `cursor-grok-4.6-high` |
| Reviewers (spec + quality) and fix loops | `cursor-grok-4.6-high` |
| Final whole-branch review | `cursor-grok-4.6-high` |

Never: `composer-2.5-fast`, `cursor-grok-4.5-high-fast`, or any other fast-tier slug.
