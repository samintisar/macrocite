# Swing Assistant 07 — Forward Paper Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Step the seven pre-registered Breakout portfolios forward one NYSE session at a time, on prices that did not exist when their orders were decided; save every state, order, fill, and equity point; write a weekly report; and warn by Windows toast when the nightly run fails or stops running. Minimal build only (spec 07, Scope): no `paper judge`, no CDR reality check, no twin-match report line.

**Architecture:** The simulator's day loop becomes `step(state, market, readings, config, day) -> StepResult` over a plain-data `SimState`, and `simulate()` becomes `initial_state()` plus a loop of `step()`, with byte-identical results. `backtest/sim_state.py` writes a `SimState` to JSON and reads it back exactly. A new `signalbench.paper` package holds the portfolios file loader, `paper start` (create the portfolios from committed files), `paper run` (advisory lock, run row, ingest, then for each portfolio every missed session in order, one transaction per session), the weekly report, and `paper status`. Migration `0012_paper_trading` adds four tables. `scripts/paper_nightly.ps1` runs the job from Task Scheduler, logs to `logs/`, and shows toasts through the built-in WinRT API.

**Tech Stack:** Python 3.11+, SQLModel (in-memory SQLite in tests), Alembic, PyYAML, Typer, pytest, ruff 0.16, mypy strict, Windows PowerShell 5.1. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-09-29-swing-assistant-07-paper-trading-design.md`](../specs/2026-09-29-swing-assistant-07-paper-trading-design.md). **Depends on:** branch `feat/swing-06-breakout-v2` (the cash vehicle and the six v2 configs), which sits on `feat/swing-03-jev-reader`; none is merged. This branch, `feat/swing-07-paper-trading`, is cut from `feat/swing-06-breakout-v2`.

---

## Conventions for every task

- Run commands from the repo root with the Bash tool (Git Bash syntax). Use `uv run …` for every Python tool. Stay on branch `feat/swing-07-paper-trading`; never push without asking the owner.
- **Never edit `data/strategy_*.yaml`** (their `config_sha256` values are in stored runs and will be frozen into the paper portfolios) **or any `.env*` file.** Stage explicit paths only; never `git add -A` or `git add .`.
- **`README.md` has an uncommitted change that belongs to the owner.** Do not edit it, stage it, stash it, or check it out. Task 11 gives the owner the README lines to add.
- **No real database before Task 12.** Tasks 1–11 use only synthetic fixtures, the in-memory SQLite `session` fixture from `tests/conftest.py`, and throwaway git repos under pytest's `tmp_path`. Do not run the migration, `signalbench paper …`, `backtest run`, or `ingest` against the real database, and do not register any scheduled task, until the controller's go-ahead below.
- mypy runs strict on `src/` only. Test helpers are imported as `from strategy_helpers import …` and `from paper_helpers import …`.
- ruff 0.16 sorts imports (`I001`). Run `uv run ruff check --fix .` only **after** the module a test imports exists: before that, ruff files the missing `signalbench.*` name as third-party and moves the import. Every block below is already in ruff's order.
- After each task: `uv run pytest -q`, `uv run ruff check .`, and `uv run mypy src` all pass before committing.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Every code block in this plan was replayed, task by task, in a scratch clone of this branch before the plan was committed: each "Expected" failure and pass count below is from that replay. The suite starts at 493 passed and ends at 559 passed after Task 11, with ruff and mypy clean. `data/paper_v1.yaml` exists but is untracked from Task 3 on; the two tests that read it skip without it. If a number disagrees, look for a transcription slip before touching a test.

## The paper run, as built

| Rule | Behaviour |
| --- | --- |
| One run at a time | A Postgres session advisory lock (`pg_try_advisory_lock`) on its own connection, held for the whole run. Held elsewhere: print a line, exit 0, write no `paper_runs` row |
| Run row | Inserted as `running` before any work; closed as `ok` or `failed` with `finished_at`, `sessions_stepped`, `target_session`, and the error. A crash leaves `running` |
| Target | `last_complete_session(calendar, now)`: today after 16:15 New York, else the previous session |
| Nothing to do | Every portfolio has already stepped the target: no ingest, nothing stepped, exit 0 (a rerun the same night) |
| Ingest | `_ingest_prices` (prices plus the liquidity flags). Single-ticker failures are printed; the data check decides |
| Data check | Per regime symbol: `load_market_inputs(…, end=target)` and `check_series_current` (every universe series and QQQ must have a bar on the target). Otherwise the run fails and nothing is stepped; the next run catches up |
| Frozen config | The config file's `config_sha256` must equal the one stored at start. A mismatch refuses that portfolio only; the others still step, the weekly report is still written, then the run fails (exit 1, toast) |
| Stepping | Every session after `last_session` (from `started_on`) up to the target, in order, each on a `MarketView` of bars up to the target (`AsOfView` refuses anything later). One transaction per session: the new `SimState`, the session's events, and its equity row |
| Events | The simulator's events in its order (`exit` → `fill_exit`, `entry` → `fill_entry`; `skip`, `exit_deferred`, `vehicle_buy`, `vehicle_sell`, `pause`, `resume`, `stop_update` as they are), then `order_exit` and `order_entry` for the orders decided at that close. A fill's payload carries the id of the order event written the night before |
| Catch-up | A session's rows are `catch_up = true` when written at or after 09:30 New York on the next session (a missed night, or a late morning run) |
| Weekly report | Written on the first run of each ISO week (no `reports/paper/<date>-weekly.md` dated in the current ISO week yet) |
| Errors | Any exception rolls back the session in progress, marks the run `failed` with `<Type>: <message>`, and the CLI prints one `ERROR: …` line and exits 1 |

## Decisions this plan makes where the spec is silent

Two of them depart from the spec's letter: the extra `catch_up` column on `paper_equity`, and the extra `stop_update` event kind. Three need the owner's confirmation before `paper start`; they are marked.


- **`SimState` fields beyond the spec's list.** `opened` (each open position's signal date, signal close, and initial stop: the trade record and R need them) and `sessions` (sessions processed: the pause's auto-resume counts sessions, and the first session parks the start equity in QQQ). `vehicle_mark` (the vehicle's last close) is saved but never read back into a decision. The JSON has a `format: 1` key; a saved state of another format is refused. `step()` refuses a session on or before `last_session`.
- **The invariant test is stronger than the spec's.** Each night's step runs on a `MarketView` built from only the bars known that night, as the paper runner's does, and the state goes through JSON text between nights. Cases: plain and QQQ configs, with a pause and resume, with a deferred exit, and the 520-session two-setup random walk of `tests/test_v1_regression.py` (13 trades).
- **`stop_update` is a paper event kind too.** The spec's list omits it, but the simulator emits it and the log is append-only; dropping it would lose each trailing-stop move.
- **Payloads.** Each simulator event's own fields (without `date` and `event`, which become `session` and `kind`). `fill_exit` adds `order_event_id` and `trade` (the whole trade record, dates as ISO strings: R, P&L, sessions held); `fill_entry` adds `order_event_id`; `order_entry` is the whole entry order; `order_exit` is position id, symbol, and reason. A deferred exit is ordered again at that night's close, and its fill points at the latest order.
- **Catch-up, made exact.** The spec's test is whether the order was recorded "before the next session's open"; the plan uses 09:30 New York on the next NYSE session. **`paper_equity` gains a `catch_up` column** (a departure from the spec's column list): a quiet catch-up session has no events, so without it the report could not count catch-up sessions.
- **`paper_runs.status` starts as `running`**, so a crash is visible; only `ok` counts for the stale check. `paper_equity`'s primary key is `(portfolio_id, session)`, which is the spec's unique key.
- **The start session** is the first NYSE session strictly after the New York date on which `paper start` runs.
- **Setups a paper file may name:** `pullback`, `breakout`, `combined` (not `sentiment`, whose Jev readings a paper run does not load). `paper start` also applies the backtest's `check_config_path` (a config lives at `data/strategy_<version>.yaml`).
- **The weekly report's "this week"** is the ISO week before the one the report is written in (the week just ended). Returns, Sharpe, and drawdowns are measured from the first close, as the backtest measures a run, next to QQQ bought at the first session's close; failed runs are counted by their New York start date. The "is a report due" check reads the file names in `reports/paper/`.
- **`paper status`** prints `last ok run: …` first, then one line per portfolio; "days until judgeable" reads `judgeable after N days and M more closed trades` (12 months is the same date a year later; 29 February becomes 28 February). **`--stale-after-days N`** (new option) exits 3 with a `STALE:` line when the last `ok` run started more than N days ago, or, before any `ok` run, when the portfolios were created more than N days ago. The script runs it before `paper run`, so it reports the history before tonight.
- **Error output.** `paper run` and `paper status` print exactly one `ERROR: <Type>: <first line>` line on any failure, never a traceback (Typer's rich traceback prints local variables, which would put the database URL in the log). The toast's title is "SignalBench paper run failed" and its body is that line without `ERROR: `: the spec's one sentence split into title and body.
- **The script needs Windows PowerShell 5.1** (`powershell.exe`), because PowerShell 7 cannot load the WinRT toast types. It shows toasts under Windows PowerShell's own app id, which Windows always has registered. A `-NoToast` switch logs the toast text instead, so Task 10 can check the script without a toast or a database.
- **Confirm with the owner: earnings dates.** A paper run reads the backtest's earnings dates (SEC Item 2.02 filing dates) and runs only `ingest prices`. Forward, an earnings date is not known until its 8-K is filed, so the earnings blackout and the earnings exit will almost never fire in paper trading, where the backtest used realized dates as a stand-in for announced ones. Built as specified (fills and data exactly like the backtest); adding the Finnhub calendar would be a spec change. **Fixed after Task 11 (owner-approved):** `paper run` also ingests the Finnhub calendar (a failure only warns) and paper reads SEC 2.02 plus Finnhub dates; backtests still read SEC 2.02 only (spec 07 changelog; `tests/test_paper_run.py`).
- **Confirm with the owner: splits and dividends.** A saved position keeps the units, entry price, and stop of the day it opened. When yfinance rescales a stock's history for a split, the next night's prices are on the new scale, so a held stock that splits 2-for-1 looks like a 50% fall and hits its stop; nothing guards against it (the spec is silent). Dividends after entry are lost (price return), where the backtest's adjusted prices included them. **Fixed after Task 11 (owner-approved):** `paper run` rescales a saved state across a split and refuses one whose prices are on another scale (spec 07 changelog; `tests/test_paper_splits.py`, `tests/test_paper_run.py`). Dividends stay price-only.
- **Confirm with the owner: the run time.** This PC's time zone is "Pacific Time (British Columbia)", which Windows keeps at UTC−7 all year from November 2026. 15:00 local is 18:00 New York until 1 November 2026 and 17:00 New York after it: still after the 16:15 cutoff, so the target is that day, but with less time for the day's bars to arrive (a run that finds them missing fails with a toast, and the next run catches up). Task 16 shows 15:00 as specified and 16:00 as the alternative.

## File map

| Path | Action | Responsibility |
| --- | --- | --- |
| `src/signalbench/backtest/simulator.py` | Modify | `Opened`, `Orders`, `SimState`, `StepResult`, `initial_state()`, `step()`; `simulate()` as a loop of `step()` |
| `src/signalbench/backtest/sim_state.py` | Create | `SimState` to JSON and back, exactly |
| `src/signalbench/db/models.py` | Modify | `PaperPortfolio`, `PaperEvent`, `PaperEquity`, `PaperRun` |
| `alembic/versions/0012_paper_trading.py` | Create | The four tables |
| `src/signalbench/paper/__init__.py`, `portfolios.py` | Create | The portfolios file loader |
| `src/signalbench/paper/start.py` | Create | `paper start` |
| `src/signalbench/paper/lock.py` | Create | The advisory lock |
| `src/signalbench/paper/run.py` | Create | `paper run`: stepping, events, equity rows, the run row |
| `src/signalbench/paper/report.py` | Create | The weekly report |
| `src/signalbench/paper/status.py` | Create | `paper status` lines and the stale check |
| `src/signalbench/cli.py` | Modify | `paper start`, `paper run`, `paper status` |
| `scripts/paper_nightly.ps1` | Create | The nightly job, its log, and the toasts |
| `tests/test_simulator_step.py`, `test_paper_schema.py`, `test_paper_file.py`, `test_paper_start.py`, `test_paper_lock.py`, `test_paper_run.py`, `test_paper_report.py`, `test_paper_status.py`, `test_paper_cli.py`, `paper_helpers.py` | Create | Tests and a helper |
| `tests/test_migrations.py` | Modify | The new head and a 0012 check |
| `data/paper_v1.yaml` | Create (Task 3), commit (Task 13, gated) | The seven pre-registered portfolios |
| `docs/superpowers/specs/2026-09-29-swing-assistant-07-paper-trading-design.md` | Modify | Implementation choices; later the start line |
| `docs/research-log.md` | Modify (gated) | The start entry |

---

### Task 1: The day loop as `step()` over a saved `SimState`

**Files:**
- Modify: `src/signalbench/backtest/simulator.py`
- Create: `src/signalbench/backtest/sim_state.py`, `tests/test_simulator_step.py`

`tests/test_v1_regression.py`, `tests/test_simulator.py`, `tests/test_simulator_cash_vehicle.py`, and every other existing test stay as they are and must pass unchanged.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_simulator_step.py`:

```python
"""Spec 07: the day loop as step(), with SimState saved to JSON and reloaded between nights.

Stepping night by night, each night on a market that holds only the bars known by then, and
saving and reloading the state in between, must give exactly what one simulate() gives.
"""

import json
import random
from dataclasses import replace
from datetime import date

import pytest

from signalbench.backtest.sim_state import state_from_json, state_to_json
from signalbench.backtest.simulator import (
    EquityPoint,
    Event,
    SimState,
    TradeRecord,
    initial_state,
    simulate,
    step,
)
from signalbench.market.bars import AdjustedBar
from signalbench.strategy.config import CashVehicle, StrategyConfig
from signalbench.strategy.market_view import MarketView
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    load_test_config,
    make_bar,
    make_market,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)

PULLBACK = load_test_config().with_setups(("pullback",))
BOTH = load_test_config().with_setups(("pullback", "breakout"))
QQQ = CashVehicle(symbol="QQQ", cost_per_side=0.002)
DAYS = weekdays(date(2023, 1, 2), 280)
START, ENTRY, EXIT, END = 240, 252, 262, 270
Bars = dict[str, list[AdjustedBar]]


def _with_qqq(config: StrategyConfig) -> StrategyConfig:
    return replace(config, cash_vehicle=QQQ)


def _pullback() -> Bars:
    return {"AAA": series(DAYS, pullback_closes(len(DAYS)))}


def _crash() -> Bars:
    """The pullback fixture, then a fall to 60 after the entry: a stop exit, a pause, a resume."""
    closes = pullback_closes(len(DAYS))
    closes[ENTRY + 1 :] = [60.0] * (len(DAYS) - ENTRY - 1)
    return {"AAA": series(DAYS, closes)}


def _no_bar_on_exit() -> Bars:
    """AAA has no bar on EXIT, when its time exit is due: the exit is deferred a session."""
    bars = series(DAYS, pullback_closes(len(DAYS)))
    return {"AAA": bars[:EXIT] + bars[EXIT + 1 :]}


def _until(bars: list[AdjustedBar], day: date) -> list[AdjustedBar]:
    return [bar for bar in bars if bar.date <= day]


def _nightly(
    bars: Bars, benchmark: list[AdjustedBar], config: StrategyConfig, start: date, end: date,
    days: list[date],
) -> tuple[list[Event], list[TradeRecord], list[EquityPoint], SimState]:
    """One step per night, each on a market built from the bars known that night, with the
    state saved to JSON text and read back before the next night."""
    state = initial_state(config)
    events: list[Event] = []
    trades: list[TradeRecord] = []
    curve: list[EquityPoint] = []
    for day in [d for d in days if start <= d <= end]:
        known = {symbol: _until(series_, day) for symbol, series_ in bars.items()}
        market = make_market(known, _until(benchmark, day), days, config)
        result = step(state, market, NullReadingsView(), config, day)
        saved = json.dumps(state_to_json(result.state))
        state = state_from_json(json.loads(saved))
        events.extend(result.events)
        trades.extend(result.trades)
        curve.append(result.point)
    return events, trades, curve, state


CASES = {
    "pullback": (_pullback, PULLBACK),
    "pullback-qqq": (_pullback, _with_qqq(PULLBACK)),
    "pause": (_crash, PULLBACK),
    "pause-qqq": (_crash, _with_qqq(PULLBACK)),
    "deferred-exit": (_no_bar_on_exit, PULLBACK),
    "deferred-exit-qqq": (_no_bar_on_exit, _with_qqq(PULLBACK)),
}


@pytest.mark.parametrize("case", list(CASES))
def test_stepping_night_by_night_with_save_and_reload_equals_one_simulate(case: str) -> None:
    make, config = CASES[case]
    bars, benchmark = make(), trend_bars(DAYS, 300.0, 0.5)
    whole = simulate(
        make_market(bars, benchmark, DAYS, config), NullReadingsView(), config,
        DAYS[START], DAYS[END],
    )
    events, trades, curve, state = _nightly(bars, benchmark, config, DAYS[START], DAYS[END], DAYS)
    assert (events, trades, curve) == (whole.events, whole.trades, whole.equity_curve)
    assert list(state.positions) == whole.open_positions
    assert state.last_session == DAYS[END]
    kinds = {str(event["event"]) for event in events}
    expected = {"pause": {"pause", "resume"}, "deferred-exit": {"exit_deferred"}}
    assert expected.get(case.removesuffix("-qqq"), set()) <= kinds
    assert ("vehicle_buy" in kinds) == case.endswith("-qqq")
    assert len(trades) == 1


RANDOM_DAYS = weekdays(date(2021, 1, 4), 520)
SECTORS = {"AAA": "Energy", "BBB": "Energy", "CCC": "Energy", "DDD": "Utilities", "EEE": "Financials"}


def _walk(rng: random.Random) -> list[AdjustedBar]:
    """The random walk of test_v1_regression.py (uniform draws only)."""
    bars: list[AdjustedBar] = []
    close = 100.0
    for day in RANDOM_DAYS:
        close = max(5.0, close * (1.0008 + 0.07 * (rng.random() - 0.5)))
        volume = int(1_000_000 * (3.0 if rng.random() < 0.05 else 1.0))
        spread = close * 0.01
        bars.append(make_bar(day, close, open_=close * (1 + 0.017 * (rng.random() - 0.5)),
                             high=close + spread, low=close - spread, volume=volume))
    return bars


@pytest.mark.parametrize("config", [BOTH, _with_qqq(BOTH)], ids=["cash", "qqq"])
def test_a_long_two_setup_run_steps_to_the_same_result(config: StrategyConfig) -> None:
    rng = random.Random(20260928)
    bars = {name: _walk(rng) for name in SECTORS}
    market = make_market(
        bars, trend_bars(RANDOM_DAYS, 300.0, 0.3), RANDOM_DAYS, config, sectors=SECTORS
    )
    start, end = RANDOM_DAYS[210], RANDOM_DAYS[510]
    whole = simulate(market, NullReadingsView(), config, start, end)
    state = initial_state(config)
    curve: list[EquityPoint] = []
    events: list[Event] = []
    for day in market.sessions_between(start, end):
        result = step(state, market, NullReadingsView(), config, day)
        state = state_from_json(json.loads(json.dumps(state_to_json(result.state))))
        curve.append(result.point)
        events.extend(result.events)
    assert (events, curve) == (whole.events, whole.equity_curve)
    assert len(whole.trades) >= 10


def _states(market: MarketView, config: StrategyConfig, start: date, end: date) -> list[SimState]:
    state = initial_state(config)
    states = [state]
    for day in market.sessions_between(start, end):
        state = step(state, market, NullReadingsView(), config, day).state
        states.append(state)
    return states


@pytest.mark.parametrize("case", list(CASES))
def test_every_nightly_state_round_trips_through_json_exactly(case: str) -> None:
    make, config = CASES[case]
    market = make_market(make(), trend_bars(DAYS, 300.0, 0.5), DAYS, config)
    states = _states(market, config, DAYS[START], DAYS[END])
    for state in states:
        assert state_from_json(json.loads(json.dumps(state_to_json(state)))) == state
    assert any(state.positions for state in states)
    assert any(state.orders is not None and state.orders.entries for state in states)
    assert any(state.orders is not None and state.orders.exits for state in states)


def test_the_initial_state_holds_the_start_equity_and_nothing_else() -> None:
    state = initial_state(_with_qqq(PULLBACK))
    assert (state.cash, state.vehicle_units, state.vehicle_mark) == (100.0, 0.0, 0.0)
    assert (state.positions, state.opened, state.orders) == ((), {}, None)
    assert (state.risk.peak, state.risk.paused, state.next_id, state.sessions) == (100.0, False, 1, 0)
    assert state.last_session is None


def test_step_refuses_a_session_it_has_already_processed() -> None:
    market = make_market(_pullback(), trend_bars(DAYS, 300.0, 0.5), DAYS, PULLBACK)
    state = step(initial_state(PULLBACK), market, NullReadingsView(), PULLBACK, DAYS[START]).state
    with pytest.raises(ValueError, match="already processed"):
        step(state, market, NullReadingsView(), PULLBACK, DAYS[START])


def test_a_json_state_of_another_format_is_refused() -> None:
    data = state_to_json(initial_state(PULLBACK))
    data["format"] = 2
    with pytest.raises(ValueError, match="format 2"):
        state_from_json(data)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_simulator_step.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.backtest.sim_state'`.

