# Spec 07 — Forward paper trading

**Parent:** [Overview](2026-09-22-swing-assistant-00-overview.md) · **Depends on:** spec 06 (branch `feat/swing-06-breakout-v2`: the cash vehicle and the six v2 configs), which sits on spec 03 (not merged yet).
**Goal:** Run seven Breakout portfolios forward, one session at a time, on prices that did not exist when their orders were decided. This is the only out-of-sample test of spec 06: every backtest so far reused 2012–2026, which had already been seen. No money is involved.

## Scope: minimal build (owner decision, 2026-09-29)

Keep it cheap: step the seven portfolios every night, save everything, write a weekly report, and warn on failure. **Built now:** the step refactor, the four tables, `paper start`, `paper run`, `paper status`, the weekly report, and the nightly script with its failure toast. **Deferred** until there is a reason (see "Later"): `paper judge`, the CDR reality check, and the twin-portfolio match line in the report. The judging rule is still fixed below, so nothing about it can drift while the data accumulates.

## Why

Five of the six spec 06 variants passed the pass bar, but the ideas were found by looking at the same data, six variants were tried, and one passed by 0.001 (`docs/research-log.md`). Paper trading decides which, if any, is worth real money. Owner decisions (2026-09-28/29): all seven portfolios are paper-traded whatever their backtest result; fills use US prices exactly like the backtest; results arrive as a weekly report file plus a Windows notification on failure; the runner steps forward with saved state (approach A).

## Portfolios (pre-registered)

`data/paper_v1.yaml`, committed before `paper start`, lists:

| Name | Config | Setup |
| --- | --- | --- |
| `v1-breakout` | `data/strategy_v1.yaml` | breakout |
| `v2-t30-cash` | `data/strategy_v2-t30-cash.yaml` | breakout |
| `v2-t30-qqq` | `data/strategy_v2-t30-qqq.yaml` | breakout |
| `v2-t60-cash` | `data/strategy_v2-t60-cash.yaml` | breakout |
| `v2-t60-qqq` | `data/strategy_v2-t60-qqq.yaml` | breakout |
| `v2-none-cash` | `data/strategy_v2-none-cash.yaml` | breakout |
| `v2-none-qqq` | `data/strategy_v2-none-qqq.yaml` | breakout |

- `v1-breakout` and `v2-t30-cash` have the same rules, so their trades must be identical every night; any difference is a bug in the runner.
- **Start:** every portfolio starts with the config's `start_equity` (100) on the same first session: the first NYSE session after `paper start` runs. QQQ buy-and-hold from that session's close is the benchmark.
- **Frozen rules:** each portfolio stores its config's `config_sha256` at start. A config whose bytes no longer match is refused for that portfolio (the run fails and notifies); configs are never edited, and a new idea is a new portfolio in a new `data/paper_<n>.yaml`.
- **Judging** (fixed here): a portfolio is judged once it has 12 months since its start **and** at least 30 closed trades, whichever is later. It uses the spec 02 pass bar over the paper period: at least 30 trades, mean R above 0.10, mean R above 0 in both halves of the paper period (split at the calendar midpoint), and Sharpe of total equity at least QQQ buy-and-hold's over the same sessions. Before that, reports are information only. A pass makes the portfolio a candidate for real money, which is an owner decision recorded in the overview changelog.

## The step refactor

- `backtest/simulator.py`'s daily loop becomes `step(state, market, readings, config, day) -> StepResult`, where `StepResult` holds the new `SimState` and that day's events, closed trades, and equity point.
- `SimState` is plain data: cash; cash-vehicle units and last mark; open positions; the orders decided at the last close (exits and entries); the risk state (peak, paused, paused since); the next position id; the last session processed. It serializes to JSON and back exactly (floats round-trip; dates as ISO strings).
- `simulate()` becomes: build the initial state, then loop `step()` over the sessions. Its results must not change: `tests/test_v1_regression.py`, every simulator and cash-vehicle test, and the v1 Breakout reproduction stay green, unchanged.
- The key invariant, tested: stepping a portfolio night by night from a start date, saving and reloading `SimState` between nights, gives exactly the same trades, events, and equity curve as one `simulate()` over the same sessions.

## Database (migration `0012_paper_trading`)

| Table | Columns | Notes |
| --- | --- | --- |
| `paper_portfolios` | id, name (unique), config_path, config_sha256, setup, started_on, state (JSON `SimState`), last_session, created_at | `state` and `last_session` update together, once per stepped session |
| `paper_events` | id, portfolio_id, session, kind, payload (JSON), recorded_at, catch_up | Append-only, never updated. Kinds: `order_exit`, `order_entry` (decided at the close), `fill_exit`, `fill_entry`, `skip`, `exit_deferred`, `vehicle_buy`, `vehicle_sell`, `pause`, `resume` |
| `paper_equity` | portfolio_id, session, equity, cash, vehicle_value, open_positions | One row per portfolio per session; unique on (portfolio_id, session) |
| `paper_runs` | id, started_at, finished_at, status (`ok`/`failed`), error, sessions_stepped, target_session | One row per `paper run` |

- **Order timestamps:** an order decided at session D's close is written with `recorded_at` on the night of D, before the next session's open. Its fill, the next night, carries the order event's id in its payload. A catch-up run (a missed night) steps each missed session in order using only data up to that session, and marks its rows `catch_up = true`: their decisions are still made on the right data, but their `recorded_at` no longer proves it, and the weekly report counts them.
- **Fills** always use the US open and the 0.2% cost, exactly like the backtest.
- **Data revisions:** saved state, events, and equity rows are never recomputed. If a price source later revises history, only later decisions see it, as live trading would.

## Commands

