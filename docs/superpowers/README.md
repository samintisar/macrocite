# SignalBench Superpowers docs

## Current: Swing Assistant (2026-09-22)

Start at the [overview spec](specs/2026-09-22-swing-assistant-00-overview.md). Sub-specs 01–05 are built in order, and each gets its own plan in `plans/` once the previous gate is green.

| # | Spec | Gate (short) |
| --- | --- | --- |
| 01 | [Data foundation](specs/2026-09-22-swing-assistant-01-data-foundation-design.md) · plan: [2026-09-22-swing-01-data-foundation.md](plans/2026-09-22-swing-01-data-foundation.md) | CI green; real ingest counts recorded; CDR price symbol verified |
| 02 | [Strategy and backtest](specs/2026-09-22-swing-assistant-02-strategy-backtest-design.md) · plan: [2026-09-24-swing-02-strategy-backtest.md](plans/2026-09-24-swing-02-strategy-backtest.md) | Pre-registered v1 pass-bar reports committed; go/no-go recorded |
| 03 | [Jev reader](specs/2026-09-22-swing-assistant-03-jev-reader-design.md) · plan: [2026-09-24-swing-03-jev-reader.md](plans/2026-09-24-swing-03-jev-reader.md) | Filter decision, Sentiment report, calibration report committed |
| 04 | [Ledger](specs/2026-09-22-swing-assistant-04-ledger-design.md) (v2-none-cash only) · plan: [2026-09-30-swing-04-ledger.md](plans/2026-09-30-swing-04-ledger.md) | Hand-checked ACB and CDR-split fixtures pass |
| 05 | [Bot and evening scan](specs/2026-09-22-swing-assistant-05-bot-scan-design.md) (v2-none-cash only) | Real scheduled scan delivered to Telegram; `/deposit 100` recorded; then the first real trade |
| 06 | [Breakout v2: idle cash in QQQ and holding time](specs/2026-09-28-swing-assistant-06-breakout-v2-design.md) · plan: [2026-09-28-swing-06-breakout-v2.md](plans/2026-09-28-swing-06-breakout-v2.md) | Six post-hoc variant reports committed and logged; `v2-t30-cash` reproduces v1 Breakout |
| 07 | [Forward paper trading](specs/2026-09-29-swing-assistant-07-paper-trading-design.md) · plan: [2026-09-29-swing-07-paper-trading.md](plans/2026-09-29-swing-07-paper-trading.md) | Built, not started: the owner dropped paper trading on 2026-09-29 and chose v2-none-cash to trade directly |

## History: original phases (2026-08-19)

Phases 0–3 are built. Phases 4 (dashboard) and 5 (stretch) were not built and are superseded by the Swing Assistant.

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