- [ ] **Step 3: Split the loop into `step()`**

Replace the whole of `src/signalbench/backtest/simulator.py` with the file below. What changes: the module docstring gains two lines; `_Opened` and `_Orders` become the public `Opened` and `Orders`; the new `SimState`, `StepResult`, and `initial_state()`; the body of the old `for index, day in enumerate(sessions)` loop moves, line for line, into `step()`, which reads its locals from the state and returns a new one (the only new lines are the guard, the unpacking at the top, `vehicle_close`, and building the new `SimState`); `simulate()` loops `step()`; `_open_at_end()` reads the state. `_vehicle_prices`, `_switch`, `_settled`, `_held_value_at_open`, and `_event` are unchanged.

```python
"""Day-by-day simulation of decide() over NYSE sessions (spec 02, Simulator).

For each session t: at the open, fill the exits and then the entries decided at t-1;
at the close, mark to market, update the peak and pause state, and call decide(t).
`step()` is one session; `simulate()` is `initial_state()` and then `step()` over the
sessions. Forward paper trading (spec 07) saves the `SimState` between nights.

With a cash vehicle (spec 06), idle cash waits in the regime symbol (QQQ): on a session with
fills, QQQ is sold at the open just enough to pay for the entries, or the cash left after the
fills buys QQQ at the open. The start equity is parked at the first open. Configs without a
cash vehicle never touch QQQ and behave exactly as before.

Two consequences of "QQQ trades only at an open with a bar, and only on a session with a fill":
- On a session where the vehicle has no bar, it does not trade (it is marked at its last
  close), so an entry that needs vehicle money is skipped as `no_cash`.
- Cash left over on such a session, or after a session with only skips or deferred exits,
  waits as cash until the next session with a fill and a vehicle bar.
"""

from dataclasses import dataclass, field, replace
from datetime import date

from signalbench.strategy.config import CashVehicle, SetupName, StrategyConfig
from signalbench.strategy.decide import decide
from signalbench.strategy.decision import EntryOrder, ExitOrder, ExitReason, SkipReason
from signalbench.strategy.entries import MIN_POSITION_FRACTION
from signalbench.strategy.market_view import AsOfView, MarketView
from signalbench.strategy.portfolio import PortfolioState, Position
from signalbench.strategy.readings import ReadingsView

Event = dict[str, object]
DUST = 1e-9  # cash this close to zero after the day's fills is float residue, not a switch


@dataclass(frozen=True)
class TradeRecord:
    position_id: str
    symbol: str
    setup: SetupName
    sector: str
    signal_date: date
    entry_date: date
    exit_signal_date: date
    exit_date: date
    signal_close: float
    entry_price: float
    exit_price: float
    initial_stop: float
    units: float
    r: float  # (exit fill - entry fill) / (signal close - initial stop): planned risk, costs in
    pnl: float
    reason: ExitReason
    sessions_held: int


@dataclass(frozen=True)
class EquityPoint:
    date: date
    equity: float
    cash: float
    open_positions: int
    vehicle_value: float = 0.0  # the cash vehicle at the close (spec 06); 0 without one


@dataclass(frozen=True)
class OpenPositionRecord:
    """A position still open when the run ends. It is not a trade and has no R."""

    position_id: str
    symbol: str
    setup: SetupName
    sector: str
    signal_date: date
    entry_date: date
    entry_price: float
    units: float
    stop: float
    sessions_held: int
    last_close: float
    unrealized_pnl: float  # units * (last close - entry fill), before any exit cost


@dataclass(frozen=True)
class SimulationResult:
    start: date
    end: date
    equity_curve: list[EquityPoint]
    trades: list[TradeRecord]
    events: list[Event]
    open_positions: list[Position]
    open_at_end: list[OpenPositionRecord] = field(default_factory=list)


@dataclass(frozen=True)
class RiskState:
    peak: float
    paused: bool
    paused_since: date | None
    paused_at: int | None  # session index of the pause, for the auto-resume count


def step_risk(
    state: RiskState, equity: float, index: int, day: date, config: StrategyConfig
) -> tuple[RiskState, str | None]:
    """Close-of-session pause logic.

    While paused: after `auto_resume_sessions` sessions, resume and reset peak := equity
    (the backtest stand-in for the owner's /resume). Otherwise the peak tracks the highest
    equity, and equity below (1 - pause_drawdown) x peak pauses new entries.
    """
    if state.paused and state.paused_at is not None:
        if index - state.paused_at >= config.auto_resume_sessions:
            return RiskState(peak=equity, paused=False, paused_since=None, paused_at=None), "resume"
        return replace(state, peak=max(state.peak, equity)), None
    peak = max(state.peak, equity)
    if equity < (1.0 - config.pause_drawdown) * peak:
        return RiskState(peak=peak, paused=True, paused_since=day, paused_at=index), "pause"
    return replace(state, peak=peak), None


@dataclass(frozen=True)
class Opened:
    """What a position keeps from its signal, for its trade record."""

    signal_date: date
    signal_close: float
    initial_stop: float

    @property
    def planned_risk(self) -> float:
        """Per-unit risk planned at the signal: signal close - stop (the unit of R)."""
        return self.signal_close - self.initial_stop


@dataclass(frozen=True)
class Orders:
    """What decide() returned at a close, to fill at the next open."""

    decided_on: date
    exits: list[ExitOrder]
    entries: list[EntryOrder]


@dataclass(frozen=True)
class SimState:
    """Everything the day loop carries from one session to the next (spec 07).

    Plain data: `backtest/sim_state.py` writes it to JSON and reads it back exactly.
    """

    cash: float
    vehicle_units: float  # cash-vehicle units held (spec 06); 0 without a vehicle
    vehicle_mark: float  # the vehicle's close at the last session; 0 before its first bar
    positions: tuple[Position, ...]  # in the order they were opened
    opened: dict[str, Opened]  # by position id
    orders: Orders | None  # decided at the last close, to fill at the next open
    risk: RiskState
    next_id: int  # the number in the next position id, P00001 first
    sessions: int  # sessions processed so far: the next session's index
    last_session: date | None


@dataclass(frozen=True)
class StepResult:
    """One session: the state after its close, and what happened during it."""

    state: SimState
    events: list[Event]
    trades: list[TradeRecord]
    point: EquityPoint


def initial_state(config: StrategyConfig) -> SimState:
    """The state before the first session: the start equity in cash, nothing else."""
    return SimState(
        cash=config.start_equity,
        vehicle_units=0.0,
        vehicle_mark=0.0,
        positions=(),
        opened={},
        orders=None,
        risk=RiskState(peak=config.start_equity, paused=False, paused_since=None, paused_at=None),
        next_id=1,
        sessions=0,
        last_session=None,
    )


def entry_skip(entry: EntryOrder, open_price: float, cash: float, config: StrategyConfig) -> SkipReason | None:
    """Open-time skip rules: gap_up, gap_below_stop, and no cash left.

    `cash` is what the entry can spend: cash, plus the cash vehicle at the open net of its
    selling cost (spec 06).
    """
    if open_price > entry.signal_close * (1.0 + config.gap_up_limit):
        return "gap_up"
    if open_price <= entry.stop:
        return "gap_below_stop"
    if cash <= 0.0:
        return "no_cash"
    return None


def simulate(
    market: MarketView,
    readings: ReadingsView,
    config: StrategyConfig,
    start: date,
    end: date,
) -> SimulationResult:
    sessions = market.sessions_between(start, end)
    if not sessions:
        raise ValueError(f"no sessions between {start} and {end}")
    state = initial_state(config)
    trades: list[TradeRecord] = []
    events: list[Event] = []
    curve: list[EquityPoint] = []
    for day in sessions:
        result = step(state, market, readings, config, day)
        state = result.state
        trades.extend(result.trades)
        events.extend(result.events)
        curve.append(result.point)
    return SimulationResult(
        start=sessions[0],
        end=sessions[-1],
        equity_curve=curve,
        trades=trades,
        events=events,
        open_positions=list(state.positions),
        open_at_end=_open_at_end(market, sessions[-1], state),
    )


def step(
    state: SimState,
    market: MarketView,
    readings: ReadingsView,
    config: StrategyConfig,
    day: date,
) -> StepResult:
    """One session: the open (fills decided at the last close), then the close (marks, the
    pause state, and decide()). `state` is not changed; the result holds the new state."""
    if state.last_session is not None and day <= state.last_session:
        raise ValueError(f"{day} was already processed (the last session is {state.last_session})")
    index = state.sessions
    cash = state.cash
    vehicle = config.cash_vehicle
    vehicle_units = state.vehicle_units
    positions = {position.id: position for position in state.positions}
    opened = dict(state.opened)
    risk = state.risk
    orders = state.orders
    next_id = state.next_id
    trades: list[TradeRecord] = []
    events: list[Event] = []

    view = market.at(day)
    vehicle_open, vehicle_mark = _vehicle_prices(view, day, vehicle)
    sleeve = 0.0  # what the vehicle would pay today, net of its selling cost
    if vehicle is not None and vehicle_open is not None:
        sleeve = vehicle_units * vehicle_open * (1.0 - vehicle.cost_per_side)
    filled = index == 0  # the first open parks the start equity

    # Open: exits first, then entries, both decided at the previous close.
    deferred: list[ExitOrder] = []  # exits with no bar today, retried at the next open
    if orders is not None:
        for order in orders.exits:
            position = positions[order.position_id]
            snap = view.snapshot(position.symbol)
            if snap is None or snap.date != day:
                events.append(_event(day, "exit_deferred", position_id=position.id))
                deferred.append(order)
                continue
            fill = snap.open * (1.0 - config.cost_per_side)
            cash += position.units * fill
            filled = True
            meta = opened.pop(position.id)
            del positions[position.id]
            trade = TradeRecord(
                position_id=position.id,
                symbol=position.symbol,
                setup=position.setup,
                sector=position.sector,
                signal_date=meta.signal_date,
                entry_date=position.entry_date,
                exit_signal_date=orders.decided_on,
                exit_date=day,
                signal_close=meta.signal_close,
                entry_price=position.entry_price,
                exit_price=fill,
                initial_stop=meta.initial_stop,
                units=position.units,
                r=(fill - position.entry_price) / meta.planned_risk,
                pnl=position.units * (fill - position.entry_price),
                reason=order.reason,
                sessions_held=position.sessions_held,
            )
            trades.append(trade)
            events.append(
                _event(day, "exit", position_id=position.id, symbol=position.symbol,
                       reason=order.reason, price=fill, r=trade.r)
            )
        open_equity = (
            cash + _held_value_at_open(view, day, positions) + vehicle_units * vehicle_mark
        )
        for entry in orders.entries:
            snap = view.snapshot(entry.symbol)
            skip = "no_bar" if snap is None or snap.date != day else None
            if snap is not None and skip is None:
                skip = entry_skip(entry, snap.open, cash + sleeve, config)
            fill = 0.0 if snap is None else snap.open * (1.0 + config.cost_per_side)
            units = 0.0 if skip is not None else min(entry.units, (cash + sleeve) / fill)
            if skip is None and units * fill < MIN_POSITION_FRACTION * open_equity:
                skip = "no_cash"  # trimmed to dust by earlier fills in this batch
            if snap is None or skip is not None:
                events.append(
                    _event(day, "skip", symbol=entry.symbol, setup=entry.setup, reason=skip)
                )
                continue
            cash -= units * fill  # below zero only until the vehicle is sold, below
            filled = True
            position_id = f"P{next_id:05d}"
            next_id += 1
            meta = Opened(orders.decided_on, entry.signal_close, entry.stop)
            target = None
            if entry.target_r is not None:
                target = fill + entry.target_r * meta.planned_risk
            positions[position_id] = Position(
                id=position_id,
                symbol=entry.symbol,
                setup=entry.setup,
                sector=entry.sector,
                units=units,
                entry_price=fill,
                entry_date=day,
                stop=entry.stop,
                target=target,
                time_limit=entry.time_limit,
                sessions_held=0,
                highest_close=0.0,
            )
            opened[position_id] = meta
            events.append(
                _event(day, "entry", position_id=position_id, symbol=entry.symbol,
                       setup=entry.setup, units=units, price=fill, trimmed=units < entry.units)
            )

    # Open, after the fills: sell the vehicle to cover the entries, or park the cash left.
    if vehicle is not None and vehicle_open is not None and filled:
        cash, vehicle_units = _switch(day, cash, vehicle_units, vehicle_open, vehicle, events)

    # Close: mark to market, count the session, update the peak and pause state.
    value = 0.0
    for position_id, position in list(positions.items()):
        snap = view.snapshot(position.symbol)
        close = position.entry_price if snap is None else snap.close
        value += position.units * close
        positions[position_id] = replace(
            position,
            sessions_held=position.sessions_held + 1,
            highest_close=max(position.highest_close, close),
        )
    vehicle_value = 0.0
    vehicle_close = state.vehicle_mark
    spendable = cash  # what decide() may size against: cash, plus the vehicle net of cost
    if vehicle is not None:
        benchmark = view.benchmark()
        vehicle_close = 0.0 if benchmark is None else benchmark.close
        vehicle_value = vehicle_units * vehicle_close
        spendable = cash + vehicle_value * (1.0 - vehicle.cost_per_side)
    equity = cash + value + vehicle_value
    risk, change = step_risk(risk, equity, index, day, config)
    if change is not None:
        events.append(_event(day, change, equity=equity, peak=risk.peak))
    point = EquityPoint(day, equity, cash, len(positions), vehicle_value)

    portfolio = PortfolioState(
        cash=spendable,
        positions=tuple(positions.values()),
        pending=(),
        equity=equity,
        peak=risk.peak,
        paused=risk.paused,
        paused_since=risk.paused_since,
    )
    decision = decide(day, market, readings, portfolio, config)
    for update in decision.stop_updates:
        positions[update.position_id] = replace(
            positions[update.position_id], stop=update.new_stop
        )
        events.append(
            _event(day, "stop_update", position_id=update.position_id,
                   old_stop=update.old_stop, new_stop=update.new_stop)
        )
    for skipped in decision.skips:
        events.append(
            _event(day, "skip", symbol=skipped.symbol, setup=skipped.setup, reason=skipped.reason)
        )
    carried = {order.position_id for order in deferred}
    exits = deferred + [e for e in decision.exits if e.position_id not in carried]
    new_state = SimState(
        cash=cash,
        vehicle_units=vehicle_units,
        vehicle_mark=vehicle_close,
        positions=tuple(positions.values()),
        opened=opened,
        orders=Orders(day, sorted(exits, key=lambda e: e.position_id), decision.entries),
        risk=risk,
        next_id=next_id,
        sessions=index + 1,
        last_session=day,
    )
    return StepResult(state=new_state, events=events, trades=trades, point=point)


def _open_at_end(market: MarketView, end: date, state: SimState) -> list[OpenPositionRecord]:
    """Positions still held at the last close, marked the way the equity curve marks them."""
    view = market.at(end)
    records: list[OpenPositionRecord] = []
    for position in state.positions:
        snap = view.snapshot(position.symbol)
        close = position.entry_price if snap is None else snap.close
        records.append(
            OpenPositionRecord(
                position_id=position.id,
                symbol=position.symbol,
                setup=position.setup,
                sector=position.sector,
                signal_date=state.opened[position.id].signal_date,
                entry_date=position.entry_date,
                entry_price=position.entry_price,
                units=position.units,
                stop=position.stop,
                sessions_held=position.sessions_held,
                last_close=close,
                unrealized_pnl=position.units * (close - position.entry_price),
            )
        )
    return records


def _vehicle_prices(
    view: AsOfView, day: date, vehicle: CashVehicle | None
) -> tuple[float | None, float]:
    """The vehicle's open today (None when it has no bar today, so it does not trade) and its
    mark at the open (today's open, else the last close; 0 before its first bar)."""
    if vehicle is None:
        return None, 0.0
    snap = view.benchmark()
    if snap is None:
        return None, 0.0
    if snap.date != day:
        return None, snap.close
    return snap.open, snap.open


def _switch(
    day: date,
    cash: float,
    units: float,
    price: float,
    vehicle: CashVehicle,
    events: list[Event],
) -> tuple[float, float]:
    """Settle the day's cash against the vehicle at the open (spec 06).

    Negative cash (entries paid beyond the cash on hand) sells just enough units to cover it;
    positive cash buys units. Fills mirror a stock's: price x (1 - cost) when selling and
    price x (1 + cost) when buying, so the cost is charged only on the amount that moves.
    Returns the new (cash, units).
    """
    cost = vehicle.cost_per_side
    if cash < -DUST:
        sold = min(-cash / (price * (1.0 - cost)), units)
        amount = sold * price
        events.append(
            _event(day, "vehicle_sell", symbol=vehicle.symbol, units=sold, price=price,
                   amount=amount, cost=amount * cost)
        )
        return _settled(cash + amount * (1.0 - cost)), units - sold
    if cash > DUST:
        bought = cash / (price * (1.0 + cost))
        amount = bought * price
        events.append(
            _event(day, "vehicle_buy", symbol=vehicle.symbol, units=bought, price=price,
                   amount=amount, cost=amount * cost)
        )
        return _settled(cash - amount * (1.0 + cost)), units + bought
    return cash, units


def _settled(cash: float) -> float:
    """Cash after a switch: float residue within DUST of zero is zero."""
    return 0.0 if abs(cash) <= DUST else cash


def _held_value_at_open(view: AsOfView, day: date, positions: dict[str, Position]) -> float:
    """Held units marked at today's open (the last close when a symbol has no bar today)."""
    value = 0.0
    for position in positions.values():
        snap = view.snapshot(position.symbol)
        if snap is None:
            mark = position.entry_price
        else:
            mark = snap.open if snap.date == day else snap.close
        value += position.units * mark
    return value


def _event(day: date, kind: str, **fields: object) -> Event:
    return {"date": day.isoformat(), "event": kind, **fields}
```