- `signalbench paper start`: reads `data/paper_v1.yaml` (must be committed and unchanged), checks each config is committed and unchanged, and creates the seven portfolios with their `config_sha256` and the start session. Refuses if any of them already exists.
- `signalbench paper run`: the nightly job.
  1. Take a Postgres advisory lock; if another run holds it, exit 0 with a message.
  2. Insert a `paper_runs` row.
  3. `ingest prices` (spec 01), and the liquidity flags.
  4. Target = the latest complete session (`last_complete_session`, the 16:15 New York rule).
  5. For each portfolio: check its config's sha; then step every session after `last_session` up to the target, in order, each in its own transaction (state, events, and equity row together).
  6. On the first run of a new ISO week, write the weekly report.
  7. Close the `paper_runs` row. Any error marks it `failed`, prints the error, and exits 1.
  - Running twice on the same night steps nothing and exits 0.
- `signalbench paper status`: one line per portfolio: last session, equity, return since start, open positions, closed trades, days until judgeable.

## Scheduling and notifications

- `scripts/paper_nightly.ps1` runs `uv run signalbench paper run` from the repo root, appends its output to a log file under `logs/` (git-ignored), and on a non-zero exit shows a Windows toast notification ("SignalBench paper run failed: <first line of the error>") using the built-in Windows toast API (no extra modules).
- The same script shows a toast when the last `ok` run in `paper_runs` is more than 3 days old, which catches a task that never ran.
- Task Scheduler runs the script daily at 15:00 Pacific (18:00 New York) and at log-on. Registering the task is a lasting change on the owner's PC: the exact command is shown to the owner and run only with their go-ahead.

## Weekly report

`reports/paper/<date>-weekly.md`, written on the first run of each ISO week. For each portfolio, since its start, next to QQQ buy-and-hold over the same sessions:
- equity, total return, Sharpe, max drawdown
- closed trades, mean R, win rate, open positions, average hold
- average share of equity in QQQ (QQQ variants)
- catch-up sessions and failed runs this week
- a line saying the report is information only until the judging rule is met

Reports accumulate as files; they are committed when reviewed.

## Testing

All on synthetic fixtures and in-memory SQLite:
- `SimState` JSON round-trip is exact.
- Stepping with save/reload between nights equals one `simulate()` over the same sessions (with and without a cash vehicle, with a pause, with a deferred exit).
- `simulate()` unchanged: the v1 regression pin and all existing simulator tests pass unchanged.
- `paper run`: steps only new sessions; a second run the same night does nothing; catch-up marks rows; a changed config fails that portfolio; order events precede their fills; the lock refuses a concurrent run.
- `paper start` refuses to run twice and refuses an uncommitted `data/paper_v1.yaml`.
- Weekly report rendering on a synthetic portfolio.
- The toast script is checked by hand once (a forced failure).

## Order of work

1. Build and test the code (no real runs).
2. ⛔ Run the migration on the real database.
3. ⛔ Commit `data/paper_v1.yaml` on its own, then `paper start`.
4. ⛔ Run `paper run` by hand once; check `paper status` and that the two identical portfolios match.
5. ⛔ Show the owner the Task Scheduler command; register it on their go-ahead; force one failure to check the toast.
6. Log the start in `docs/research-log.md` and this spec's changelog.

## Later (deferred, not built now)

- `signalbench paper judge`: scores a portfolio on the pass bar once it has 12 months and 30 closed trades (the rule above). Until it exists, judging is done by hand from the stored equity and events.
- CDR reality check: record each CDR's actual open next to the US-based fill.
- A weekly-report line confirming `v1-breakout` and `v2-t30-cash` still match (they can be compared by hand from `paper_events` meanwhile).

## Out of scope

Telegram (spec 05), real orders, other setups, changing any config, and CDR-price fills.

## Changelog

- 2026-09-29: created (owner decisions: all seven portfolios; US-price fills; weekly report file plus Windows toast on failure; saved-state stepping).
- 2026-09-29: trimmed to a minimal build (owner: "keep it running cheaply"): `paper judge`, the CDR reality check, and the twin-match report line moved to Later.
- 2026-09-29: implementation choices (plan `2026-09-29-swing-07-paper-trading.md`):
  - `SimState` also holds each open position's signal date, signal close, and initial stop (for R and the trade record) and the count of sessions processed (the pause's auto-resume counts sessions; the first session parks the start equity in QQQ). Its JSON carries `format: 1`. The invariant test steps each night on a market built from only the bars known that night.
  - `stop_update` is logged as a paper event too. A fill's payload carries its order event's id, and `fill_exit` carries the whole trade record. A deferred exit is ordered again at that night's close.
  - Catch-up means written at or after 09:30 New York on the next session. `paper_equity` has a `catch_up` column as well (a quiet session has no events to mark); `paper_runs.status` starts as `running`.
  - A run needs every universe series and QQQ to have a bar on the target session (the backtest's check), or it fails and steps nothing; the next run catches up. A rerun with nothing to step skips the ingest. A portfolio whose config changed is refused, the others still step, the weekly report is still written, and the run fails.
  - The start session is the first NYSE session after the New York date of `paper start`. The weekly report covers the ISO week before the one it is written in, and measures returns from the first close, as the backtest does. `paper status --stale-after-days N` exits 3 when no run has succeeded for N days; the nightly script checks it before each run.
  - `paper run` and `paper status` print one `ERROR: …` line on failure, never a traceback. The script needs Windows PowerShell 5.1 for the toast.
  - Known limits, to confirm with the owner before `paper start`: forward earnings dates are not known until filed, so the earnings rules will rarely fire; a split in a held stock looks like a crash, and dividends after entry are lost; on this PC (British Columbia time) 15:00 local is 17:00 New York from November 2026.