- [ ] **Step 4: Write the JSON round trip**

Create `src/signalbench/backtest/sim_state.py`:

```python
"""`SimState` to JSON and back, exactly (spec 07).

Floats are written as Python writes them (the shortest repr that reads back to the same
float), dates as ISO strings. `state_from_json(json.loads(json.dumps(state_to_json(s))))`
equals `s`.
"""

from collections.abc import Mapping
from datetime import date
from typing import Any, cast, get_args

from signalbench.backtest.simulator import Opened, Orders, RiskState, SimState
from signalbench.strategy.config import SetupName
from signalbench.strategy.decision import EntryOrder, ExitOrder, ExitReason
from signalbench.strategy.portfolio import Position

FORMAT = 1  # bump when SimState changes shape; old saved states are then refused


def state_to_json(state: SimState) -> dict[str, Any]:
    orders = state.orders
    return {
        "format": FORMAT,
        "cash": state.cash,
        "vehicle_units": state.vehicle_units,
        "vehicle_mark": state.vehicle_mark,
        "positions": [_position_json(position) for position in state.positions],
        "opened": {key: _opened_json(meta) for key, meta in state.opened.items()},
        "orders": None if orders is None else {
            "decided_on": orders.decided_on.isoformat(),
            "exits": [
                {"position_id": o.position_id, "symbol": o.symbol, "reason": o.reason}
                for o in orders.exits
            ],
            "entries": [_entry_json(entry) for entry in orders.entries],
        },
        "risk": {
            "peak": state.risk.peak,
            "paused": state.risk.paused,
            "paused_since": _day_json(state.risk.paused_since),
            "paused_at": state.risk.paused_at,
        },
        "next_id": state.next_id,
        "sessions": state.sessions,
        "last_session": _day_json(state.last_session),
    }


def state_from_json(data: Mapping[str, Any]) -> SimState:
    if data.get("format") != FORMAT:
        raise ValueError(f"saved state has format {data.get('format')}; this code reads {FORMAT}")
    orders = data["orders"]
    risk = data["risk"]
    return SimState(
        cash=data["cash"],
        vehicle_units=data["vehicle_units"],
        vehicle_mark=data["vehicle_mark"],
        positions=tuple(_position(raw) for raw in data["positions"]),
        opened={key: _opened(raw) for key, raw in data["opened"].items()},
        orders=None if orders is None else Orders(
            decided_on=date.fromisoformat(orders["decided_on"]),
            exits=[
                ExitOrder(position_id=o["position_id"], symbol=o["symbol"], reason=_reason(o["reason"]))
                for o in orders["exits"]
            ],
            entries=[_entry(raw) for raw in orders["entries"]],
        ),
        risk=RiskState(
            peak=risk["peak"],
            paused=risk["paused"],
            paused_since=_day(risk["paused_since"]),
            paused_at=risk["paused_at"],
        ),
        next_id=data["next_id"],
        sessions=data["sessions"],
        last_session=_day(data["last_session"]),
    )


def _day_json(day: date | None) -> str | None:
    return None if day is None else day.isoformat()


def _day(text: str | None) -> date | None:
    return None if text is None else date.fromisoformat(text)


def _setup(value: str) -> SetupName:
    if value not in get_args(SetupName):
        raise ValueError(f"unknown setup {value!r} in saved state")
    return cast(SetupName, value)


def _reason(value: str) -> ExitReason:
    if value not in get_args(ExitReason):
        raise ValueError(f"unknown exit reason {value!r} in saved state")
    return cast(ExitReason, value)


def _position_json(position: Position) -> dict[str, Any]:
    return {
        "id": position.id,
        "symbol": position.symbol,
        "setup": position.setup,
        "sector": position.sector,
        "units": position.units,
        "entry_price": position.entry_price,
        "entry_date": position.entry_date.isoformat(),
        "stop": position.stop,
        "target": position.target,
        "time_limit": position.time_limit,
        "sessions_held": position.sessions_held,
        "highest_close": position.highest_close,
    }


def _position(raw: Mapping[str, Any]) -> Position:
    return Position(
        id=raw["id"],
        symbol=raw["symbol"],
        setup=_setup(raw["setup"]),
        sector=raw["sector"],
        units=raw["units"],
        entry_price=raw["entry_price"],
        entry_date=date.fromisoformat(raw["entry_date"]),
        stop=raw["stop"],
        target=raw["target"],
        time_limit=raw["time_limit"],
        sessions_held=raw["sessions_held"],
        highest_close=raw["highest_close"],
    )


def _opened_json(meta: Opened) -> dict[str, Any]:
    return {
        "signal_date": meta.signal_date.isoformat(),
        "signal_close": meta.signal_close,
        "initial_stop": meta.initial_stop,
    }


def _opened(raw: Mapping[str, Any]) -> Opened:
    return Opened(
        signal_date=date.fromisoformat(raw["signal_date"]),
        signal_close=raw["signal_close"],
        initial_stop=raw["initial_stop"],
    )


def _entry_json(entry: EntryOrder) -> dict[str, Any]:
    return {
        "symbol": entry.symbol,
        "setup": entry.setup,
        "sector": entry.sector,
        "signal_close": entry.signal_close,
        "stop": entry.stop,
        "target_r": entry.target_r,
        "time_limit": entry.time_limit,
        "units": entry.units,
        "risk_amount": entry.risk_amount,
        "catalyst": entry.catalyst,
        "rank": entry.rank,
    }


def _entry(raw: Mapping[str, Any]) -> EntryOrder:
    return EntryOrder(
        symbol=raw["symbol"],
        setup=_setup(raw["setup"]),
        sector=raw["sector"],
        signal_close=raw["signal_close"],
        stop=raw["stop"],
        target_r=raw["target_r"],
        time_limit=raw["time_limit"],
        units=raw["units"],
        risk_amount=raw["risk_amount"],
        catalyst=raw["catalyst"],
        rank=raw["rank"],
    )
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_simulator_step.py tests/test_v1_regression.py tests/test_simulator.py tests/test_simulator_cash_vehicle.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 46 passed (17 new, the 2 regression pins, and the 27 simulator and cash-vehicle tests, unchanged); the full suite 510 passed; ruff `All checks passed!`; mypy `Success`.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/backtest/simulator.py src/signalbench/backtest/sim_state.py tests/test_simulator_step.py
git commit -m "refactor: the simulator's day loop as step() over a JSON-safe SimState

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The four tables and migration `0012_paper_trading`

**Files:**
- Modify: `src/signalbench/db/models.py`, `tests/test_migrations.py`
- Create: `alembic/versions/0012_paper_trading.py`, `tests/test_paper_schema.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_schema.py`:

```python
"""Spec 07 tables: the models, and migration 0012 that creates the same columns."""

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from signalbench.backtest.sim_state import state_from_json, state_to_json
from signalbench.backtest.simulator import initial_state, step
from signalbench.db.models import PaperEquity, PaperEvent, PaperPortfolio, PaperRun
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    load_test_config,
    make_market,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0012_paper_trading.py"
TABLES = ("paper_portfolios", "paper_events", "paper_equity", "paper_runs")
CONFIG = load_test_config().with_setups(("pullback",))
DAYS = weekdays(date(2023, 1, 2), 280)
NOW = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)


def _portfolio(session: Session, name: str = "p1") -> PaperPortfolio:
    portfolio = PaperPortfolio(
        name=name, config_path="data/strategy_test.yaml", config_sha256="a" * 64,
        setup="pullback", started_on=DAYS[240], state=state_to_json(initial_state(CONFIG)),
    )
    session.add(portfolio)
    session.commit()
    session.refresh(portfolio)
    return portfolio


def test_a_saved_state_reads_back_from_the_database_exactly(session: Session) -> None:
    portfolio = _portfolio(session)
    market = make_market(
        {"AAA": series(DAYS, pullback_closes(len(DAYS)))}, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG
    )
    state = initial_state(CONFIG)
    for day in DAYS[240:256]:  # an open position and its orders
        state = step(state, market, NullReadingsView(), CONFIG, day).state
    assert state.positions
    portfolio.state = state_to_json(state)
    portfolio.last_session = state.last_session
    session.add(portfolio)
    session.commit()
    session.expire_all()
    stored = session.get(PaperPortfolio, portfolio.id)
    assert stored is not None
    assert state_from_json(stored.state) == state
    assert stored.last_session == DAYS[255]


def test_portfolio_names_are_unique(session: Session) -> None:
    _portfolio(session)
    with pytest.raises(IntegrityError):
        _portfolio(session)


def test_one_equity_row_per_portfolio_and_session(session: Session) -> None:
    portfolio = _portfolio(session)
    assert portfolio.id is not None
    for _ in range(2):
        session.add(
            PaperEquity(portfolio_id=portfolio.id, session=DAYS[240], equity=100.0, cash=100.0,
                        vehicle_value=0.0, open_positions=0, catch_up=False)
        )
    with pytest.raises(IntegrityError):
        session.commit()


def test_events_and_runs_store_their_json_and_times(session: Session) -> None:
    portfolio = _portfolio(session)
    assert portfolio.id is not None
    session.add(
        PaperEvent(portfolio_id=portfolio.id, session=DAYS[240], kind="order_entry",
                   payload={"symbol": "AAA", "units": 0.1}, recorded_at=NOW, catch_up=False)
    )
    session.add(PaperRun(started_at=NOW, status="running"))
    session.commit()
    session.expire_all()
    [event] = session.exec(select(PaperEvent)).all()
    [run] = session.exec(select(PaperRun)).all()
    assert (event.payload, event.recorded_at) == ({"symbol": "AAA", "units": 0.1}, NOW)
    assert (run.status, run.sessions_stepped, run.finished_at, run.error) == ("running", 0, None, None)


def _upgrade_and_downgrade() -> tuple[dict[str, list[tuple[str, bool]]], list[str]]:
    spec = importlib.util.spec_from_file_location("migration_0012", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        inspector = inspect(connection)
        columns = {
            table: [(c["name"], bool(c["nullable"])) for c in inspector.get_columns(table)]
            for table in TABLES
        }
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        left = inspect(connection).get_table_names()
    return columns, left


def test_migration_0012_creates_the_model_columns_and_drops_them_again() -> None:
    created, left = _upgrade_and_downgrade()
    for table in TABLES:
        model = SQLModel.metadata.tables[table]
        assert created[table] == [(c.name, bool(c.nullable)) for c in model.columns], table
    assert left == []
```

In `tests/test_migrations.py`, replace:

```python
    assert _script().get_heads() == ["0011_jev_readings"]
```

with:

```python
    assert _script().get_heads() == ["0012_paper_trading"]
```

Append to `tests/test_migrations.py`:

```python
def test_0012_creates_the_four_paper_tables() -> None:
    text = (VERSIONS / "0012_paper_trading.py").read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0011_jev_readings"' in text
    for table in ("paper_portfolios", "paper_events", "paper_equity", "paper_runs"):
        assert f'op.create_table(\n        "{table}"' in text
    assert 'sa.PrimaryKeyConstraint("portfolio_id", "session")' in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_migrations.py -q`
Expected: 2 failed, 4 passed: the head is still `0011_jev_readings`, and `FileNotFoundError` for `0012_paper_trading.py`.

Run: `uv run pytest tests/test_paper_schema.py -q`
Expected: collection error, `ImportError: cannot import name 'PaperEquity' from 'signalbench.db.models'`.

- [ ] **Step 3: Add the models**

Append to `src/signalbench/db/models.py`:

```python
class PaperPortfolio(SQLModel, table=True):
    """One pre-registered forward paper portfolio (spec 07). Created by `paper start`."""

    __tablename__ = "paper_portfolios"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    config_path: str  # repo-relative, e.g. data/strategy_v1.yaml
    config_sha256: str  # of the config at start; a config that no longer matches is refused
    setup: str  # pullback | breakout | combined
    started_on: date  # the first session stepped
    state: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))  # a SimState
    last_session: date | None = None  # updated with `state`, once per stepped session
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(UTCDateTime(), nullable=False),
    )


class PaperEvent(SQLModel, table=True):
    """What happened to a paper portfolio in one session (spec 07). Append-only."""

    __tablename__ = "paper_events"

    id: int | None = Field(default=None, primary_key=True)
    portfolio_id: int = Field(foreign_key="paper_portfolios.id", ondelete="RESTRICT", index=True)
    session: date
    kind: str  # order_exit | order_entry | fill_exit | fill_entry | skip | exit_deferred | ...
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    recorded_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    catch_up: bool  # recorded after the next session's open, so recorded_at proves nothing


class PaperEquity(SQLModel, table=True):
    """A paper portfolio at one session's close (spec 07): one row per portfolio and session."""

    __tablename__ = "paper_equity"

    portfolio_id: int = Field(
        foreign_key="paper_portfolios.id", ondelete="RESTRICT", primary_key=True
    )
    session: date = Field(primary_key=True)
    equity: float
    cash: float
    vehicle_value: float
    open_positions: int
    catch_up: bool


class PaperRun(SQLModel, table=True):
    """One `signalbench paper run` (spec 07)."""

    __tablename__ = "paper_runs"

    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    finished_at: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime(), nullable=True)
    )
    status: str  # running | ok | failed
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    sessions_stepped: int = 0
    target_session: date | None = None
```

- [ ] **Step 4: Add the migration**

Create `alembic/versions/0012_paper_trading.py`:

```python
"""Spec 07 forward paper trading

Revision ID: 0012_paper_trading
Revises: 0011_jev_readings
Create Date: 2026-09-29 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_paper_trading"
down_revision: str | None = "0011_jev_readings"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_portfolios",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("config_path", sa.String(), nullable=False),
        sa.Column("config_sha256", sa.String(), nullable=False),
        sa.Column("setup", sa.String(), nullable=False),
        sa.Column("started_on", sa.Date(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("last_session", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "paper_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("session", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("catch_up", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["paper_portfolios.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paper_events_portfolio_id", "paper_events", ["portfolio_id"])
    op.create_table(
        "paper_equity",
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("session", sa.Date(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("cash", sa.Float(), nullable=False),
        sa.Column("vehicle_value", sa.Float(), nullable=False),
        sa.Column("open_positions", sa.Integer(), nullable=False),
        sa.Column("catch_up", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["paper_portfolios.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("portfolio_id", "session"),
    )
    op.create_table(
        "paper_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sessions_stepped", sa.Integer(), nullable=False),
        sa.Column("target_session", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("paper_runs")
    op.drop_table("paper_equity")
    op.drop_index("ix_paper_events_portfolio_id", table_name="paper_events")
    op.drop_table("paper_events")
    op.drop_table("paper_portfolios")
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_paper_schema.py tests/test_migrations.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 11 passed; the full suite 516 passed; ruff and mypy clean. (`test_migration_0012_creates_the_model_columns_and_drops_them_again` runs the migration's own `upgrade()` and `downgrade()` on SQLite and compares every column name and nullability with the models.)

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/db/models.py alembic/versions/0012_paper_trading.py tests/test_paper_schema.py tests/test_migrations.py
git commit -m "feat: paper trading tables and migration 0012

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The portfolios file and its loader (`data/paper_v1.yaml` written, not committed)

**Files:**
- Create: `src/signalbench/paper/__init__.py`, `src/signalbench/paper/portfolios.py`, `tests/test_paper_file.py` (committed in this task)
- Create: `data/paper_v1.yaml` (**not** committed here: Task 13 commits it on its own, after the owner has read it)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_file.py`:

```python
"""The paper portfolios file (spec 07, Portfolios) and its loader."""

from dataclasses import replace
from pathlib import Path

import pytest

from signalbench.paper.portfolios import PaperFileError, PortfolioSpec, load_paper_file
from signalbench.strategy.config import load_strategy_config

REPO = Path(__file__).resolve().parents[1]
PAPER_V1 = REPO / "data" / "paper_v1.yaml"
EXPECTED = [
    PortfolioSpec("v1-breakout", "data/strategy_v1.yaml", "breakout"),
    PortfolioSpec("v2-t30-cash", "data/strategy_v2-t30-cash.yaml", "breakout"),
    PortfolioSpec("v2-t30-qqq", "data/strategy_v2-t30-qqq.yaml", "breakout"),
    PortfolioSpec("v2-t60-cash", "data/strategy_v2-t60-cash.yaml", "breakout"),
    PortfolioSpec("v2-t60-qqq", "data/strategy_v2-t60-qqq.yaml", "breakout"),
    PortfolioSpec("v2-none-cash", "data/strategy_v2-none-cash.yaml", "breakout"),
    PortfolioSpec("v2-none-qqq", "data/strategy_v2-none-qqq.yaml", "breakout"),
]
GOOD = "portfolios:\n  - name: p1\n    config: data/strategy_test.yaml\n    setup: pullback\n"


def _paper_v1() -> Path:
    if not PAPER_V1.exists():
        pytest.skip("data/paper_v1.yaml is written by spec 07 Task 3 and committed after review")
    return PAPER_V1


def test_paper_v1_lists_the_seven_pre_registered_portfolios() -> None:
    assert load_paper_file(_paper_v1()) == EXPECTED


def test_every_paper_v1_config_parses_and_the_twins_share_their_rules() -> None:
    configs = {spec.name: load_strategy_config(REPO / spec.config)[0] for spec in load_paper_file(_paper_v1())}
    twin = configs["v2-t30-cash"]
    assert replace(twin, version="v1") == configs["v1-breakout"]
    assert {config.start_equity for config in configs.values()} == {100.0}


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "paper_test.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_well_formed_file_loads(tmp_path: Path) -> None:
    assert load_paper_file(_write(tmp_path, GOOD)) == [
        PortfolioSpec("p1", "data/strategy_test.yaml", "pullback")
    ]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("portfolios: []\n", r"paper_test.yaml.portfolios: expected a non-empty list"),
        (GOOD + "extra: 1\n", "expected exactly one top-level key, 'portfolios'"),
        (GOOD.replace("name: p1", "name: P 1"), r"portfolios\[0\].name: expected lowercase"),
        (GOOD.replace("data/strategy_test.yaml", "strategy_test.yaml"), r"\[0\].config: expected data/strategy_"),
        (GOOD.replace("pullback", "sentiment"), r"\[0\].setup: expected one of pullback, breakout, combined"),
        (GOOD.replace("    setup: pullback\n", ""), r"\[0\]: expected exactly the keys name, config, setup"),
        (GOOD + GOOD.removeprefix("portfolios:\n"), r"duplicate names \['p1'\]"),
    ],
)
def test_a_malformed_file_is_refused(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(PaperFileError, match=message):
        load_paper_file(_write(tmp_path, text))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_paper_file.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.paper'`.

- [ ] **Step 3: Write the loader**

Create `src/signalbench/paper/__init__.py` as an empty file (like `src/signalbench/backtest/__init__.py`).

Create `src/signalbench/paper/portfolios.py`:

```python
"""The pre-registered paper portfolios file, `data/paper_<n>.yaml` (spec 07, Portfolios)."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

PaperSetup = Literal["pullback", "breakout", "combined"]
SETUPS: tuple[PaperSetup, ...] = ("pullback", "breakout", "combined")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CONFIG = re.compile(r"^data/strategy_[A-Za-z0-9_-]+\.yaml$")


class PaperFileError(ValueError):
    """The portfolios file is malformed. The message names the file and the entry."""


@dataclass(frozen=True)
class PortfolioSpec:
    name: str
    config: str  # repo-relative, forward slashes: data/strategy_<version>.yaml
    setup: PaperSetup


def paper_setup(value: object, where: str) -> PaperSetup:
    """A setup a paper portfolio may run: not Sentiment, whose Jev readings a paper run does
    not load."""
    if value not in SETUPS:
        raise PaperFileError(f"{where}: expected one of {', '.join(SETUPS)}")
    return value


def load_paper_file(path: Path) -> list[PortfolioSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    where = path.name
    if not isinstance(raw, dict) or set(raw) != {"portfolios"}:
        raise PaperFileError(f"{where}: expected exactly one top-level key, 'portfolios'")
    items = raw["portfolios"]
    if not isinstance(items, list) or not items:
        raise PaperFileError(f"{where}.portfolios: expected a non-empty list")
    specs: list[PortfolioSpec] = []
    for index, item in enumerate(items):
        at = f"{where}.portfolios[{index}]"
        if not isinstance(item, dict) or set(item) != {"name", "config", "setup"}:
            raise PaperFileError(f"{at}: expected exactly the keys name, config, setup")
        name, config, setup = item["name"], item["config"], item["setup"]
        if not isinstance(name, str) or not NAME.match(name):
            raise PaperFileError(f"{at}.name: expected lowercase letters, digits and dashes")
        if not isinstance(config, str) or not CONFIG.match(config):
            raise PaperFileError(f"{at}.config: expected data/strategy_<version>.yaml")
        specs.append(PortfolioSpec(name=name, config=config, setup=paper_setup(setup, f"{at}.setup")))
    names = [spec.name for spec in specs]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise PaperFileError(f"{where}: duplicate names {duplicates}")
    return specs
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_paper_file.py -q`
Expected: 8 passed, 2 skipped (`data/paper_v1.yaml` does not exist yet).

- [ ] **Step 5: Write `data/paper_v1.yaml`**

Create `data/paper_v1.yaml`:

```yaml
# Pre-registered forward paper portfolios (spec 07). Committed on its own before
# `signalbench paper start`. Do not edit: a new idea is a new portfolio in a new
# data/paper_<n>.yaml. v1-breakout and v2-t30-cash have the same rules, so their trades
# must match every night; any difference is a bug in the runner.
portfolios:
  - name: v1-breakout
    config: data/strategy_v1.yaml
    setup: breakout
  - name: v2-t30-cash
    config: data/strategy_v2-t30-cash.yaml
    setup: breakout
  - name: v2-t30-qqq
    config: data/strategy_v2-t30-qqq.yaml
    setup: breakout
  - name: v2-t60-cash
    config: data/strategy_v2-t60-cash.yaml
    setup: breakout
  - name: v2-t60-qqq
    config: data/strategy_v2-t60-qqq.yaml
    setup: breakout
  - name: v2-none-cash
    config: data/strategy_v2-none-cash.yaml
    setup: breakout
  - name: v2-none-qqq
    config: data/strategy_v2-none-qqq.yaml
    setup: breakout
```

- [ ] **Step 6: Run the tests and checks**

Run: `uv run pytest tests/test_paper_file.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 10 passed; the full suite 526 passed; ruff and mypy clean.

- [ ] **Step 7: Commit the loader and its tests, not the portfolios file**

```bash
git add src/signalbench/paper/__init__.py src/signalbench/paper/portfolios.py tests/test_paper_file.py
git commit -m "feat: the paper portfolios file loader

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Leave `data/paper_v1.yaml` untracked. `git status --short` lists `?? data/paper_v1.yaml` (plus the owner's `README.md` change).

---

### Task 4: `paper start`

**Files:**
- Create: `src/signalbench/paper/start.py`, `tests/paper_helpers.py`, `tests/test_paper_start.py`

- [ ] **Step 1: Write the test helper and the failing tests**

Create `tests/paper_helpers.py` (a throwaway git repo with the test config, a QQQ copy of it, and a paper file with three portfolios; `p-plain` and `p-twin` share a config, like `v1-breakout` and `v2-t30-cash`):

```python
"""A throwaway git repo with test configs and a paper file, shared by the paper tests."""

import subprocess
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from strategy_helpers import FIXTURE_CONFIG

NEW_YORK = ZoneInfo("America/New_York")
PAPER_FILE = "data/paper_test.yaml"
PORTFOLIOS = {  # name: (config, setup)
    "p-plain": ("data/strategy_test.yaml", "pullback"),
    "p-twin": ("data/strategy_test.yaml", "pullback"),
    "p-qqq": ("data/strategy_test-qqq.yaml", "pullback"),
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def make_repo(root: Path) -> Path:
    """A repo with the test config, a QQQ copy of it, and a paper file, all committed."""
    data = root / "data"
    data.mkdir(parents=True)
    plain = FIXTURE_CONFIG.read_text(encoding="utf-8")
    (data / "strategy_test.yaml").write_text(plain, encoding="utf-8")
    qqq = plain.replace("version: test\n", "version: test-qqq\n")
    qqq += "cash_vehicle:\n  symbol: QQQ\n  cost_per_side: 0.002\n"
    (data / "strategy_test-qqq.yaml").write_text(qqq, encoding="utf-8")
    lines = ["portfolios:"]
    for name, (config, setup) in PORTFOLIOS.items():
        lines += [f"  - name: {name}", f"    config: {config}", f"    setup: {setup}"]
    (root / PAPER_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "data")
    git(root, "commit", "-q", "-m", "configs")
    return root


def evening(day: date, hour: int = 18) -> datetime:
    """`hour`:00 New York time on `day`."""
    return datetime.combine(day, time(hour, 0), tzinfo=NEW_YORK)
```

Create `tests/test_paper_start.py`:

```python
"""`paper start` (spec 07): every portfolio or none, from committed files only, once."""

from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session, select

from paper_helpers import PAPER_FILE, PORTFOLIOS, evening, make_repo
from signalbench.backtest.sim_state import state_from_json
from signalbench.backtest.simulator import initial_state
from signalbench.db.models import PaperPortfolio
from signalbench.paper.start import PaperRefusedError, start_portfolios
from signalbench.strategy.config import load_strategy_config
from strategy_helpers import WeekdaySessions, weekdays

DAYS = weekdays(date(2023, 1, 2), 280)
FRIDAY = evening(DAYS[239])  # 2023-12-01; DAYS[240] is the Monday after


def _start(session: Session, repo: Path) -> list[PaperPortfolio]:
    return start_portfolios(
        session, paper_file=repo / PAPER_FILE, repo=repo, calendar=WeekdaySessions(), now=FRIDAY
    )


def test_start_creates_every_portfolio_on_the_next_session(session: Session, tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    created = _start(session, repo)
    assert [p.name for p in created] == list(PORTFOLIOS)
    for portfolio in created:
        config, sha = load_strategy_config(repo / portfolio.config_path)
        assert (portfolio.config_path, portfolio.setup) == PORTFOLIOS[portfolio.name]
        assert portfolio.config_sha256 == sha
        assert portfolio.started_on == DAYS[240]
        assert portfolio.last_session is None
        assert state_from_json(portfolio.state) == initial_state(config)


def test_start_refuses_to_run_twice(session: Session, tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    _start(session, repo)
    with pytest.raises(PaperRefusedError, match="already exist: p-plain, p-qqq, p-twin"):
        _start(session, repo)
    assert len(session.exec(select(PaperPortfolio)).all()) == 3


def test_start_refuses_an_uncommitted_paper_file(session: Session, tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    with (repo / PAPER_FILE).open("a", encoding="utf-8") as handle:
        handle.write("# edited\n")
    with pytest.raises(PaperRefusedError, match="paper_test.yaml must be committed, unchanged"):
        _start(session, repo)
    assert session.exec(select(PaperPortfolio)).all() == []


def test_start_refuses_an_uncommitted_config_and_creates_nothing(
    session: Session, tmp_path: Path
) -> None:
    repo = make_repo(tmp_path)
    with (repo / "data" / "strategy_test-qqq.yaml").open("a", encoding="utf-8") as handle:
        handle.write("# edited\n")
    with pytest.raises(PaperRefusedError, match="p-qqq: data/strategy_test-qqq.yaml must exist"):
        _start(session, repo)
    assert session.exec(select(PaperPortfolio)).all() == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_paper_start.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.paper.start'`.

- [ ] **Step 3: Write `start_portfolios()`**

Create `src/signalbench/paper/start.py`:

```python
"""`signalbench paper start` (spec 07, Commands): create the pre-registered portfolios."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from signalbench.backtest.preregistration import check_config_path
from signalbench.backtest.provenance import committed_unchanged
from signalbench.backtest.sim_state import state_to_json
from signalbench.backtest.simulator import initial_state
from signalbench.db.models import PaperPortfolio
from signalbench.market.calendar import Sessions
from signalbench.paper.portfolios import load_paper_file
from signalbench.strategy.config import load_strategy_config

NEW_YORK = ZoneInfo("America/New_York")


class PaperRefusedError(ValueError):
    """A paper command was refused. The message says why."""


def start_portfolios(
    session: Session, *, paper_file: Path, repo: Path, calendar: Sessions, now: datetime
) -> list[PaperPortfolio]:
    """Create every portfolio in `paper_file`, all starting on the first session after today
    (New York). All or nothing: refuses when the file or any config is not committed and
    unchanged, or when any of the portfolios already exists."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"now must be timezone-aware, got naive {now.isoformat()}")
    if not paper_file.exists():
        raise PaperRefusedError(f"{paper_file.name} not found.")
    if not committed_unchanged(repo, paper_file):
        raise PaperRefusedError(
            f"{paper_file.name} must be committed, unchanged, before `paper start` (spec 07)."
        )
    specs = load_paper_file(paper_file)
    taken = session.exec(
        select(PaperPortfolio.name).where(col(PaperPortfolio.name).in_([s.name for s in specs]))
    ).all()
    if taken:
        raise PaperRefusedError(
            f"Paper portfolios already exist: {', '.join(sorted(taken))}. A new idea is a new "
            "portfolio in a new data/paper_<n>.yaml (spec 07)."
        )
    started_on = calendar.next_sessions(now.astimezone(NEW_YORK).date(), 1)[0]
    portfolios: list[PaperPortfolio] = []
    for spec in specs:
        path = repo / spec.config
        if not path.exists() or not committed_unchanged(repo, path):
            raise PaperRefusedError(
                f"{spec.name}: {spec.config} must exist and be committed, unchanged (spec 07)."
            )
        config, sha = load_strategy_config(path)
        check_config_path(repo, path, config.version)
        portfolios.append(
            PaperPortfolio(
                name=spec.name,
                config_path=spec.config,
                config_sha256=sha,
                setup=spec.setup,
                started_on=started_on,
                state=state_to_json(initial_state(config)),
            )
        )
    session.add_all(portfolios)
    session.commit()
    for portfolio in portfolios:
        session.refresh(portfolio)
    return portfolios
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_paper_start.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 4 passed; the full suite 530 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/paper/start.py tests/paper_helpers.py tests/test_paper_start.py
git commit -m "feat: signalbench paper start creates the pre-registered portfolios

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The advisory lock

**Files:**
- Create: `src/signalbench/paper/lock.py`, `tests/test_paper_lock.py`

SQLite has no advisory locks. The test registers two Python functions under the Postgres names on every SQLite connection, so the real SQL runs unchanged against a shared set of held keys.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_lock.py`:

```python
"""The paper-run lock, on SQLite with stand-ins for the two Postgres lock functions."""

import sqlite3
from typing import Any

from sqlalchemy import Engine, create_engine, event

from signalbench.paper.lock import PAPER_RUN_LOCK, advisory_lock


def _engine(held: set[int]) -> Engine:
    """SQLite, where pg_try_advisory_lock and pg_advisory_unlock act on `held` (every
    connection shares it, as every Postgres session shares the server's locks)."""
    engine = create_engine("sqlite://")

    def try_lock(key: int) -> int:
        if key in held:
            return 0
        held.add(key)
        return 1

    def unlock(key: int) -> int:
        held.discard(key)
        return 1

    @event.listens_for(engine, "connect")
    def _functions(connection: sqlite3.Connection, _record: Any) -> None:
        connection.create_function("pg_try_advisory_lock", 1, try_lock)
        connection.create_function("pg_advisory_unlock", 1, unlock)

    return engine


def test_a_second_run_is_refused_while_the_first_holds_the_lock() -> None:
    held: set[int] = set()
    engine = _engine(held)
    with advisory_lock(engine) as first:
        assert first is True
        assert held == {PAPER_RUN_LOCK}
        with advisory_lock(engine) as second:
            assert second is False
        assert held == {PAPER_RUN_LOCK}  # the refused run does not release the holder's lock
    assert held == set()
    with advisory_lock(engine) as again:
        assert again is True


def test_the_lock_is_released_when_the_run_raises() -> None:
    held: set[int] = set()
    engine = _engine(held)
    try:
        with advisory_lock(engine):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert held == set()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_paper_lock.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.paper.lock'`.

- [ ] **Step 3: Write the lock**

Create `src/signalbench/paper/lock.py`:

```python
"""One `paper run` at a time (spec 07, Commands step 1): a Postgres session advisory lock."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text

PAPER_RUN_LOCK = 2026_0929_07  # an arbitrary bigint that names the paper-run lock


@contextmanager
def advisory_lock(engine: Engine, key: int = PAPER_RUN_LOCK) -> Iterator[bool]:
    """Yield True when this process holds the lock, False when another run does.

    The lock lives on its own connection, held open for the whole run, so the run's
    transactions do not release it; Postgres drops it if the process dies.
    """
    with engine.connect() as connection:
        held = bool(
            connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        )
        connection.commit()
        try:
            yield held
        finally:
            if held:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                connection.commit()
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_paper_lock.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 2 passed; the full suite 532 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/paper/lock.py tests/test_paper_lock.py
git commit -m "feat: a Postgres advisory lock so only one paper run steps at a time

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `paper run`: stepping, events, equity rows, and the run row

**Files:**
- Create: `src/signalbench/paper/run.py`, `tests/test_paper_run.py`

The tests store the pullback fixture's prices up to the Friday before the start, start three portfolios that evening, and then run with a fixed clock and a `Feed` that stores each night's bars, as `ingest prices` would. The lock is `nullcontext(True)` (held) or `nullcontext(False)` (held by another run).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_run.py`:

```python
"""`paper run` (spec 07) on synthetic prices that arrive one night at a time.

The pullback fixture: AAA signals on DAYS[251], fills at the DAYS[252] open, and exits for time
at the DAYS[262] open. The portfolios start on DAYS[240], the Monday after `paper start`.
"""

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, col, func, select

from paper_helpers import PAPER_FILE, evening, make_repo
from signalbench.backtest.runner import load_market_inputs
from signalbench.backtest.simulator import SimulationResult, simulate
from signalbench.db.models import (
    PaperEquity,
    PaperEvent,
    PaperPortfolio,
    PaperRun,
    Price,
    Ticker,
    TickerKind,
)
from signalbench.ingest.cdr import CdrEntry
from signalbench.market.bars import AdjustedBar
from signalbench.market.calendar import HISTORY_START
from signalbench.paper.run import RunOutcome, run_paper
from signalbench.paper.start import start_portfolios
from signalbench.strategy.config import load_strategy_config
from signalbench.strategy.market_view import MarketView
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    WeekdaySessions,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)

DAYS = weekdays(date(2023, 1, 2), 280)
FIRST, LAST = 240, 270
UNIVERSE = [
    CdrEntry("AAA", "ZAAA", "ZAAA.NE", "Aaa", "Information Technology"),
    CdrEntry("BBB", "ZBBB", "ZBBB.NE", "Bbb", "Energy"),
]
BARS = {
    "AAA": series(DAYS, pullback_closes(len(DAYS))),
    "BBB": trend_bars(DAYS, 50.0, 0.1),
    "QQQ": trend_bars(DAYS, 300.0, 0.5),
}
KINDS = {"AAA": TickerKind.us_stock, "BBB": TickerKind.us_stock, "QQQ": TickerKind.benchmark}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _price(ticker: Ticker, bar: AdjustedBar) -> Price:
    value = Decimal(str(round(bar.close, 4)))
    return Price(ticker_id=ticker.id, date=bar.date, open=Decimal(str(round(bar.open, 4))),
                 high=Decimal(str(round(bar.high, 4))), low=Decimal(str(round(bar.low, 4))),
                 close=value, adj_close=value, volume=bar.volume)


class Feed:
    """Stands in for `ingest prices`: stores every fixture bar dated on or before the clock's
    New York date that is not stored yet."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls = 0

    def __call__(self, session: Session) -> list[str]:
        self.calls += 1
        today = self.clock().date()
        for symbol, bars in BARS.items():
            ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
            last = session.exec(select(func.max(Price.date)).where(Price.ticker_id == ticker.id)).one()
            for bar in bars:
                if (last is None or bar.date > last) and bar.date <= today:
                    session.add(_price(ticker, bar))
        session.commit()
        return []


@pytest.fixture
def repo(session: Session, tmp_path: Path) -> Path:
    """Prices up to DAYS[FIRST - 1], and the three test portfolios started that evening."""
    for symbol, bars in BARS.items():
        ticker = Ticker(symbol=symbol, company_name=symbol, kind=KINDS[symbol])
        session.add(ticker)
        session.commit()
        session.refresh(ticker)
        session.add_all(_price(ticker, bar) for bar in bars[:FIRST])
        session.commit()
    root = make_repo(tmp_path / "repo")
    start_portfolios(session, paper_file=root / PAPER_FILE, repo=root, calendar=WeekdaySessions(),
                     now=evening(DAYS[FIRST - 1]))
    return root


def _run(
    session: Session, repo: Path, clock: Clock, ingest: Callable[[Session], list[str]] | None = None,
    held: bool = True,
) -> RunOutcome:
    return run_paper(
        session, lock=nullcontext(held), repo=repo, universe=UNIVERSE, calendar=WeekdaySessions(),
        clock=clock, ingest=ingest or Feed(clock), echo=lambda _line: None,
    )


def _portfolio(session: Session, name: str) -> PaperPortfolio:
    return session.exec(select(PaperPortfolio).where(PaperPortfolio.name == name)).one()


def _equity(session: Session, name: str) -> list[PaperEquity]:
    portfolio = _portfolio(session, name)
    return list(session.exec(
        select(PaperEquity).where(PaperEquity.portfolio_id == portfolio.id)
        .order_by(col(PaperEquity.session))
    ).all())


def _events(session: Session, name: str) -> list[PaperEvent]:
    portfolio = _portfolio(session, name)
    return list(session.exec(
        select(PaperEvent).where(PaperEvent.portfolio_id == portfolio.id).order_by(col(PaperEvent.id))
    ).all())


def test_a_run_steps_every_session_since_the_last_one_and_flags_catch_up(
    session: Session, repo: Path
) -> None:
    outcome = _run(session, repo, Clock(evening(DAYS[FIRST + 2])))
    assert (outcome.status, outcome.target) == ("ok", DAYS[FIRST + 2])
    assert outcome.stepped == {"p-plain": 3, "p-twin": 3, "p-qqq": 3}
    rows = _equity(session, "p-plain")
    assert [(r.session, r.catch_up) for r in rows] == [
        (DAYS[FIRST], True), (DAYS[FIRST + 1], True), (DAYS[FIRST + 2], False),
    ]
    assert _portfolio(session, "p-qqq").last_session == DAYS[FIRST + 2]
    [run] = session.exec(select(PaperRun)).all()
    assert (run.status, run.sessions_stepped, run.target_session, run.error) == (
        "ok", 9, DAYS[FIRST + 2], None,
    )
    assert run.finished_at is not None
    later = _run(session, repo, Clock(evening(DAYS[FIRST + 4])))
    assert later.stepped == {"p-plain": 2, "p-twin": 2, "p-qqq": 2}
    assert [r.catch_up for r in _equity(session, "p-plain")[3:]] == [True, False]


def test_a_second_run_the_same_night_steps_nothing(session: Session, repo: Path) -> None:
    clock = Clock(evening(DAYS[FIRST]))
    _run(session, repo, clock)
    events = len(session.exec(select(PaperEvent)).all())
    feed = Feed(clock)
    again = _run(session, repo, clock, feed)
    assert (again.status, again.stepped, feed.calls) == ("ok", {}, 0)
    assert len(session.exec(select(PaperEvent)).all()) == events
    assert len(_equity(session, "p-plain")) == 1
    assert [r.status for r in session.exec(select(PaperRun)).all()] == ["ok", "ok"]


def test_a_run_before_the_close_is_complete_targets_the_previous_session(
    session: Session, repo: Path
) -> None:
    outcome = _run(session, repo, Clock(evening(DAYS[FIRST + 1], hour=15)))
    assert (outcome.target, outcome.stepped["p-plain"]) == (DAYS[FIRST], 1)


def _simulated(session: Session, repo: Path, config_path: str) -> SimulationResult:
    config = load_strategy_config(repo / config_path)[0].with_setups(("pullback",))
    inputs = load_market_inputs(session, UNIVERSE, "QQQ", DAYS[LAST])
    calendar = WeekdaySessions()
    sessions = calendar.sessions_between(HISTORY_START, DAYS[LAST]) + calendar.next_sessions(DAYS[LAST], 3)
    market = MarketView(inputs.symbols, inputs.benchmark, sessions, config)
    return simulate(market, NullReadingsView(), config, DAYS[FIRST], DAYS[LAST])


def _without(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in keys}


@pytest.fixture
def nightly(session: Session, repo: Path) -> Path:
    """One run every evening from DAYS[FIRST] to DAYS[LAST]."""
    for i in range(FIRST, LAST + 1):
        assert _run(session, repo, Clock(evening(DAYS[i]))).status == "ok"
    return repo


@pytest.mark.parametrize(
    ("name", "config_path"),
    [("p-plain", "data/strategy_test.yaml"), ("p-qqq", "data/strategy_test-qqq.yaml")],
)
def test_nightly_runs_store_exactly_what_one_simulate_gives(
    session: Session, nightly: Path, name: str, config_path: str
) -> None:
    whole = _simulated(session, nightly, config_path)
    assert [(r.session, r.equity, r.cash, r.vehicle_value, r.open_positions)
            for r in _equity(session, name)] == [
        (p.date, p.equity, p.cash, p.vehicle_value, p.open_positions) for p in whole.equity_curve
    ]
    stored = [e for e in _events(session, name) if not e.kind.startswith("order_")]
    kinds = {"exit": "fill_exit", "entry": "fill_entry"}
    assert [(e.session.isoformat(), e.kind, _without(e.payload, "order_event_id", "trade"))
            for e in stored] == [
        (str(e["date"]), kinds.get(str(e["event"]), str(e["event"])), _without(e, "date", "event"))
        for e in whole.events
    ]
    [trade] = whole.trades
    [fill] = [e for e in stored if e.kind == "fill_exit"]
    expected = {k: v.isoformat() if isinstance(v, date) else v for k, v in asdict(trade).items()}
    assert fill.payload["trade"] == expected
    assert not any(e.catch_up for e in _events(session, name))


def test_the_twin_portfolios_match_every_night(session: Session, nightly: Path) -> None:
    def rows(name: str) -> list[tuple[date, str, dict[str, Any]]]:
        return [(e.session, e.kind, _without(e.payload, "order_event_id")) for e in _events(session, name)]

    assert rows("p-plain") == rows("p-twin")
    assert len(rows("p-plain")) > 10


def test_order_events_are_recorded_the_night_before_their_fills(
    session: Session, nightly: Path
) -> None:
    fills = [e for e in _events(session, "p-qqq") if e.kind in ("fill_exit", "fill_entry")]
    assert [e.kind for e in fills] == ["fill_entry", "fill_exit"]
    for fill in fills:
        order = session.get(PaperEvent, fill.payload["order_event_id"])
        assert order is not None and order.id is not None and fill.id is not None
        assert order.kind == fill.kind.replace("fill_", "order_")
        assert order.payload["symbol"] == fill.payload["symbol"] == "AAA"
        assert (order.id < fill.id, order.session < fill.session) == (True, True)
        assert order.recorded_at < fill.recorded_at
        assert order.recorded_at < evening(fill.session, hour=9)  # before the fill's open


def test_a_changed_config_fails_that_portfolio_and_the_others_still_step(
    session: Session, repo: Path
) -> None:
    with (repo / "data" / "strategy_test-qqq.yaml").open("a", encoding="utf-8") as handle:
        handle.write("# edited\n")
    outcome = _run(session, repo, Clock(evening(DAYS[FIRST])))
    assert outcome.status == "failed"
    assert outcome.stepped == {"p-plain": 1, "p-twin": 1}
    assert outcome.error is not None
    assert outcome.error.startswith(
        "PaperRefusedError: p-qqq: data/strategy_test-qqq.yaml no longer matches the config_sha256"
    )
    assert _portfolio(session, "p-qqq").last_session is None
    [run] = session.exec(select(PaperRun)).all()
    assert (run.status, run.error, run.sessions_stepped) == ("failed", outcome.error, 2)


def test_the_lock_refuses_a_concurrent_run(session: Session, repo: Path) -> None:
    clock = Clock(evening(DAYS[FIRST]))
    feed = Feed(clock)
    outcome = _run(session, repo, clock, feed, held=False)
    assert (outcome.status, outcome.run_id, feed.calls) == ("locked", None, 0)
    assert session.exec(select(PaperRun)).all() == []


def test_an_error_fails_the_run_and_is_recorded(session: Session, repo: Path) -> None:
    def broken(_session: Session) -> list[str]:
        raise RuntimeError("price feed down")

    outcome = _run(session, repo, Clock(evening(DAYS[FIRST])), broken)
    assert (outcome.status, outcome.error, outcome.stepped) == (
        "failed", "RuntimeError: price feed down", {},
    )
    [run] = session.exec(select(PaperRun)).all()
    assert (run.status, run.error, run.target_session) == (
        "failed", "RuntimeError: price feed down", DAYS[FIRST],
    )
    assert run.finished_at is not None


def test_prices_that_stop_before_the_target_fail_the_run(session: Session, repo: Path) -> None:
    outcome = _run(session, repo, Clock(evening(DAYS[FIRST])), lambda _session: [])
    assert outcome.status == "failed"
    assert outcome.error is not None and "do not end on the run's last session" in outcome.error
    assert _equity(session, "p-plain") == []


def test_a_run_without_portfolios_fails(session: Session) -> None:
    outcome = _run(session, Path("."), Clock(evening(DAYS[FIRST])))
    assert (outcome.status, outcome.error) == (
        "failed", "PaperRefusedError: No paper portfolios. Run `signalbench paper start` first.",
    )
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_paper_run.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.paper.run'`.

- [ ] **Step 3: Write the runner**

Create `src/signalbench/paper/run.py`:

```python
"""`signalbench paper run` (spec 07, Commands): step every portfolio to the last complete session.

Each stepped session is one transaction: the portfolio's saved state, the session's events,
and its equity row. Orders decided at a close are written that night, before the next open;
their fills the next night carry the order event's id. Rows written after the next session's
open (a missed night, or a late run) are marked `catch_up`.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from signalbench.backtest.runner import (
    MarketInputs,
    check_series_current,
    last_complete_session,
    load_market_inputs,
    setups_for_run,
)
from signalbench.backtest.sim_state import state_from_json, state_to_json
from signalbench.backtest.simulator import SimState, StepResult, step
from signalbench.db.models import PaperEquity, PaperEvent, PaperPortfolio, PaperRun
from signalbench.ingest.cdr import CdrEntry
from signalbench.market.calendar import HISTORY_START, Sessions
from signalbench.paper.portfolios import paper_setup
from signalbench.paper.start import PaperRefusedError
from signalbench.strategy.config import (
    StrategyConfig,
    config_sha256,
    load_strategy_config,
)
from signalbench.strategy.market_view import MarketView
from signalbench.strategy.readings import NullReadingsView

NEW_YORK = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)  # New York
FILLS = {"exit": "fill_exit", "entry": "fill_entry"}  # simulator event -> paper event kind
RunStatus = Literal["locked", "ok", "failed"]


@dataclass(frozen=True)
class RunOutcome:
    status: RunStatus
    run_id: int | None = None
    target: date | None = None
    stepped: dict[str, int] = field(default_factory=dict)  # sessions stepped per portfolio
    error: str | None = None


def run_paper(
    session: Session,
    *,
    lock: AbstractContextManager[bool],
    repo: Path,
    universe: list[CdrEntry],
    calendar: Sessions,
    clock: Callable[[], datetime],
    ingest: Callable[[Session], list[str]],
    echo: Callable[[str], None],
) -> RunOutcome:
    """The nightly job. `lock` yields False when another run holds it: nothing is done.
    `ingest` refreshes prices (and the liquidity flags) and returns what failed. `clock` must
    return timezone-aware times."""
    with lock as held:
        if not held:
            echo("Another paper run holds the lock; nothing to do.")
            return RunOutcome(status="locked")
        run = PaperRun(started_at=clock(), status="running")
        session.add(run)
        session.commit()
        session.refresh(run)
        stepped: dict[str, int] = {}
        try:
            refused = _step_all(session, run, repo, universe, calendar, clock, ingest, echo, stepped)
            if refused:
                raise PaperRefusedError("; ".join(refused))
        except Exception as error:  # noqa: BLE001  # spec 07: any error fails the run, recorded
            session.rollback()
            message = f"{type(error).__name__}: {error}"
            _finish(session, run, "failed", clock(), stepped, message)
            return RunOutcome("failed", run.id, run.target_session, stepped, message)
        _finish(session, run, "ok", clock(), stepped, None)
        return RunOutcome("ok", run.id, run.target_session, stepped, None)


def _step_all(
    session: Session,
    run: PaperRun,
    repo: Path,
    universe: list[CdrEntry],
    calendar: Sessions,
    clock: Callable[[], datetime],
    ingest: Callable[[Session], list[str]],
    echo: Callable[[str], None],
    stepped: dict[str, int],
) -> list[str]:
    """Step every portfolio that is behind the target. Returns the portfolios refused for a
    changed config; they are not stepped, and the others still are."""
    portfolios = session.exec(select(PaperPortfolio).order_by(col(PaperPortfolio.id))).all()
    if not portfolios:
        raise PaperRefusedError("No paper portfolios. Run `signalbench paper start` first.")
    target = last_complete_session(calendar, clock())
    run.target_session = target
    session.add(run)
    session.commit()
    behind = [p for p in portfolios if p.last_session is None or p.last_session < target]
    if not behind:
        echo(f"Every portfolio has stepped {target.isoformat()}; nothing to do.")
        return []
    failed = ingest(session)
    if failed:
        echo(f"ingest: {len(failed)} failed ({', '.join(failed)}); stepping on the stored prices")
    refused: list[str] = []
    inputs: dict[str, MarketInputs] = {}
    for portfolio in behind:
        path = repo / portfolio.config_path
        if not path.exists() or config_sha256(path.read_bytes()) != portfolio.config_sha256:
            refused.append(
                f"{portfolio.name}: {portfolio.config_path} no longer matches the config_sha256 "
                "stored at its start, so it was not stepped (spec 07: configs are never edited)"
            )
            continue
        config = load_strategy_config(path)[0].with_setups(
            setups_for_run(paper_setup(portfolio.setup, portfolio.name), "off")
        )
        symbol = config.regime_symbol
        if symbol not in inputs:
            inputs[symbol] = load_market_inputs(session, universe, symbol, target)
            check_series_current(inputs[symbol], symbol, target)
        market = _market(inputs[symbol], config, calendar, target)
        _step_portfolio(session, portfolio, config, market, calendar, target, clock, stepped)
        echo(f"{portfolio.name}: {stepped.get(portfolio.name, 0)} sessions to {target.isoformat()}")
    return refused


def _market(
    inputs: MarketInputs, config: StrategyConfig, calendar: Sessions, target: date
) -> MarketView:
    """Bars up to the target; the calendar runs on past it, as the backtest's does, for the
    earnings look-ahead (the session calendar is known in advance)."""
    lookahead = max(config.earnings_blackout_sessions, config.earnings_exit_sessions)
    sessions = calendar.sessions_between(HISTORY_START, target)
    sessions += calendar.next_sessions(target, lookahead)
    return MarketView(inputs.symbols, inputs.benchmark, sessions, config)


def _step_portfolio(
    session: Session,
    portfolio: PaperPortfolio,
    config: StrategyConfig,
    market: MarketView,
    calendar: Sessions,
    target: date,
    clock: Callable[[], datetime],
    stepped: dict[str, int],
) -> None:
    """Every session after the portfolio's last one, up to the target, in order: each is one
    transaction (state, events, and equity row together)."""
    assert portfolio.id is not None
    state = state_from_json(portfolio.state)
    last = portfolio.last_session
    days = [d for d in calendar.sessions_between(portfolio.started_on, target) if last is None or d > last]
    for day in days:
        exit_orders, entry_orders = _order_ids(session, portfolio.id, state)
        result = step(state, market, NullReadingsView(), config, day)
        recorded_at = clock()
        catch_up = recorded_at >= _next_open(calendar, day)
        _record(session, portfolio.id, day, result, exit_orders, entry_orders, recorded_at, catch_up)
        portfolio.state = state_to_json(result.state)
        portfolio.last_session = day
        session.add(portfolio)
        session.commit()
        state = result.state
        stepped[portfolio.name] = stepped.get(portfolio.name, 0) + 1


def _next_open(calendar: Sessions, day: date) -> datetime:
    return datetime.combine(calendar.next_sessions(day, 1)[0], MARKET_OPEN, tzinfo=NEW_YORK)


def _order_ids(
    session: Session, portfolio_id: int, state: SimState
) -> tuple[dict[str, int], dict[str, int]]:
    """The order events written at the last close: exits by position id, entries by symbol."""
    if state.orders is None:
        return {}, {}
    rows = session.exec(
        select(PaperEvent).where(
            PaperEvent.portfolio_id == portfolio_id,
            PaperEvent.session == state.orders.decided_on,
            col(PaperEvent.kind).in_(["order_exit", "order_entry"]),
        )
    ).all()
    exits = {str(r.payload["position_id"]): r.id for r in rows if r.kind == "order_exit" and r.id}
    entries = {str(r.payload["symbol"]): r.id for r in rows if r.kind == "order_entry" and r.id}
    return exits, entries


def _record(
    session: Session,
    portfolio_id: int,
    day: date,
    result: StepResult,
    exit_orders: dict[str, int],
    entry_orders: dict[str, int],
    recorded_at: datetime,
    catch_up: bool,
) -> None:
    """The session's events in the simulator's order, then the orders decided at its close,
    then its equity row."""
    trades = {trade.position_id: trade for trade in result.trades}

    def add(kind: str, payload: dict[str, Any]) -> None:
        session.add(
            PaperEvent(portfolio_id=portfolio_id, session=day, kind=kind, payload=payload,
                       recorded_at=recorded_at, catch_up=catch_up)
        )

    for event in result.events:
        kind = str(event["event"])
        payload = {key: value for key, value in event.items() if key not in ("date", "event")}
        if kind == "exit":
            position_id = str(event["position_id"])
            payload["order_event_id"] = exit_orders[position_id]
            payload["trade"] = _plain(asdict(trades[position_id]))
        elif kind == "entry":
            payload["order_event_id"] = entry_orders[str(event["symbol"])]
        add(FILLS.get(kind, kind), payload)
    orders = result.state.orders
    if orders is not None:
        for order in orders.exits:
            add("order_exit", {"position_id": order.position_id, "symbol": order.symbol,
                               "reason": order.reason})
        for entry in orders.entries:
            add("order_entry", _plain(asdict(entry)))
    point = result.point
    session.add(
        PaperEquity(portfolio_id=portfolio_id, session=day, equity=point.equity, cash=point.cash,
                    vehicle_value=point.vehicle_value, open_positions=point.open_positions,
                    catch_up=catch_up)
    )


def _plain(values: dict[str, Any]) -> dict[str, Any]:
    """JSON-ready: dates as ISO strings."""
    return {k: v.isoformat() if isinstance(v, date) else v for k, v in values.items()}


def _finish(
    session: Session,
    run: PaperRun,
    status: str,
    finished_at: datetime,
    stepped: dict[str, int],
    error: str | None,
) -> None:
    run.status = status
    run.finished_at = finished_at
    run.sessions_stepped = sum(stepped.values())
    run.error = error
    session.add(run)
    session.commit()
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_paper_run.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 12 passed; the full suite 544 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/paper/run.py tests/test_paper_run.py
git commit -m "feat: signalbench paper run steps each portfolio night by night

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The weekly report

**Files:**
- Create: `src/signalbench/paper/report.py`, `tests/test_paper_report.py`
- Modify: `src/signalbench/paper/run.py`, `tests/test_paper_run.py`

Every number in the hand-built fixture is worked by hand: equity 100, 101, 99, 102, 103 is +3.00% with a 2.0% worst fall (101 to 99); QQQ closes 500, 505, 495, 510, 515 make the same moves, so the same Sharpe (5.83); R 0.5 and −0.25 give mean R +0.125, a 50% win rate, and an average hold of (4 + 6) / 2 = 5.0 sessions; QQQ is half of equity every session.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_report.py`:

```python
"""The weekly paper report (spec 07), rendered from a hand-built portfolio."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlmodel import Session

from signalbench.db.models import (
    PaperEquity,
    PaperEvent,
    PaperPortfolio,
    PaperRun,
    Price,
    Ticker,
    TickerKind,
)
from signalbench.paper.report import previous_week, report_due, write_weekly_report

WEEK = [date(2026, 10, 12 + i) for i in range(5)]  # Monday to Friday
MONDAY_AFTER = date(2026, 10, 19)
EQUITY = [100.0, 101.0, 99.0, 102.0, 103.0]
QQQ_CLOSES = [500.0, 505.0, 495.0, 510.0, 515.0]  # the same moves as EQUITY: the same Sharpe
EXPECTED = """\
# Paper trading: weekly report, 2026-10-19

Information only. A portfolio is judged once it has 12 months since its start and at least 30 \
closed trades, whichever is later (spec 07, Judging); until then these numbers decide nothing.

Failed runs from 2026-10-12 to 2026-10-18: 1.

## Equity since the start

| Portfolio | Start | Last session | Sessions | Equity | Total return | Sharpe | Max drawdown \
| QQQ return | QQQ Sharpe | QQQ max drawdown |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| demo-qqq | 2026-10-12 | 2026-10-16 | 5 | 103.00 | +3.00% | 5.83 | 2.0% | +3.00% | 5.83 | 2.0% |
| demo-cash | 2026-10-12 | not stepped yet | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

## Trades since the start

| Portfolio | Closed trades | Mean R | Win rate | Average hold (sessions) | Open positions \
| Average share in QQQ | Catch-up sessions last week |
| --- | --- | --- | --- | --- | --- | --- | --- |
| demo-qqq | 2 | +0.125 | 50.0% | 5.0 | 1 | 50.0% | 2 |
| demo-cash | 0 | n/a | n/a | n/a | 0 | n/a | 0 |
"""


def _portfolio(session: Session, name: str) -> int:
    portfolio = PaperPortfolio(
        name=name, config_path="data/strategy_test.yaml", config_sha256="a" * 64,
        setup="breakout", started_on=WEEK[0], state={},
    )
    session.add(portfolio)
    session.commit()
    session.refresh(portfolio)
    assert portfolio.id is not None
    return portfolio.id


def _fixture(session: Session) -> None:
    demo = _portfolio(session, "demo-qqq")
    _portfolio(session, "demo-cash")
    for i, (day, equity) in enumerate(zip(WEEK, EQUITY, strict=True)):
        session.add(
            PaperEquity(portfolio_id=demo, session=day, equity=equity, cash=0.0,
                        vehicle_value=equity / 2, open_positions=1 if i == 4 else 0,
                        catch_up=i in (1, 2))
        )
    for day, r, held in ((WEEK[2], 0.5, 4), (WEEK[4], -0.25, 6)):
        session.add(
            PaperEvent(portfolio_id=demo, session=day, kind="fill_exit",
                       payload={"trade": {"r": r, "sessions_held": held}},
                       recorded_at=datetime(2026, 10, 20, tzinfo=UTC), catch_up=False)
        )
    demo_row = session.get(PaperPortfolio, demo)
    assert demo_row is not None
    demo_row.last_session = WEEK[-1]
    session.add(demo_row)
    session.add(PaperRun(started_at=datetime(2026, 10, 14, 22, tzinfo=UTC), status="failed"))
    session.add(PaperRun(started_at=datetime(2026, 10, 20, 22, tzinfo=UTC), status="failed"))
    session.add(PaperRun(started_at=datetime(2026, 10, 15, 22, tzinfo=UTC), status="ok"))
    qqq = Ticker(symbol="QQQ", company_name="QQQ", kind=TickerKind.benchmark)
    session.add(qqq)
    session.commit()
    session.refresh(qqq)
    for day, close in zip(WEEK, QQQ_CLOSES, strict=True):
        value = Decimal(str(close))
        session.add(Price(ticker_id=qqq.id, date=day, open=value, high=value, low=value,
                          close=value, adj_close=value, volume=1_000_000))
    session.commit()


def test_the_weekly_report_on_a_hand_built_portfolio(session: Session, tmp_path: Path) -> None:
    _fixture(session)
    path = write_weekly_report(session, tmp_path / "paper", MONDAY_AFTER)
    assert path == tmp_path / "paper" / "2026-10-19-weekly.md"
    assert path.read_text(encoding="utf-8") == EXPECTED


def test_the_report_covers_the_iso_week_before_the_one_it_is_written_in() -> None:
    assert previous_week(date(2026, 10, 19)) == (date(2026, 10, 12), date(2026, 10, 18))
    assert previous_week(date(2026, 10, 25)) == (date(2026, 10, 12), date(2026, 10, 18))


def test_a_report_is_due_once_per_iso_week(tmp_path: Path) -> None:
    reports = tmp_path / "paper"
    assert report_due(reports, date(2026, 10, 21))  # no folder yet
    reports.mkdir()
    (reports / "2026-10-12-weekly.md").write_text("last week\n", encoding="utf-8")
    (reports / "notes.md").write_text("not a report\n", encoding="utf-8")
    assert report_due(reports, date(2026, 10, 21))
    (reports / "2026-10-20-weekly.md").write_text("this week\n", encoding="utf-8")
    assert not report_due(reports, date(2026, 10, 21))
    assert not report_due(reports, date(2026, 10, 25))  # Sunday, same ISO week
    assert report_due(reports, date(2026, 10, 26))
```

In `tests/test_paper_run.py`, replace:

```python
        clock=clock, ingest=ingest or Feed(clock), echo=lambda _line: None,
    )
```

with:

```python
        clock=clock, ingest=ingest or Feed(clock), reports_dir=repo / "reports" / "paper",
        echo=lambda _line: None,
    )
```

In `tests/test_paper_run.py`, replace:

```python
def test_a_run_without_portfolios_fails(session: Session) -> None:
    outcome = _run(session, Path("."), Clock(evening(DAYS[FIRST])))
```

with:

```python
def test_a_run_without_portfolios_fails(session: Session, tmp_path: Path) -> None:
    outcome = _run(session, tmp_path, Clock(evening(DAYS[FIRST])))
```

Append to `tests/test_paper_run.py`:

```python
def test_the_first_run_of_each_iso_week_writes_the_weekly_report(
    session: Session, repo: Path
) -> None:
    reports = repo / "reports" / "paper"
    monday = _run(session, repo, Clock(evening(DAYS[FIRST])))
    assert monday.report == reports / f"{DAYS[FIRST].isoformat()}-weekly.md"
    assert "| p-qqq | 2023-12-04 | 2023-12-04 | 1 |" in monday.report.read_text(encoding="utf-8")
    for i in range(FIRST + 1, FIRST + 5):  # Tuesday to Friday: no new report
        assert _run(session, repo, Clock(evening(DAYS[i]))).report is None
    next_monday = _run(session, repo, Clock(evening(DAYS[FIRST + 5])))
    assert next_monday.report == reports / f"{DAYS[FIRST + 5].isoformat()}-weekly.md"
    assert sorted(path.name for path in reports.iterdir()) == [
        "2023-12-04-weekly.md", "2023-12-11-weekly.md",
    ]


def test_a_refused_portfolio_does_not_stop_the_weekly_report(session: Session, repo: Path) -> None:
    with (repo / "data" / "strategy_test-qqq.yaml").open("a", encoding="utf-8") as handle:
        handle.write("# edited\n")
    outcome = _run(session, repo, Clock(evening(DAYS[FIRST])))
    assert outcome.status == "failed"
    assert outcome.report is not None
    assert "| p-qqq | 2023-12-04 | not stepped yet | 0 |" in outcome.report.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_paper_report.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.paper.report'`.

Run: `uv run pytest tests/test_paper_run.py -q`
Expected: 10 failed, 4 errors: every run now passes `reports_dir`, so `TypeError: run_paper() got an unexpected keyword argument 'reports_dir'` (the four errors are the tests that use the `nightly` fixture).

- [ ] **Step 3: Write the report**

Create `src/signalbench/paper/report.py`:

```python
"""The weekly paper report, `reports/paper/<date>-weekly.md` (spec 07, Weekly report).

Written on the first run of each ISO week. Every number is since the portfolio's start, next
to QQQ bought at the close of its first session and held over the same sessions, measured as
the backtest measures a run (from the first close). Information only until the judging rule
is met.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import fmean
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from signalbench.backtest.benchmarks import (
    BenchmarkStats,
    benchmark_stats,
    buy_and_hold,
)
from signalbench.backtest.metrics import max_drawdown, sharpe
from signalbench.db.models import (
    PaperEquity,
    PaperEvent,
    PaperPortfolio,
    PaperRun,
    Ticker,
)
from signalbench.market.bars import adjusted_bars

NEW_YORK = ZoneInfo("America/New_York")
BENCHMARK = "QQQ"  # spec 07: QQQ buy-and-hold is the benchmark
REPORT_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-weekly\.md$")
JUDGING = (
    "Information only. A portfolio is judged once it has 12 months since its start and at "
    "least 30 closed trades, whichever is later (spec 07, Judging); until then these numbers "
    "decide nothing."
)


@dataclass(frozen=True)
class PortfolioSummary:
    name: str
    started_on: date
    last_session: date | None
    sessions: int
    equity: float | None  # None (and the three below) before the first stepped session
    total_return: float | None
    sharpe: float | None
    max_drawdown: float | None
    closed_trades: int
    mean_r: float | None
    win_rate: float | None
    average_hold: float | None  # sessions, the entry session included
    open_positions: int
    vehicle_share: float | None  # average share of equity in QQQ; None without a vehicle
    catch_up_sessions: int  # in the week the report covers
    benchmark: BenchmarkStats | None


def previous_week(today: date) -> tuple[date, date]:
    """Monday and Sunday of the ISO week before `today`'s."""
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(days=7), monday - timedelta(days=1)


def report_due(reports_dir: Path, today: date) -> bool:
    """True unless a weekly report is already dated in `today`'s ISO week."""
    monday = today - timedelta(days=today.weekday())
    for path in reports_dir.glob("*-weekly.md") if reports_dir.exists() else []:
        match = REPORT_NAME.match(path.name)
        if match and monday <= date.fromisoformat(match.group(1)) <= monday + timedelta(days=6):
            return False
    return True


def summarize(session: Session, portfolio: PaperPortfolio, week: tuple[date, date]) -> PortfolioSummary:
    rows = session.exec(
        select(PaperEquity).where(PaperEquity.portfolio_id == portfolio.id)
        .order_by(col(PaperEquity.session))
    ).all()
    fills = session.exec(
        select(PaperEvent).where(PaperEvent.portfolio_id == portfolio.id, PaperEvent.kind == "fill_exit")
    ).all()
    rs = [float(fill.payload["trade"]["r"]) for fill in fills]
    holds = [int(fill.payload["trade"]["sessions_held"]) for fill in fills]
    values = [row.equity for row in rows]
    sessions = [row.session for row in rows]
    shares = [row.vehicle_value / row.equity for row in rows if row.equity > 0.0]
    uses_vehicle = any(row.vehicle_value > 0.0 for row in rows)
    return PortfolioSummary(
        name=portfolio.name,
        started_on=portfolio.started_on,
        last_session=portfolio.last_session,
        sessions=len(rows),
        equity=values[-1] if values else None,
        total_return=values[-1] / values[0] - 1.0 if values else None,
        sharpe=sharpe(values) if values else None,
        max_drawdown=max_drawdown(values) if values else None,
        closed_trades=len(rs),
        mean_r=fmean(rs) if rs else None,
        win_rate=sum(1 for r in rs if r > 0.0) / len(rs) if rs else None,
        average_hold=fmean(holds) if holds else None,
        open_positions=rows[-1].open_positions if rows else 0,
        vehicle_share=fmean(shares) if uses_vehicle and shares else None,
        catch_up_sessions=sum(1 for row in rows if row.catch_up and week[0] <= row.session <= week[1]),
        benchmark=_benchmark(session, sessions),
    )


def _benchmark(session: Session, sessions: list[date]) -> BenchmarkStats | None:
    if not sessions:
        return None
    ticker = session.exec(select(Ticker).where(Ticker.symbol == BENCHMARK)).first()
    if ticker is None:
        return None
    bars = adjusted_bars(session, ticker.id, end=sessions[-1])
    return benchmark_stats(f"{BENCHMARK} buy-and-hold", buy_and_hold(bars, sessions), sessions)


def failed_runs(session: Session, week: tuple[date, date]) -> int:
    runs = session.exec(select(PaperRun).where(PaperRun.status == "failed")).all()
    return sum(1 for run in runs if week[0] <= run.started_at.astimezone(NEW_YORK).date() <= week[1])


def _pct(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2%}" if signed else f"{value:.1%}"


def _number(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def render_weekly_report(
    written_on: date, week: tuple[date, date], failed: int, summaries: list[PortfolioSummary]
) -> str:
    lines = [
        f"# Paper trading: weekly report, {written_on.isoformat()}",
        "",
        JUDGING,
        "",
        f"Failed runs from {week[0].isoformat()} to {week[1].isoformat()}: {failed}.",
        "",
        "## Equity since the start",
        "",
        (
            "| Portfolio | Start | Last session | Sessions | Equity | Total return | Sharpe "
            "| Max drawdown | QQQ return | QQQ Sharpe | QQQ max drawdown |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in summaries:
        last = "not stepped yet" if s.last_session is None else s.last_session.isoformat()
        qqq = s.benchmark
        lines.append(
            f"| {s.name} | {s.started_on.isoformat()} | {last} | {s.sessions} "
            f"| {_number(s.equity, '.2f')} | {_pct(s.total_return, signed=True)} "
            f"| {_number(s.sharpe, '.2f')} | {_pct(s.max_drawdown)} "
            f"| {_pct(None if qqq is None else qqq.total_return, signed=True)} "
            f"| {_number(None if qqq is None else qqq.sharpe, '.2f')} "
            f"| {_pct(None if qqq is None else qqq.max_drawdown)} |"
        )
    lines += [
        "",
        "## Trades since the start",
        "",
        (
            "| Portfolio | Closed trades | Mean R | Win rate | Average hold (sessions) "
            "| Open positions | Average share in QQQ | Catch-up sessions last week |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in summaries:
        lines.append(
            f"| {s.name} | {s.closed_trades} | {_number(s.mean_r, '+.3f')} | {_pct(s.win_rate)} "
            f"| {_number(s.average_hold, '.1f')} | {s.open_positions} | {_pct(s.vehicle_share)} "
            f"| {s.catch_up_sessions} |"
        )
    return "\n".join(lines) + "\n"


def write_weekly_report(session: Session, reports_dir: Path, today: date) -> Path:
    week = previous_week(today)
    portfolios = session.exec(select(PaperPortfolio).order_by(col(PaperPortfolio.id))).all()
    text = render_weekly_report(
        today, week, failed_runs(session, week), [summarize(session, p, week) for p in portfolios]
    )
    path = reports_dir / f"{today.isoformat()}-weekly.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
```

- [ ] **Step 4: Write the report from `run_paper()`**

In `src/signalbench/paper/run.py`, replace:

```python
from signalbench.paper.portfolios import paper_setup
```

with:

```python
from signalbench.paper.portfolios import paper_setup
from signalbench.paper.report import report_due, write_weekly_report
```

In `src/signalbench/paper/run.py`, replace:

```python
    stepped: dict[str, int] = field(default_factory=dict)  # sessions stepped per portfolio
    error: str | None = None
```

with:

```python
    stepped: dict[str, int] = field(default_factory=dict)  # sessions stepped per portfolio
    error: str | None = None
    report: Path | None = None  # the weekly report, when this run wrote one
```

In `src/signalbench/paper/run.py`, replace:

```python
    ingest: Callable[[Session], list[str]],
    echo: Callable[[str], None],
) -> RunOutcome:
    """The nightly job. `lock` yields False when another run holds it: nothing is done.
    `ingest` refreshes prices (and the liquidity flags) and returns what failed. `clock` must
    return timezone-aware times."""
```

with:

```python
    ingest: Callable[[Session], list[str]],
    reports_dir: Path,
    echo: Callable[[str], None],
) -> RunOutcome:
    """The nightly job. `lock` yields False when another run holds it: nothing is done.
    `ingest` refreshes prices (and the liquidity flags) and returns what failed. `clock` must
    return timezone-aware times. The first run of an ISO week writes the weekly report into
    `reports_dir`, after stepping (a portfolio refused for its config does not stop it)."""
```

In `src/signalbench/paper/run.py`, replace:

```python
        stepped: dict[str, int] = {}
        try:
            refused = _step_all(session, run, repo, universe, calendar, clock, ingest, echo, stepped)
            if refused:
                raise PaperRefusedError("; ".join(refused))
```

with:

```python
        stepped: dict[str, int] = {}
        report: Path | None = None
        try:
            refused = _step_all(session, run, repo, universe, calendar, clock, ingest, echo, stepped)
            today = clock().astimezone(NEW_YORK).date()
            if report_due(reports_dir, today):
                report = write_weekly_report(session, reports_dir, today)
                echo(f"weekly report: {report}")
            if refused:
                raise PaperRefusedError("; ".join(refused))
```

In `src/signalbench/paper/run.py`, replace:

```python
            return RunOutcome("failed", run.id, run.target_session, stepped, message)
        _finish(session, run, "ok", clock(), stepped, None)
        return RunOutcome("ok", run.id, run.target_session, stepped, None)
```

with:

```python
            return RunOutcome("failed", run.id, run.target_session, stepped, message, report)
        _finish(session, run, "ok", clock(), stepped, None)
        return RunOutcome("ok", run.id, run.target_session, stepped, None, report)
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_paper_report.py tests/test_paper_run.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 17 passed; the full suite 549 passed; ruff and mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/paper/report.py src/signalbench/paper/run.py tests/test_paper_report.py tests/test_paper_run.py
git commit -m "feat: the weekly paper report, written on the first run of each ISO week

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `paper status` and the stale check

**Files:**
- Create: `src/signalbench/paper/status.py`, `tests/test_paper_status.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_status.py`:

```python
"""`paper status` (spec 07): one line per portfolio, days until judgeable, and the stale check."""

from datetime import UTC, date, datetime

from sqlmodel import Session

from signalbench.db.models import PaperEquity, PaperEvent, PaperPortfolio, PaperRun
from signalbench.paper.status import judge_date, judgeable, stale_message, status_lines

START = date(2026, 10, 12)
NOW = datetime(2026, 10, 20, 22, 0, tzinfo=UTC)


def _portfolio(session: Session, name: str, created_at: datetime = NOW) -> int:
    portfolio = PaperPortfolio(
        name=name, config_path="data/strategy_test.yaml", config_sha256="a" * 64,
        setup="breakout", started_on=START, state={}, created_at=created_at,
    )
    session.add(portfolio)
    session.commit()
    session.refresh(portfolio)
    assert portfolio.id is not None
    return portfolio.id


def test_one_line_per_portfolio(session: Session) -> None:
    stepped = _portfolio(session, "p-stepped")
    _portfolio(session, "p-new")
    for day, equity, held in ((START, 100.0, 0), (date(2026, 10, 13), 101.5, 1)):
        session.add(PaperEquity(portfolio_id=stepped, session=day, equity=equity, cash=0.0,
                                vehicle_value=0.0, open_positions=held, catch_up=False))
    session.add(PaperEvent(portfolio_id=stepped, session=START, kind="fill_exit", payload={},
                           recorded_at=NOW, catch_up=False))
    session.add(PaperRun(started_at=datetime(2026, 10, 13, 22, 0, tzinfo=UTC), status="ok"))
    session.commit()
    assert status_lines(session, date(2026, 10, 20)) == [
        "last ok run: 2026-10-13T22:00+00:00",
        (
            "p-stepped: last 2026-10-13 | equity 101.50 | return +1.50% | open 1 | closed 1 "
            "| judgeable after 357 days and 29 more closed trades"
        ),
        "p-new: starts 2026-10-12, not stepped yet | judgeable after 357 days and 30 more closed trades",
    ]


def test_judgeable_counts_twelve_months_and_thirty_closed_trades() -> None:
    assert judge_date(date(2026, 10, 12)) == date(2027, 10, 12)
    assert judge_date(date(2028, 2, 29)) == date(2029, 2, 28)
    assert judgeable(date(2025, 1, 1), 30, date(2026, 1, 2)) == "judgeable now"
    assert judgeable(date(2025, 1, 1), 12, date(2026, 1, 2)) == "judgeable after 18 more closed trades"
    assert judgeable(date(2025, 1, 1), 45, date(2025, 12, 30)) == "judgeable after 2 days"


def test_stale_when_no_run_has_succeeded_for_more_than_three_days(session: Session) -> None:
    assert stale_message(session, NOW, 3) is None  # no portfolios: nothing to watch
    _portfolio(session, "p1", created_at=datetime(2026, 10, 18, 22, 0, tzinfo=UTC))
    assert stale_message(session, NOW, 3) is None  # created two days ago, no run yet
    assert stale_message(session, datetime(2026, 10, 22, 23, 0, tzinfo=UTC), 3) == (
        "STALE: the last ok paper run was never, more than 3 days ago."
    )
    session.add(PaperRun(started_at=datetime(2026, 10, 16, 22, 0, tzinfo=UTC), status="ok"))
    session.add(PaperRun(started_at=datetime(2026, 10, 19, 22, 0, tzinfo=UTC), status="failed"))
    session.commit()
    assert stale_message(session, NOW, 3) == (
        "STALE: the last ok paper run was on 2026-10-16T22:00+00:00, more than 3 days ago."
    )
    session.add(PaperRun(started_at=datetime(2026, 10, 19, 23, 0, tzinfo=UTC), status="ok"))
    session.commit()
    assert stale_message(session, NOW, 3) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_paper_status.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.paper.status'`.

- [ ] **Step 3: Write the status lines**

Create `src/signalbench/paper/status.py`:

```python
"""`signalbench paper status` (spec 07, Commands): one line per portfolio, and the stale check
the nightly script uses."""

from datetime import date, datetime, timedelta

from sqlmodel import Session, col, func, select

from signalbench.db.models import PaperEquity, PaperEvent, PaperPortfolio, PaperRun

JUDGE_TRADES = 30  # spec 07, Judging: at least 30 closed trades, and 12 months since the start


def judge_date(started_on: date) -> date:
    """12 months after the start (a 29 February start becomes 28 February)."""
    try:
        return started_on.replace(year=started_on.year + 1)
    except ValueError:
        return started_on.replace(year=started_on.year + 1, day=28)


def judgeable(started_on: date, closed_trades: int, today: date) -> str:
    days = max(0, (judge_date(started_on) - today).days)
    more = max(0, JUDGE_TRADES - closed_trades)
    if days == 0 and more == 0:
        return "judgeable now"
    waits = ([f"{days} days"] if days else []) + ([f"{more} more closed trades"] if more else [])
    return "judgeable after " + " and ".join(waits)


def last_ok_run(session: Session) -> datetime | None:
    return session.exec(select(func.max(PaperRun.started_at)).where(PaperRun.status == "ok")).one()


def stale_message(session: Session, now: datetime, days: int) -> str | None:
    """A warning when no `paper run` has succeeded for more than `days` days (counted from the
    portfolios' creation when none has succeeded yet); None otherwise, or with no portfolios."""
    last = last_ok_run(session)
    created = session.exec(select(func.min(PaperPortfolio.created_at))).one()
    since = last if last is not None else created
    if since is None or now - since <= timedelta(days=days):
        return None
    when = "never" if last is None else f"on {last.isoformat(timespec='minutes')}"
    return f"STALE: the last ok paper run was {when}, more than {days} days ago."


def status_lines(session: Session, today: date) -> list[str]:
    last = last_ok_run(session)
    lines = [f"last ok run: {'none' if last is None else last.isoformat(timespec='minutes')}"]
    portfolios = session.exec(select(PaperPortfolio).order_by(col(PaperPortfolio.id))).all()
    for portfolio in portfolios:
        rows = session.exec(
            select(PaperEquity).where(PaperEquity.portfolio_id == portfolio.id)
            .order_by(col(PaperEquity.session))
        ).all()
        closed = len(session.exec(
            select(PaperEvent.id).where(
                PaperEvent.portfolio_id == portfolio.id, PaperEvent.kind == "fill_exit"
            )
        ).all())
        wait = judgeable(portfolio.started_on, closed, today)
        if not rows:
            lines.append(f"{portfolio.name}: starts {portfolio.started_on.isoformat()}, not stepped yet | {wait}")
            continue
        first, latest = rows[0], rows[-1]
        lines.append(
            f"{portfolio.name}: last {latest.session.isoformat()} | equity {latest.equity:.2f} "
            f"| return {latest.equity / first.equity - 1.0:+.2%} | open {latest.open_positions} "
            f"| closed {closed} | {wait}"
        )
    return lines
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_paper_status.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 3 passed; the full suite 552 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/paper/status.py tests/test_paper_status.py
git commit -m "feat: paper status lines and the stale-run check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The CLI: `paper start`, `paper run`, `paper status`

**Files:**
- Modify: `src/signalbench/cli.py`
- Create: `tests/test_paper_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_cli.py`:

```python
"""`signalbench paper start|run|status` wiring (spec 07). The logic is tested in test_paper_*."""

from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session
from typer.testing import CliRunner

from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import PaperPortfolio

runner = CliRunner()


def _use(monkeypatch: pytest.MonkeyPatch, session: Session, held: bool = True) -> list[int]:
    """The test session, a lock that is `held` (or not), and an ingest that counts its calls."""
    calls: list[int] = []
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "advisory_lock", lambda _engine: nullcontext(held))
    monkeypatch.setattr(cli, "_ingest_prices", lambda _session: calls.append(1) or [])
    return calls


def test_paper_help_lists_start_run_and_status() -> None:
    result = runner.invoke(app, ["paper", "--help"])
    assert result.exit_code == 0
    for command in ("start", "run", "status"):
        assert command in result.stdout


def test_paper_run_without_portfolios_fails_with_one_error_line(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _use(monkeypatch, session)
    monkeypatch.setattr(cli, "PAPER_REPORTS_DIR", tmp_path / "paper")
    result = runner.invoke(app, ["paper", "run"])
    assert result.exit_code == 1
    assert result.stderr == (
        "ERROR: PaperRefusedError: No paper portfolios. Run `signalbench paper start` first.\n"
    )
    assert calls == []


def test_paper_run_exits_0_when_another_run_holds_the_lock(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _use(monkeypatch, session, held=False)
    result = runner.invoke(app, ["paper", "run"])
    assert (result.exit_code, result.stdout) == (0, "Another paper run holds the lock; nothing to do.\n")
    assert calls == []


def test_paper_run_reports_the_first_line_when_the_database_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down() -> Session:
        raise RuntimeError('connection failed: port 1 refused\nIs the server running on "127.0.0.1"?')

    monkeypatch.setattr(cli, "get_session", down)
    result = runner.invoke(app, ["paper", "run"])
    assert result.exit_code == 1
    assert result.stderr == "ERROR: RuntimeError: connection failed: port 1 refused\n"


def test_paper_status_reports_the_first_line_when_the_database_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down() -> Session:
        raise RuntimeError("connection failed: port 1 refused\nmore detail")

    monkeypatch.setattr(cli, "get_session", down)
    result = runner.invoke(app, ["paper", "status", "--stale-after-days", "3"])
    assert result.exit_code == 1
    assert result.stderr == "ERROR: RuntimeError: connection failed: port 1 refused\n"


def test_paper_start_refuses_a_missing_paper_file(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _use(monkeypatch, session)
    monkeypatch.setattr(cli, "PAPER_V1_PATH", tmp_path / "data" / "paper_v1.yaml")
    result = runner.invoke(app, ["paper", "start"])
    assert (result.exit_code, result.stderr) == (1, "paper_v1.yaml not found.\n")


def test_paper_status_exits_3_when_runs_are_stale(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, session)
    session.add(
        PaperPortfolio(name="p1", config_path="data/strategy_test.yaml", config_sha256="a" * 64,
                       setup="breakout", started_on=date(2026, 10, 12), state={},
                       created_at=datetime.now(UTC) - timedelta(days=5))
    )
    session.commit()
    plain = runner.invoke(app, ["paper", "status"])
    assert plain.exit_code == 0
    assert plain.stdout.splitlines()[0] == "last ok run: none"
    assert plain.stdout.splitlines()[1].startswith("p1: starts 2026-10-12, not stepped yet")
    checked = runner.invoke(app, ["paper", "status", "--stale-after-days", "3"])
    assert checked.exit_code == 3
    assert checked.stderr == "STALE: the last ok paper run was never, more than 3 days ago.\n"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_paper_cli.py -q`
Expected: 7 failed: `AttributeError: <module 'signalbench.cli' …> has no attribute 'advisory_lock'` for the four that patch the lock, and exit code 2 (`No such command 'paper'`) for the help test and the two database-down tests.

- [ ] **Step 3: Wire the commands**

In `src/signalbench/cli.py`, replace:

```python
from signalbench.db.session import get_session
```

with:

```python
from signalbench.db.session import engine, get_session
```

In `src/signalbench/cli.py`, replace:

```python
from signalbench.market.legal_close import LegalCloses
```

with:

```python
from signalbench.market.legal_close import LegalCloses
from signalbench.paper.lock import advisory_lock
from signalbench.paper.run import run_paper
from signalbench.paper.start import start_portfolios
from signalbench.paper.status import stale_message, status_lines
```

In `src/signalbench/cli.py`, replace:

```python
JEV_REPORTS_DIR = REPO_ROOT / "reports" / "jev"
```

with:

```python
JEV_REPORTS_DIR = REPO_ROOT / "reports" / "jev"
PAPER_V1_PATH = REPO_ROOT / "data" / "paper_v1.yaml"
PAPER_REPORTS_DIR = REPO_ROOT / "reports" / "paper"
STALE_EXIT = 3  # `paper status --stale-after-days`: no ok paper run for too long
```

In `src/signalbench/cli.py`, replace:

```python
jev_app = typer.Typer(help="Jev reads filings and news (spec 03).")
app.add_typer(jev_app, name="jev")
```

with:

```python
jev_app = typer.Typer(help="Jev reads filings and news (spec 03).")
app.add_typer(jev_app, name="jev")

paper_app = typer.Typer(help="Forward paper trading of the pre-registered portfolios (spec 07).")
app.add_typer(paper_app, name="paper")
```

Append to `src/signalbench/cli.py`:

```python
def _first_line(error: BaseException) -> str:
    lines = str(error).strip().splitlines()
    return lines[0] if lines else ""


@paper_app.command("start")
def paper_start() -> None:
    """Create the portfolios in data/paper_v1.yaml; they start on the next NYSE session."""
    try:
        with get_session() as session:
            created = start_portfolios(
                session, paper_file=PAPER_V1_PATH, repo=REPO_ROOT, calendar=NyseSessions(), now=_now()
            )
            lines = [
                f"{p.name}: starts {p.started_on.isoformat()} | {p.config_path} "
                f"| config_sha256 {p.config_sha256[:12]}"
                for p in created
            ]
    except (ValueError, yaml.YAMLError) as error:  # PaperRefusedError, PaperFileError, ConfigError
        typer.echo(" ".join(str(error).split()), err=True)
        raise typer.Exit(1) from None
    for line in lines:
        typer.echo(line)


@paper_app.command("run")
def paper_run() -> None:
    """The nightly job: ingest prices, step every portfolio to the last complete session, and
    write the weekly report on the first run of an ISO week. Safe to rerun."""
    try:
        with get_session() as session:
            outcome = run_paper(
                session,
                lock=advisory_lock(engine),
                repo=REPO_ROOT,
                universe=load_universe(UNIVERSE_PATH),
                calendar=NyseSessions(),
                clock=_now,
                ingest=_ingest_prices,
                reports_dir=PAPER_REPORTS_DIR,
                echo=typer.echo,
            )
    except Exception as error:  # noqa: BLE001  # e.g. the database is down: no run row to mark
        typer.echo(f"ERROR: {type(error).__name__}: {_first_line(error)}", err=True)
        raise typer.Exit(1) from None
    if outcome.status == "failed":
        typer.echo(f"ERROR: {outcome.error}", err=True)
        raise typer.Exit(1)
    if outcome.status == "ok" and outcome.target is not None:
        stepped = sum(outcome.stepped.values())
        typer.echo(f"paper run {outcome.run_id}: ok, {stepped} sessions stepped to {outcome.target}")


@paper_app.command("status")
def paper_status(
    stale_after_days: Annotated[
        int | None,
        typer.Option(
            "--stale-after-days",
            min=1,
            help="Exit 3 when no paper run has succeeded for more than this many days.",
        ),
    ] = None,
) -> None:
    """One line per portfolio: last session, equity, return, positions, and judging."""
    now = _now()
    try:
        with get_session() as session:
            lines = status_lines(session, now.date())
            stale = None if stale_after_days is None else stale_message(session, now, stale_after_days)
    except Exception as error:  # noqa: BLE001  # one line for the nightly log, not a traceback
        typer.echo(f"ERROR: {type(error).__name__}: {_first_line(error)}", err=True)
        raise typer.Exit(1) from None
    for line in lines:
        typer.echo(line)
    if stale is not None:
        typer.echo(stale, err=True)
        raise typer.Exit(STALE_EXIT)
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_paper_cli.py tests/test_cli.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 42 passed (7 new, and the 35 existing CLI tests); the full suite 559 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/cli.py tests/test_paper_cli.py
git commit -m "feat: signalbench paper start, run, and status commands

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The nightly script and its toasts

**Files:**
- Create: `scripts/paper_nightly.ps1`

- [ ] **Step 1: Write the script**

Create `scripts/paper_nightly.ps1`:

```powershell
<#
.SYNOPSIS
    The nightly SignalBench paper run (spec 07), for Task Scheduler.

.DESCRIPTION
    Runs `uv run signalbench paper run` from the repo root and appends everything to
    logs\paper-<yyyy-MM>.log (git-ignored). Shows a Windows toast when this run fails, and
    another when no paper run has succeeded for more than -StaleAfterDays days (checked
    before this run, so it catches a task that has not been running).

    Needs Windows PowerShell 5.1 (powershell.exe): the toast uses the built-in WinRT
    notification API, which PowerShell 7 cannot load. No modules are installed.

.PARAMETER NoToast
    Write each toast's text to the log instead of showing it (for checking the script).
#>
param(
    [int]$StaleAfterDays = 3,
    [switch]$NoToast
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$logDir = Join-Path $repo 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ('paper-{0:yyyy-MM}.log' -f (Get-Date))
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Log([string[]]$Lines) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
    Add-Content -Path $log -Encoding UTF8 -Value ($Lines | ForEach-Object { "$stamp  $_" })
}

function Show-Toast([string]$Title, [string]$Message) {
    if ($NoToast) {
        Write-Log @("toast (not shown): $Title | $Message")
        return
    }
    $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
    $title = [System.Security.SecurityElement]::Escape($Title)
    $body = [System.Security.SecurityElement]::Escape($Message)
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml("<toast><visual><binding template=`"ToastGeneric`"><text>$title</text><text>$body</text></binding></visual></toast>")
    $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
    # Windows PowerShell's own app id: toasts need a registered app, and this one always is.
    $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
}

function Invoke-SignalBench([string[]]$Arguments) {
    # Native stderr must not stop the script: take every line, stdout and stderr, as text.
    $ErrorActionPreference = 'Continue'
    $output = @(& $script:uv run signalbench @Arguments 2>&1 | ForEach-Object { "$_" })
    $code = $LASTEXITCODE
    Write-Log (@("> signalbench $($Arguments -join ' ')  (exit $code)") + $output)
    [pscustomobject]@{ Code = $code; Output = $output }
}

try {
    $script:uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $script:uv) {
        throw 'uv is not on PATH'
    }
    $status = Invoke-SignalBench @('paper', 'status', '--stale-after-days', "$StaleAfterDays")
    if ($status.Code -eq 3) {
        $stale = $status.Output | Where-Object { $_ -like 'STALE:*' } | Select-Object -First 1
        Show-Toast 'SignalBench paper runs are stale' "$stale"
    }
    $run = Invoke-SignalBench @('paper', 'run')
    if ($run.Code -ne 0) {
        $line = $run.Output | Where-Object { $_ -like 'ERROR:*' } | Select-Object -First 1
        if (-not $line) {
            $line = $run.Output | Where-Object { $_.Trim() } | Select-Object -Last 1
        }
        if (-not $line) {
            $line = "exit code $($run.Code)"
        }
        Show-Toast 'SignalBench paper run failed' ($line -replace '^ERROR:\s*', '')
        exit 1
    }
    exit 0
}
catch {
    Write-Log @("script error: $_")
    Show-Toast 'SignalBench paper run failed' "$_"
    exit 1
}
```

- [ ] **Step 2: Check that it parses in Windows PowerShell 5.1**

Run: `powershell.exe -NoProfile -Command '$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path scripts/paper_nightly.ps1), [ref]$null, [ref]$e); "parse errors: $($e.Count)"'`
Expected: `parse errors: 0`.

- [ ] **Step 3: Check a failing night without a toast or a database**

`nohost.invalid` never resolves, so neither command reaches a database; `-NoToast` writes the toast's text to the log instead of showing it.

Run: `rm -f logs/paper-*.log; DATABASE_URL='postgresql+psycopg://nobody:nothing@nohost.invalid:5432/none' powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/paper_nightly.ps1 -NoToast; echo "exit $?"; cat logs/paper-*.log`
Expected: `exit 1`, and five log lines, each stamped with the local time:

```text
> signalbench paper status --stale-after-days 3  (exit 1)
ERROR: OperationalError: (psycopg.OperationalError) failed to resolve host 'nohost.invalid': [Errno 11001] getaddrinfo failed
> signalbench paper run  (exit 1)
ERROR: OperationalError: (psycopg.OperationalError) failed to resolve host 'nohost.invalid': [Errno 11001] getaddrinfo failed
toast (not shown): SignalBench paper run failed | OperationalError: (psycopg.OperationalError) failed to resolve host 'nohost.invalid': [Errno 11001] getaddrinfo failed
```

`logs/` is git-ignored: `git status --short` must not list it.

- [ ] **Step 4: Commit**

```bash
git add scripts/paper_nightly.ps1
git commit -m "feat: nightly paper script with a Windows toast on failure or stale runs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Docs: spec 07 implementation choices and the owner's README lines

**Files:**
- Modify: `docs/superpowers/specs/2026-09-29-swing-assistant-07-paper-trading-design.md`

- [ ] **Step 1: Record the implementation choices in spec 07**

Append to `docs/superpowers/specs/2026-09-29-swing-assistant-07-paper-trading-design.md`:

```markdown
- 2026-09-29: implementation choices (plan `2026-09-29-swing-07-paper-trading.md`):
  - `SimState` also holds each open position's signal date, signal close, and initial stop (for R and the trade record) and the count of sessions processed (the pause's auto-resume counts sessions; the first session parks the start equity in QQQ). Its JSON carries `format: 1`. The invariant test steps each night on a market built from only the bars known that night.
  - `stop_update` is logged as a paper event too. A fill's payload carries its order event's id, and `fill_exit` carries the whole trade record. A deferred exit is ordered again at that night's close.
  - Catch-up means written at or after 09:30 New York on the next session. `paper_equity` has a `catch_up` column as well (a quiet session has no events to mark); `paper_runs.status` starts as `running`.
  - A run needs every universe series and QQQ to have a bar on the target session (the backtest's check), or it fails and steps nothing; the next run catches up. A rerun with nothing to step skips the ingest. A portfolio whose config changed is refused, the others still step, the weekly report is still written, and the run fails.
  - The start session is the first NYSE session after the New York date of `paper start`. The weekly report covers the ISO week before the one it is written in, and measures returns from the first close, as the backtest does. `paper status --stale-after-days N` exits 3 when no run has succeeded for N days; the nightly script checks it before each run.
  - `paper run` and `paper status` print one `ERROR: …` line on failure, never a traceback. The script needs Windows PowerShell 5.1 for the toast.
  - Known limits, to confirm with the owner before `paper start`: forward earnings dates are not known until filed, so the earnings rules will rarely fire; a split in a held stock looks like a crash, and dividends after entry are lost; on this PC (British Columbia time) 15:00 local is 17:00 New York from November 2026.
```

- [ ] **Step 2: Run the checks**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 559 passed; ruff and mypy clean.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-09-29-swing-assistant-07-paper-trading-design.md
git commit -m "docs: spec 07 implementation choices

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Give the owner the README lines**

`README.md` carries the owner's uncommitted edits, so do not touch it. Ask the owner to add these three rows to the CLI table, after the `backtest show` row:

```markdown
| `uv run signalbench paper start` | Create the seven pre-registered paper portfolios in the committed `data/paper_v1.yaml`; they start on the next NYSE session. Refuses if any already exists (spec 07) |
| `uv run signalbench paper run` | The nightly job: ingest prices, step every portfolio to the last complete session (one transaction per session), and write `reports/paper/<date>-weekly.md` on the first run of each ISO week. Safe to rerun. `scripts/paper_nightly.ps1` runs it from Task Scheduler, logs to `logs/`, and shows a Windows toast on failure |
| `uv run signalbench paper status [--stale-after-days N]` | One line per portfolio: last session, equity, return, open and closed trades, days until judgeable; exits 3 when no run has succeeded for more than N days |
```

and this line to the layout block, after the `backtest/` line:

```text
├── paper/          Forward paper trading: start, the nightly run, the weekly report, status (spec 07)
```

---

> ## ⛔ Controller: stop here until the owner says go
>
> Tasks 12–17 change the real database, commit the pre-registration, start the portfolios, and register a scheduled task on the owner's PC. Report the Task 1–11 results to the owner with `data/paper_v1.yaml` (untracked), the three decisions marked **Confirm with the owner** (earnings dates, splits and dividends, the run time), and the README lines from Task 11. Wait for a clear go in chat. Once `paper start` has run, no config or portfolio may change: a new idea is a new portfolio in a new `data/paper_<n>.yaml`.

---

### Task 12: ⛔ Run the migration on the real database

**Gated:** the owner said go.

- [ ] **Step 1: Database up, migration pending**

Run: `docker compose up -d && uv run alembic current`
Expected: `0011_jev_readings (head)` among the output lines. Anything else: **stop and report**.

- [ ] **Step 2: Migrate**

Run: `uv run alembic upgrade head && uv run alembic current`
Expected: `Running upgrade 0011_jev_readings -> 0012_paper_trading, Spec 07 forward paper trading`, then `0012_paper_trading (head)`.

---

### Task 13: ⛔ Commit `data/paper_v1.yaml` on its own

**Gated:** Task 12 done.

- [ ] **Step 1: Check the file is the one the tests check**

Run: `uv run pytest tests/test_paper_file.py -q`
Expected: 10 passed.

- [ ] **Step 2: Commit the file, and nothing else**

```bash
git add data/paper_v1.yaml
git commit -m "data: pre-registered spec 07 paper portfolios (seven)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Run: `git show --stat HEAD`
Expected: exactly `data/paper_v1.yaml`, nothing else.

---

### Task 14: ⛔ `paper start`

**Gated:** Task 13 committed.

- [ ] **Step 1: The data folder is committed and clean**

Run: `git status --short -- data src alembic`
Expected: no output.

- [ ] **Step 2: Start the portfolios**

Run it in the evening, after the day's session (the start session is the first NYSE session after today's New York date).

Run: `uv run signalbench paper start`
Expected: seven lines, in the file's order, all with the same start session (the hashes are the committed files', CRLF read as LF; `v1-breakout`'s is the `config_sha256` in the header of `reports/backtests/2026-09-24-breakout-off.md`):

```text
v1-breakout: starts <next session> | data/strategy_v1.yaml | config_sha256 98037e0b93e9
v2-t30-cash: starts <next session> | data/strategy_v2-t30-cash.yaml | config_sha256 bc207cf97485
v2-t30-qqq: starts <next session> | data/strategy_v2-t30-qqq.yaml | config_sha256 a7cf0ac5a9e8
v2-t60-cash: starts <next session> | data/strategy_v2-t60-cash.yaml | config_sha256 ef4f763d109d
v2-t60-qqq: starts <next session> | data/strategy_v2-t60-qqq.yaml | config_sha256 8bec8ccdc728
v2-none-cash: starts <next session> | data/strategy_v2-none-cash.yaml | config_sha256 a9579593cc7b
v2-none-qqq: starts <next session> | data/strategy_v2-none-qqq.yaml | config_sha256 ee1797ad9cdf
```

Any other hash means a config changed since spec 06: **stop and report**. Running `paper start` a second time must refuse with `Paper portfolios already exist: …` and exit 1.

- [ ] **Step 3: Status**

Run: `uv run signalbench paper status`
Expected: `last ok run: none`, then seven lines `<name>: starts <next session>, not stepped yet | judgeable after 365 days and 30 more closed trades` (the day count may differ by one or two).

---

### Task 15: ⛔ One manual `paper run`; the twins match

**Gated:** Task 14 done, and the start session has closed (after 16:15 New York on it: 13:15 local until 1 November 2026, 14:15 after).

- [ ] **Step 1: Run it**

Run: `uv run signalbench paper run`
Expected: the ingest lines (`prices 1/… +… ~… x…`, then `liquidity: … active, … inactive`), then seven lines `<name>: 1 sessions to <start session>`, `weekly report: …\reports\paper\<today>-weekly.md`, and last `paper run <id>: ok, 7 sessions stepped to <start session>`. Exit 0.

An `ERROR: RunRefusedError: These price series do not end on the run's last session …` means the day's bars are not all in yet: wait an hour and rerun. **Any other error: stop and report.**

- [ ] **Step 2: Run it again: nothing to do**

Run: `uv run signalbench paper run`
Expected: `Every portfolio has stepped <start session>; nothing to do.` and `paper run <id>: ok, 0 sessions stepped to <start session>`, with no ingest lines.

- [ ] **Step 3: Status**

Run: `uv run signalbench paper status`
Expected: `last ok run: <that run's start, in UTC>`, then seven lines `<name>: last <start session> | equity … | return +0.00% | open 0 | closed 0 | judgeable after …`. The `-cash` portfolios and `v1-breakout` show `equity 100.00`; the three `-qqq` portfolios show 100 less QQQ's 0.2% purchase cost, plus QQQ's move from that open to that close.

- [ ] **Step 4: The two identical portfolios match**

Run:

```bash
uv run python - <<'EOF'
from sqlmodel import col, select

from signalbench.db.models import PaperEquity, PaperEvent, PaperPortfolio
from signalbench.db.session import get_session


def rows(session, name):
    portfolio = session.exec(select(PaperPortfolio).where(PaperPortfolio.name == name)).one()
    events = session.exec(
        select(PaperEvent).where(PaperEvent.portfolio_id == portfolio.id).order_by(col(PaperEvent.id))
    ).all()
    equity = session.exec(
        select(PaperEquity).where(PaperEquity.portfolio_id == portfolio.id)
        .order_by(col(PaperEquity.session))
    ).all()
    return (
        [(e.session, e.kind, {k: v for k, v in e.payload.items() if k != "order_event_id"}) for e in events],
        [(r.session, r.equity, r.cash, r.vehicle_value, r.open_positions) for r in equity],
    )


with get_session() as session:
    one, two = rows(session, "v1-breakout"), rows(session, "v2-t30-cash")
print("twins match" if one == two else "TWINS DIFFER", len(one[0]), "events", len(one[1]), "sessions")
EOF
```

Expected: `twins match <n> events 1 sessions`. `TWINS DIFFER` is a bug in the runner (spec 07): **stop and report**; the scheduled task is not registered until it is fixed.

- [ ] **Step 5: Show the owner the first weekly report**

Show `reports/paper/<today>-weekly.md`. Do not commit it: reports are committed when reviewed (spec 07).

---

### Task 16: ⛔ Task Scheduler, and one forced failure

**Gated:** Task 15 matched. Registering the task is a lasting change on the owner's PC: show the command, and run it only on the owner's go-ahead in chat.

- [ ] **Step 1: Show the owner the command**

Run in a PowerShell window (as the owner, not elevated; if Windows answers "Access is denied", use an elevated window for the same user). `-At '15:00'` is local time; for 18:00 New York all year on this PC, use `'16:00'` instead (see Decisions, the run time):

```powershell
$repo = 'C:\Users\samin\Documents\GitHub\macrocite'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $repo `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$repo\scripts\paper_nightly.ps1`""
$triggers = @(
    New-ScheduledTaskTrigger -Daily -At '15:00'
    New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName 'SignalBench paper run' -Action $action -Trigger $triggers `
    -Settings $settings -Description 'Nightly forward paper run (spec 07): scripts\paper_nightly.ps1'
```

The task runs as the owner, only while logged on (toasts need the desktop); `-StartWhenAvailable` runs a missed 15:00 when the PC wakes. To remove it later: `Unregister-ScheduledTask -TaskName 'SignalBench paper run' -Confirm:$false`.

- [ ] **Step 2: Check it**

Run: `Get-ScheduledTask -TaskName 'SignalBench paper run' | Get-ScheduledTaskInfo | Select-Object NextRunTime, LastTaskResult`
Expected: `NextRunTime` at the next 15:00 (or 16:00) local; `LastTaskResult` 267011 (never run).

- [ ] **Step 3: Force one failure and see the toast**

In the same PowerShell window (the variable lasts only for this window; the owner's `.env` is not touched):

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://nobody:nothing@nohost.invalid:5432/none'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\paper_nightly.ps1
"exit $LASTEXITCODE"
Remove-Item Env:DATABASE_URL
Get-Content "logs\paper-$(Get-Date -Format yyyy-MM).log" -Tail 4
```

Expected: a Windows toast from "Windows PowerShell" titled **SignalBench paper run failed**, reading `OperationalError: (psycopg.OperationalError) failed to resolve host 'nohost.invalid': [Errno 11001] getaddrinfo failed`; `exit 1`; and the log's last four lines are the first four of Task 10 Step 3 (there is no `toast (not shown)` line: the toast was shown). No `paper_runs` row is written, because the run never reached the database. If no toast appears, check Settings > System > Notifications (Windows PowerShell on, Do not disturb off) and try again.

---

### Task 17: ⛔ Research-log entry and the spec 07 start line

**Files:**
- Modify: `docs/research-log.md`, `docs/superpowers/specs/2026-09-29-swing-assistant-07-paper-trading-design.md`

- [ ] **Step 1: One research-log entry**

Insert a new entry in `docs/research-log.md` after the "Breakout v2: idle cash in QQQ and holding time (spec 06)" entry and before `## Open questions`, in the log's style:

- Heading: `## <start date> — Forward paper trading started (spec 07)`.
- **Kind:** forward test, out of sample (prices that did not exist when the orders were decided). Pre-registered in `data/paper_v1.yaml` (commit `<Task 13 sha>`); no money.
- **Test:** the seven portfolios (v1 Breakout and the six spec 06 variants), 100 each from the start session `<date>`, stepped nightly with US opens and 0.2% per side; QQQ buy-and-hold from that session's close is the benchmark.
- **Judging:** once a portfolio has 12 months and at least 30 closed trades, on the spec 02 pass bar over the paper period (spec 07, Judging). Until then the weekly reports in `reports/paper/` are information only.
- **Checks:** the first run stepped all seven; `v1-breakout` and `v2-t30-cash` matched; the forced failure showed the toast. The scheduled task runs daily at `<15:00 or 16:00>` local and at log-on.
- **Known limits:** the three decisions confirmed with the owner (earnings dates, splits and dividends, the run time), one line each, as the owner decided them.

Also update the "Open questions" list: the idle-cash and holding-time item now says forward paper trading started on `<date>` (this entry).

- [ ] **Step 2: One line in the spec 07 changelog**

Append to `docs/superpowers/specs/2026-09-29-swing-assistant-07-paper-trading-design.md` one line: `- <date>: started. Migration 0012 applied; data/paper_v1.yaml committed (<sha>); paper start on <date>, first session <date>; the first run stepped all seven and the twins matched; Task Scheduler "SignalBench paper run" daily at <time> local and at log-on; a forced failure showed the toast. Logged in docs/research-log.md.`

- [ ] **Step 3: Commit**

```bash
git add docs/research-log.md docs/superpowers/specs/2026-09-29-swing-assistant-07-paper-trading-design.md
git commit -m "docs: research log and spec 07 changelog for the paper start

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Send the owner the start session, the status lines, and the weekly report path. Ask before pushing the branch or opening a PR (`feat/swing-03-jev-reader` and `feat/swing-06-breakout-v2` are not merged; this branch sits on them).

---

## Spec coverage check

| Spec 07 requirement | Task |
| --- | --- |
| `step()` and `StepResult`; `SimState` as plain data, JSON round trip exact | 1 |
| `simulate()` = initial state + `step()` loop; the v1 regression pin and every simulator and cash-vehicle test unchanged | 1 |
| Stepping with save/reload equals one `simulate()` (with and without a cash vehicle, a pause, a deferred exit) | 1 |
| Four tables in migration `0012_paper_trading`; `state` and `last_session` updated together | 2, 6 |
| `data/paper_v1.yaml` with the seven portfolios, committed before `paper start` | 3, 13 |
| `paper start`: file and configs committed and unchanged, `config_sha256` and start session stored, refuses if any exists | 4, 14 |
| `paper run` 1: the advisory lock; a held lock exits 0 | 5, 6, 9 |
| `paper run` 2–4: the `paper_runs` row; `ingest prices` and liquidity; target by `last_complete_session` | 6, 9 |
| `paper run` 5: the config sha per portfolio; every missed session in order, one transaction each | 6 |
| Order events written the night they are decided; fills carry the order event's id; catch-up rows marked | 6 |
| Fills at the US open with 0.2%, exactly like the backtest (the simulator is shared) | 1, 6 |
| `paper run` 6: the weekly report on the first run of an ISO week | 7 |
| `paper run` 7: `failed` with the error, exit 1; a second run the same night steps nothing and exits 0 | 6, 9 |
| `paper status`: last session, equity, return, open positions, closed trades, days until judgeable | 8, 9 |
| Weekly report contents, next to QQQ buy-and-hold; catch-up sessions and failed runs; the information-only line | 7 |
| `scripts/paper_nightly.ps1`: log under `logs/`, toast on failure, stale toast after 3 days | 8, 10, 16 |
| Task Scheduler daily at 15:00 Pacific and at log-on, registered only on the owner's go-ahead | 16 |
| The toast checked by hand once (a forced failure) | 16 |
| Order of work 2–6 (migration, commit and start, manual run and twin check, scheduler and toast, log) | 12–17 |
| Deferred ("Later"): `paper judge`, CDR reality check, twin-match report line | not built |
