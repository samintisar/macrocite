# Swing Assistant 06 — Breakout v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a strategy config keep idle cash in QQQ (`cash_vehicle`) and let Breakout run with no time limit (`time_limit: null`); write the six pre-registered v2 configs; then, only after they are committed, run all six on the real data and record every result. The results are post-hoc and decide nothing: every variant goes to forward paper trading whatever it shows.

**Architecture:** `StrategyConfig` gains an optional `CashVehicle` (symbol, cost per side) and `BreakoutParams.time_limit` becomes `int | None`; `exit_reason()` skips the time check when the limit is `None`. The simulator holds QQQ units beside cash: on a session with an exit or entry fill it settles the day's net cash against QQQ at QQQ's open (sell just enough to pay for the entries, or buy with what is left), filled like a stock at `open × (1 ∓ cost)`; the start equity is parked at the first open. QQQ at the close is added to equity, so sizing, the drawdown pause, the equity curve, and Sharpe all see total equity; `decide()` is unchanged and sizes against a `PortfolioState` whose cash includes QQQ net of its selling cost. `vehicle_stats()` measures the sleeve, the runner reruns a QQQ config at 0.05% per switch (reported, never stored), and the report adds an "Idle cash in QQQ" section, the sensitivity line, and the QQQ caveats. A config without `cash_vehicle` takes none of these paths, and a characterization test pins its output.

**Tech Stack:** Python 3.11+, SQLModel (in-memory SQLite in tests), PyYAML, Typer, pytest, ruff 0.16, mypy strict. No new dependencies, no migration.

**Spec:** [`docs/superpowers/specs/2026-09-28-swing-assistant-06-breakout-v2-design.md`](../specs/2026-09-28-swing-assistant-06-breakout-v2-design.md). **Depends on:** branch `feat/swing-03-jev-reader` (not merged yet); this branch, `feat/swing-06-breakout-v2`, is cut from it.

---

## Conventions for every task

- Run commands from the repo root. Use `uv run …` for everything. Stay on branch `feat/swing-06-breakout-v2`; never push without asking the owner.
- **Never edit `data/strategy_v1.yaml`** (its `config_sha256` is in the stored v1 runs) **or any `.env*` file.** Stage explicit paths only; never `git add -A` or `git add .`.
- **`README.md` has an uncommitted change that belongs to the owner.** Do not edit it, stage it, stash it, or check it out. Task 11 gives the owner the README line to add.
- **No real data before Task 13.** Tasks 1–12 use only synthetic fixtures and the in-memory SQLite `session` fixture from `tests/conftest.py`. Do not run `signalbench backtest run`, and do not open the real database, until the controller's go-ahead below: the six variants are pre-registered, and seeing a real v2 result before the configs are committed breaks that.
- Do not run `signalbench ingest prices` anywhere in this plan. The stored v1 runs used prices through 2026-09-24; Task 13 needs the same data to check that `v2-t30-cash` reproduces v1 Breakout.
- mypy runs strict on `src/` only. `tests/strategy_helpers.py` is imported as `from strategy_helpers import …`.
- ruff 0.16 sorts imports (`I001`). Run `uv run ruff check --fix .` only **after** the module a test imports exists: before that, ruff files the missing `signalbench.*` name as third-party and moves the import.
- After each task: `uv run pytest -q`, `uv run ruff check .`, and `uv run mypy src` all pass before committing.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Every code block in this plan was replayed, task by task, in a scratch clone of this branch before the plan was committed: each "Expected" failure and pass count below is from that replay. The suite starts at 440 passed and ends at 481 passed after Task 11 (12 of them are the variant-file tests, which skip until Task 10 writes the files), with ruff and mypy clean. If a number disagrees, look for a transcription slip before touching a test.

## The cash vehicle, as built

| Rule | Behaviour |
| --- | --- |
| Which symbol | `cash_vehicle.symbol` must equal `regime.symbol` (QQQ): `MarketView` already serves it, the runner already loads it and fingerprints it |
| When QQQ trades | The first session's open (the start equity), then only on sessions with at least one exit or entry **fill** (skips and deferred exits do not count). At most one QQQ trade per session: the day's net need |
| Price and cost | QQQ's open, filled like a stock: a sale pays out `units × open × (1 − cost)`, a purchase costs `units × open × (1 + cost)`. The event's `amount` is `units × open`, its `cost` is `amount × cost` |
| Order at the open | Exits (proceeds to cash), then entries (cash may go below zero), then one `vehicle_sell` (cash < 0: just enough to cover it) or `vehicle_buy` (cash > 0: all of it) |
| Cash cap at the open | Each entry's units ≤ (cash + QQQ units × QQQ open × (1 − cost)) / fill |
| Sizing at the close | `PortfolioState.equity` = cash + positions + QQQ at its close; `PortfolioState.cash` = cash + QQQ at its close × (1 − cost). `decide()` is unchanged |
| Pause | `step_risk()` sees total equity, QQQ included |
| Equity curve | `EquityPoint.equity` includes QQQ at its close; the new `EquityPoint.vehicle_value` holds that QQQ value |
| No QQQ bar today | QQQ does not trade; it is marked at its last close |
| Float residue | Cash within `DUST` = 1e-9 of zero after a switch is set to zero and never triggers a switch |

## Decisions this plan makes where the spec is silent

- **The start equity is parked in QQQ at the first session's open.** The spec says idle cash is always in QQQ and that nothing trades on a day without exits or entries; it does not say what happens to the starting cash. Leaving it as cash until the first fill would contradict "always in QQQ", so the first session counts as a funding day. It costs one 0.2% purchase. **Confirm with the owner** (Task 11 records it in the spec changelog).
- **One net QQQ trade per session.** Exits add cash, entries spend it (cash may dip below zero while the day's entries fill), then one sale covers the shortfall or one purchase parks the rest. Because exits fill before entries and a sale only ever covers a shortfall, this is the same money moved as selling per entry and then parking, with the fewest switches.
- **QQQ fills use the stock convention**, `open × (1 ∓ cost)`, so "cost on the amount moved" means cost on the market value of the QQQ units traded.
- **The decide-time cash cap** (in `entries.size()`) uses cash plus QQQ at the close net of the selling cost, mirroring the spec's open-time rule. The simulator gets this through what it puts in `PortfolioState.cash`; `decide()` and `entries.py` do not change.
- **`cash_vehicle.symbol` must be the regime symbol.** The spec says "known symbol"; the only series besides the universe that a run loads is the regime benchmark. Anything else is refused at parse time.
- **`time_limit: null` must be written out.** The key stays required for Breakout (as every key is); `null` is accepted only there. Pullback and Sentiment refuse it with the usual `expected an integer >= 1`.
- **The `-cash` configs have no `cash_vehicle` key** (the spec: a missing `cash_vehicle` means none). Their header comment says so. The QQQ block sits right after the top-level `cost_per_side`.
- **Report names.** Six runs of `--setup breakout` on one day would all be `<date>-breakout-off.md` plus id suffixes. Versions other than v1 now write `<date>-<version>-<setup>-<jev>.md` (v1 names are unchanged); four existing runner tests that run version `test` change their expected names.
- **The POST-HOC line** in every non-v1 report adds "A PASS only means the variant did not fail on the past; nothing goes live from it." (spec 06, Pass bar and reports).
- **Report additions** appear only for runs with a cash vehicle, under `metrics["cash_vehicle"]`: the cost per side; the average shares of equity in QQQ, in stocks, and in cash (they sum to 1; "stock exposure shown separately"); switches (buys and sells) and their total cost in equity units; and the 0.05% sensitivity (total return, CAGR, Sharpe, max drawdown) from a second, unstored simulation. The existing Exposure row still counts sessions holding a stock. A config without `cash_vehicle` stores exactly the v1 metrics keys.
- **QQQ caveats:** the spec's three, plus the stand-in the spec states under Variants: QQQ's adjusted prices stand in for the CAD-listed, CAD-hedged Nasdaq-100 ETF.
- **Regression pin.** `tests/test_v1_regression.py` (Task 1) records, on the code before any spec 06 change, a digest of a v1-style simulation (13 trades, both setups, stop, target, and time exits) and of a stored run's trade log, its `data_fingerprint`, and its metric keys. The fixture uses uniform random draws only, because `random.gauss` calls libm and its last digit may differ between Windows and CI.
- **No CLI change.** `backtest run --config data/strategy_<version>.yaml` already loads any version and applies the pre-registration guard; Task 9 adds a test that proves a QQQ config reaches the runner intact.
- **Config commit is gated.** Task 10 writes the six files and a test that compares them with v1 (it skips when they are absent), but only the test is committed; the six files are committed on their own in Task 12, after the owner has read them.

## File map

| Path | Action | Responsibility |
| --- | --- | --- |
| `src/signalbench/strategy/config.py` | Modify | `CashVehicle`, `StrategyConfig.cash_vehicle`, Breakout `time_limit: int \| None`, parsing and validation |
| `src/signalbench/strategy/exits.py` | Modify | No time exit when `time_limit` is `None` |
| `src/signalbench/strategy/portfolio.py`, `setups.py`, `decision.py` | Modify | `time_limit: int \| None`; `PortfolioState` comments |
| `src/signalbench/backtest/simulator.py` | Modify | QQQ units, sell-to-fund, parking, vehicle events, `EquityPoint.vehicle_value`, total-equity sizing and pause |
| `src/signalbench/backtest/metrics.py` | Modify | `VehicleStats`, `vehicle_stats()` |
| `src/signalbench/backtest/report.py` | Modify | `cash_vehicle_payload()`, "Idle cash in QQQ" section, sensitivity line, `QQQ_CAVEATS`, POST-HOC sentence, version in report names |
| `src/signalbench/backtest/runner.py` | Modify | Sleeve stats, the 0.05% rerun, report name with version |
| `tests/test_v1_regression.py` | Create | Pins v1 output before any change |
| `tests/test_simulator_cash_vehicle.py` | Create | Hand-worked cash-vehicle cases |
| `tests/test_strategy_v2_files.py` | Create | The six configs are v1 plus their declared changes |
| `tests/test_exits.py`, `test_strategy_config.py`, `test_setups.py`, `test_simulator.py`, `test_decide.py`, `test_run_metrics.py`, `test_report.py`, `test_runner.py`, `test_cli.py` | Modify | New cases (listed in each task) |
| `data/strategy_v2-{t30,t60,none}-{cash,qqq}.yaml` | Create (Task 10), commit (Task 12, gated) | The six pre-registered variants |
| `docs/superpowers/specs/2026-09-28-swing-assistant-06-breakout-v2-design.md` | Modify | Implementation choices; later the results line |
| `reports/backtests/<date>-v2-*-breakout-off.md`, `docs/research-log.md` | Create/modify (gated) | The six reports and the log entry |

---

### Task 1: Pin the v1 output before any change

**Files:**
- Create: `tests/test_v1_regression.py`

This is a characterization test: it records what the code produces **now**, so it passes on the first run and must keep passing after every later task.

- [ ] **Step 1: Write the test**

Create `tests/test_v1_regression.py`:

```python
"""Pins what a config without `cash_vehicle` produces, recorded before spec 06.

Spec 06 adds idle cash in QQQ and an optional Breakout time limit. A config without
`cash_vehicle` (v1 and the `-cash` variants) must keep producing these results exactly.
"""

import hashlib
import json
import random
from dataclasses import asdict
from datetime import date
from pathlib import Path

from pytest import approx
from sqlmodel import Session

from signalbench.backtest.runner import run_backtest
from signalbench.backtest.simulator import SimulationResult, simulate
from signalbench.db.models import TickerKind
from signalbench.market.bars import AdjustedBar
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    WeekdaySessions,
    load_test_config,
    make_bar,
    make_market,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)
from test_runner import DAYS as RUN_DAYS
from test_runner import DIP, LATER, UNIVERSE, _store

CONFIG = load_test_config().with_setups(("pullback", "breakout"))
METRIC_KEYS = [
    "average_hold", "benchmarks", "cagr", "exposure_pct", "h1_end", "h2_start", "max_drawdown",
    "mean_r", "mean_r_h1", "mean_r_h2", "median_r", "open_positions_at_end", "pauses", "recent",
    "recent_since", "sharpe", "skips_by_reason", "total_return", "trades", "trades_h1",
    "trades_h2", "win_rate",
]
TRADES, TOTAL_RETURN, SHARPE = 1, -0.0013333333333332975, -3.643123346373737
DAYS = weekdays(date(2021, 1, 4), 520)
SECTORS = {"AAA": "Energy", "BBB": "Energy", "CCC": "Energy", "DDD": "Utilities", "EEE": "Financials"}
EARNINGS = {"AAA": [DAYS[300], DAYS[380]], "CCC": [DAYS[333], DAYS[450]], "EEE": [DAYS[410]]}


def _walk(rng: random.Random) -> list[AdjustedBar]:
    bars: list[AdjustedBar] = []
    close = 100.0
    for day in DAYS:
        # Uniform draws only: gauss() calls libm (log, cos), whose last digit can vary by
        # platform, and these bars feed exact digests.
        close = max(5.0, close * (1.0008 + 0.07 * (rng.random() - 0.5)))
        volume = int(1_000_000 * (3.0 if rng.random() < 0.05 else 1.0))
        spread = close * 0.01
        bars.append(make_bar(day, close, open_=close * (1 + 0.017 * (rng.random() - 0.5)),
                             high=close + spread, low=close - spread, volume=volume))
    return bars


def _digest(rows: object) -> str:
    """SHA-256 of the rows as JSON; floats keep every digit (repr), dates become ISO strings."""
    text = json.dumps(rows, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _simulation_rows(result: SimulationResult) -> dict[str, object]:
    return {
        "trades": [asdict(trade) for trade in result.trades],
        "open_at_end": [asdict(position) for position in result.open_at_end],
        "events": result.events,
        "curve": [(p.date, p.equity, p.cash, p.open_positions) for p in result.equity_curve],
    }


def test_a_config_without_a_cash_vehicle_simulates_exactly_as_before() -> None:
    rng = random.Random(20260928)
    bars = {name: _walk(rng) for name in SECTORS}
    market = make_market(
        bars, trend_bars(DAYS, 300.0, 0.3), DAYS, CONFIG, sectors=SECTORS, earnings=EARNINGS
    )
    result = simulate(market, NullReadingsView(), CONFIG, DAYS[210], DAYS[510])
    reasons = sorted({trade.reason for trade in result.trades})
    setups = sorted({trade.setup for trade in result.trades})
    assert (len(result.trades), reasons, setups) == (13, ["stop", "target", "time"], ["breakout", "pullback"])
    assert _digest(_simulation_rows(result)) == "d3788232e641390e7770aa1da118b6113beefff74174614ef537d1a8daa2899a"


def test_a_v1_style_run_stores_the_same_metrics_trade_log_and_fingerprint(
    session: Session, tmp_path: Path
) -> None:
    _store(session, "AAA", TickerKind.us_stock, series(RUN_DAYS, pullback_closes(len(RUN_DAYS), dip=DIP)))
    _store(session, "BBB", TickerKind.us_stock, trend_bars(RUN_DAYS, 50.0, 0.1))
    _store(session, "QQQ", TickerKind.benchmark, trend_bars(RUN_DAYS, 300.0, 0.5))
    run, _ = run_backtest(
        session, setup="combined", jev_mode="off", config=load_test_config(),
        config_sha256="f" * 64, universe=UNIVERSE, calendar=WeekdaySessions(), git_sha="abc123",
        run_date=date(2026, 9, 24), reports_dir=tmp_path, now=LATER,
    )
    assert run.data_fingerprint == "f4fdd15ec3c05e0c0f97a765285e8b8f79aefbdcfc7a0d5974f3716f549bce64"
    assert _digest(run.trade_log) == "68c093c7285d91e5d248d80fa7ed7932dfcf179108996df19b4d7ad19301ef29"
    assert sorted(run.metrics) == METRIC_KEYS
    assert (run.metrics["trades"], run.metrics["total_return"], run.metrics["sharpe"]) == (
        TRADES, approx(TOTAL_RETURN, rel=1e-12), approx(SHARPE, rel=1e-12),
    )
```

The digests and numbers were recorded on this branch before any spec 06 code existed. The Sharpe and total return are compared to 12 significant digits rather than hashed, because `statistics.stdev` may round the last digit differently between Python versions.

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_v1_regression.py -v`
Expected: 2 passed. Nothing in `src/` has changed yet, so a differing digest means the fixture was mistyped: fix the transcription, never the digest.

- [ ] **Step 3: Full checks**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 442 passed; ruff `All checks passed!`; mypy `Success`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_v1_regression.py
git commit -m "test: pin v1 simulator and runner output before spec 06

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: No time exit without a time limit

**Files:**
- Modify: `src/signalbench/strategy/portfolio.py`, `src/signalbench/strategy/exits.py`, `tests/test_exits.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_exits.py`:

```python
def test_a_position_with_no_time_limit_never_exits_for_time() -> None:
    open_ended = replace(
        POSITION, setup="breakout", target=None, time_limit=None, sessions_held=10_000
    )
    assert exit_reason(open_ended, make_snapshot(), earnings_soon=False) is None
    assert exit_reason(open_ended, make_snapshot(close=95.0), earnings_soon=False) == "stop"
    assert exit_reason(open_ended, make_snapshot(), earnings_soon=True) == "earnings"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_exits.py -v`
Expected: 1 failed, 7 passed; the failure is `TypeError: '>=' not supported between instances of 'int' and 'NoneType'`.

- [ ] **Step 3: Allow `None` and skip the time check**

In `src/signalbench/strategy/portfolio.py`, replace:

```python
    time_limit: int
```

with:

```python
    time_limit: int | None  # sessions; None: no time limit (spec 06, Breakout only)
```

In `src/signalbench/strategy/exits.py`, replace:

```python
    `earnings_soon` is whether an earnings event falls on D+1 or D+2.
    """
```

with:

```python
    `earnings_soon` is whether an earnings event falls on D+1 or D+2. A position with no time
    limit (spec 06) never exits for time.
    """
```

In `src/signalbench/strategy/exits.py`, replace:

```python
    if position.sessions_held >= position.time_limit:
```

with:

```python
    if position.time_limit is not None and position.sessions_held >= position.time_limit:
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_exits.py -v && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 8 passed; the full suite 443 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/strategy/portfolio.py src/signalbench/strategy/exits.py tests/test_exits.py
git commit -m "feat: a position with no time limit never exits for time

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Config — `time_limit: null` for Breakout and the `cash_vehicle` block

**Files:**
- Modify: `src/signalbench/strategy/config.py`, `src/signalbench/strategy/setups.py`, `src/signalbench/strategy/decision.py`
- Modify: `tests/test_strategy_config.py`, `tests/test_setups.py`, `tests/test_simulator.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_strategy_config.py`, replace:

```python
from signalbench.strategy.config import (
    ConfigError,
```

with:

```python
from signalbench.strategy.config import (
    CashVehicle,
    ConfigError,
```

Append to `tests/test_strategy_config.py`:

```python
def _setup(raw: dict[str, object], name: str) -> dict[str, object]:
    setups = raw["setups"]
    assert isinstance(setups, dict)
    section = setups[name]
    assert isinstance(section, dict)
    return section


def test_breakout_time_limit_may_be_null() -> None:
    raw = _raw()
    _setup(raw, "breakout")["time_limit"] = None
    config = parse_strategy_config(raw)
    assert config.breakout.time_limit is None
    assert config.pullback.time_limit == 10 and config.sentiment.time_limit == 10


@pytest.mark.parametrize("name", ["pullback", "sentiment"])
def test_only_breakout_may_have_no_time_limit(name: str) -> None:
    raw = _raw()
    _setup(raw, name)["time_limit"] = None
    with pytest.raises(ConfigError, match=f"config.setups.{name}.time_limit: expected an integer"):
        parse_strategy_config(raw)


def test_breakout_time_limit_must_still_be_written() -> None:
    raw = _raw()
    del _setup(raw, "breakout")["time_limit"]
    with pytest.raises(ConfigError, match="config.setups.breakout: missing key 'time_limit'"):
        parse_strategy_config(raw)
    _setup(raw, "breakout")["time_limit"] = 0
    with pytest.raises(ConfigError, match="config.setups.breakout.time_limit: expected an integer"):
        parse_strategy_config(raw)


def test_no_cash_vehicle_key_means_idle_cash_stays_cash() -> None:
    config, _ = load_strategy_config(FIXTURE)
    assert config.cash_vehicle is None


def test_cash_vehicle_parses_and_survives_with_setups() -> None:
    raw = _raw()
    raw["cash_vehicle"] = {"symbol": "QQQ", "cost_per_side": 0.002}
    config = parse_strategy_config(raw)
    assert config.cash_vehicle == CashVehicle(symbol="QQQ", cost_per_side=0.002)
    assert config.with_setups(("breakout",)).cash_vehicle == config.cash_vehicle
    raw["cash_vehicle"] = {"symbol": "QQQ", "cost_per_side": 0}
    assert parse_strategy_config(raw).cash_vehicle == CashVehicle("QQQ", 0.0)  # free is allowed


@pytest.mark.parametrize(
    ("vehicle", "message"),
    [
        ({"symbol": "SPY", "cost_per_side": 0.002}, "cash_vehicle.symbol: must be the regime symbol 'QQQ'"),
        ({"symbol": "QQQ", "cost_per_side": -0.001}, "cash_vehicle.cost_per_side: expected a number >= 0"),
        ({"symbol": "QQQ", "cost_per_side": 0.05}, r"cash_vehicle.cost_per_side: expected a number in \[0, 0.05\)"),
        ({"symbol": "QQQ"}, "cash_vehicle: missing key 'cost_per_side'"),
        ({"symbol": "QQQ", "cost_per_side": 0.002, "sma": 200}, r"cash_vehicle: unknown keys \['sma'\]"),
        (None, "config.cash_vehicle: expected a mapping"),
    ],
)
def test_cash_vehicle_is_validated(vehicle: object, message: str) -> None:
    raw = _raw()
    raw["cash_vehicle"] = vehicle
    with pytest.raises(ConfigError, match=message):
        parse_strategy_config(raw)
```

In `tests/test_setups.py`, replace:

```python
from pytest import approx
```

with:

```python
from dataclasses import replace

from pytest import approx
```

In `tests/test_setups.py`, replace:

```python
def test_breakout_rejects_equal_high_or_thin_volume() -> None:
```

with:

```python
def test_breakout_with_no_time_limit_signals_none() -> None:
    open_ended = replace(CONFIG, breakout=replace(CONFIG.breakout, time_limit=None))
    snap = make_snapshot(
        close=105.0, sma=UPTREND, prior_max_close=104.0, volume=1_500_000.0,
        prior_mean_volume=1_000_000.0, atr=2.5,
    )
    signal = breakout(snap, open_ended)
    assert signal is not None
    assert (signal.target_r, signal.time_limit) == (None, None)


def test_breakout_rejects_equal_high_or_thin_volume() -> None:
```

Append to `tests/test_simulator.py`:

```python
def _breakout_run(time_limit: int | None) -> SimulationResult:
    # A steady uptrend (close 50 + 0.1 i, ATR 2) with one 2x volume day on SIGNAL: the only
    # breakout. The trailing stop (highest close - 3 ATR) is never touched.
    config = load_test_config().with_setups(("breakout",))
    config = replace(config, breakout=replace(config.breakout, time_limit=time_limit))
    days = weekdays(date(2023, 1, 2), 320)
    bars = with_bar(trend_bars(days, 50.0, 0.1), SIGNAL, volume=2_000_000)
    market = make_market({"AAA": bars}, trend_bars(days, 300.0, 0.5), days, config)
    return simulate(market, NullReadingsView(), config, days[240], days[300])


def test_a_breakout_with_no_time_limit_is_held_to_the_end() -> None:
    [timed] = _breakout_run(30).trades
    assert (timed.reason, timed.entry_date, timed.sessions_held) == ("time", DAYS[ENTRY], 30)
    open_ended = _breakout_run(None)
    assert open_ended.trades == []
    [held] = open_ended.open_positions
    assert (held.setup, held.time_limit, held.sessions_held) == ("breakout", None, 300 - ENTRY + 1)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_strategy_config.py -v`
Expected: collection error, `ImportError: cannot import name 'CashVehicle' from 'signalbench.strategy.config'`.

(The two new setup and simulator tests already pass at runtime after Task 2, because dataclasses do not check types; they are here so the `None` path is covered end to end. mypy is what needs the type changes below.)

- [ ] **Step 3: Implement the config changes**

In `src/signalbench/strategy/config.py`, replace:

```python
COST_PER_SIDE_FLOOR = 0.002
```

with:

```python
COST_PER_SIDE_FLOOR = 0.002
VEHICLE_COST_LIMIT = 0.05  # a cash vehicle's cost per side must be below 5%
```

In `src/signalbench/strategy/config.py`, replace:

```python
    stop_atr_mult: float
    trail_atr_mult: float
    time_limit: int
```

with:

```python
    stop_atr_mult: float
    trail_atr_mult: float
    time_limit: int | None  # None: exit only on the trailing stop, earnings, or the run's end
```

In `src/signalbench/strategy/config.py`, replace:

```python
@dataclass(frozen=True)
class BacktestParams:
```

with:

```python
@dataclass(frozen=True)
class CashVehicle:
    """Where idle cash waits between trades (spec 06), traded at the open like a stock."""

    symbol: str
    cost_per_side: float


@dataclass(frozen=True)
class BacktestParams:
```

In `src/signalbench/strategy/config.py`, replace:

```python
    sentiment: SentimentParams
    backtest: BacktestParams

    def with_setups(self, setups: tuple[SetupName, ...]) -> "StrategyConfig":
```

with:

```python
    sentiment: SentimentParams
    backtest: BacktestParams
    cash_vehicle: CashVehicle | None = None  # None: idle cash stays cash (v1)

    def with_setups(self, setups: tuple[SetupName, ...]) -> "StrategyConfig":
```

In `src/signalbench/strategy/config.py`, replace:

```python
    def integer(self, key: str, minimum: int = 1) -> int:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ConfigError(f"{self._where}.{key}: expected an integer >= {minimum}")
        return value
```

with:

```python
    def has(self, key: str) -> bool:
        return key in self._raw

    def integer(self, key: str, minimum: int = 1) -> int:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ConfigError(f"{self._where}.{key}: expected an integer >= {minimum}")
        return value

    def integer_or_null(self, key: str, minimum: int = 1) -> int | None:
        """Like integer(), but an explicit null is allowed. The key must still be written."""
        if self._raw.get(key, 0) is None:
            self._used.add(key)
            return None
        return self.integer(key, minimum)
```

In `src/signalbench/strategy/config.py`, replace:

```python
    priority = top.setups("setup_priority")
    config = StrategyConfig(
```

with:

```python
    priority = top.setups("setup_priority")
    vehicle = _cash_vehicle(top, where) if top.has("cash_vehicle") else None
    config = StrategyConfig(
```

In `src/signalbench/strategy/config.py`, replace:

```python
            time_limit=brk.integer("time_limit"),
```

with:

```python
            time_limit=brk.integer_or_null("time_limit"),
```

In `src/signalbench/strategy/config.py`, replace:

```python
            min_mean_r=bar.number("min_mean_r"),
        ),
    )
```

with:

```python
            min_mean_r=bar.number("min_mean_r"),
        ),
        cash_vehicle=vehicle,
    )
```

In `src/signalbench/strategy/config.py`, replace:

```python
    if config.backtest.h2_start != config.backtest.h1_end + timedelta(days=1):
```

with:

```python
    if vehicle is not None and vehicle.symbol != config.regime_symbol:
        raise ConfigError(
            f"{where}.cash_vehicle.symbol: must be the regime symbol {config.regime_symbol!r} "
            f"(the only series besides the universe that a run loads), got {vehicle.symbol!r}"
        )
    if config.backtest.h2_start != config.backtest.h1_end + timedelta(days=1):
```

In `src/signalbench/strategy/config.py`, replace:

```python
def config_sha256(raw: bytes) -> str:
```

with:

```python
def _cash_vehicle(top: _Section, where: str) -> CashVehicle:
    section = top.section("cash_vehicle")
    vehicle = CashVehicle(
        symbol=section.text("symbol"), cost_per_side=section.number("cost_per_side")
    )
    section.done()
    if vehicle.cost_per_side >= VEHICLE_COST_LIMIT:
        raise ConfigError(
            f"{where}.cash_vehicle.cost_per_side: expected a number in "
            f"[0, {VEHICLE_COST_LIMIT}), got {vehicle.cost_per_side}"
        )
    return vehicle


def config_sha256(raw: bytes) -> str:
```

- [ ] **Step 4: Let a signal and an entry order carry no time limit**

In `src/signalbench/strategy/setups.py`, replace:

```python
    target_r: float | None
    time_limit: int
```

with:

```python
    target_r: float | None
    time_limit: int | None  # None: no time limit (spec 06, Breakout only)
```

In `src/signalbench/strategy/decision.py`, replace:

```python
    target_r: float | None  # None: no target (Breakout)
    time_limit: int
```

with:

```python
    target_r: float | None  # None: no target (Breakout)
    time_limit: int | None  # None: no time limit (spec 06, Breakout only)
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_strategy_config.py tests/test_setups.py tests/test_simulator.py -v && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 43 passed; the full suite 457 passed; ruff and mypy clean.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/strategy/config.py src/signalbench/strategy/setups.py src/signalbench/strategy/decision.py tests/test_strategy_config.py tests/test_setups.py tests/test_simulator.py
git commit -m "feat: optional Breakout time limit and a cash_vehicle block in the strategy config

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 4: The simulator's cash vehicle

**Files:**
- Modify: `src/signalbench/backtest/simulator.py`, `src/signalbench/strategy/portfolio.py`
- Create: `tests/test_simulator_cash_vehicle.py`

- [ ] **Step 1: Write the failing tests**

Every number below is worked by hand from the fixture: QQQ opens and closes at 300 + 0.5 i; AAA signals on 251 (close 146), fills at the 252 open of 146, and exits for time at the 262 open. With the cash vehicle, the 100 start equity buys QQQ at the 240 open, the 252 entry is paid by selling just enough QQQ, and the 262 exit proceeds buy QQQ back.

Create `tests/test_simulator_cash_vehicle.py`:

```python
"""Idle cash in QQQ (spec 06), worked by hand on the pullback fixture of test_simulator.py.

QQQ opens and closes at 300 + 0.5 i on DAYS[i]. AAA dips on SIGNAL (close 146), fills at the
ENTRY open of 146, and exits for time at the EXIT open (10 sessions, flat at 146).
"""

from dataclasses import replace
from datetime import date

from pytest import approx

from signalbench.backtest.simulator import SimulationResult, simulate
from signalbench.market.bars import AdjustedBar
from signalbench.strategy.config import CashVehicle, StrategyConfig
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    load_test_config,
    make_market,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
    with_bar,
)

PLAIN = load_test_config().with_setups(("pullback",))
QQQ = replace(PLAIN, cash_vehicle=CashVehicle(symbol="QQQ", cost_per_side=0.002))
DAYS = weekdays(date(2023, 1, 2), 280)
START, SIGNAL, ENTRY, EXIT = 240, 251, 252, 262
COST = 0.002


def _qqq(i: int) -> float:
    return 300.0 + 0.5 * i


def _run(
    config: StrategyConfig = QQQ,
    bars: dict[str, list[AdjustedBar]] | None = None,
    benchmark: list[AdjustedBar] | None = None,
    sectors: dict[str, str] | None = None,
    end: int = 270,
) -> SimulationResult:
    bars = bars or {"AAA": series(DAYS, pullback_closes(len(DAYS)))}
    benchmark = benchmark or trend_bars(DAYS, 300.0, 0.5)
    market = make_market(bars, benchmark, DAYS, config, sectors=sectors)
    return simulate(market, NullReadingsView(), config, DAYS[START], DAYS[end])


def _vehicle_events(result: SimulationResult) -> list[dict[str, object]]:
    return [e for e in result.events if str(e["event"]).startswith("vehicle_")]


def _point(result: SimulationResult, i: int) -> tuple[float, float, float]:
    point = result.equity_curve[i - START]
    assert point.date == DAYS[i]
    return point.equity, point.cash, point.vehicle_value


PARKED = 100.0 / (_qqq(START) * (1 + COST))  # QQQ units bought with the start equity


def test_the_start_equity_is_parked_at_the_first_open_and_quiet_days_do_not_switch() -> None:
    events = _vehicle_events(_run())
    assert [(e["date"], e["event"]) for e in events] == [
        (DAYS[START].isoformat(), "vehicle_buy"),
        (DAYS[ENTRY].isoformat(), "vehicle_sell"),
        (DAYS[EXIT].isoformat(), "vehicle_buy"),
    ]
    first = events[0]
    assert (first["symbol"], first["price"]) == ("QQQ", _qqq(START))
    assert first["units"] == approx(PARKED)
    assert first["amount"] == approx(100.0 / (1 + COST))  # the market value bought
    assert first["cost"] == approx(100.0 - 100.0 / (1 + COST))


def test_sizing_uses_total_equity_and_sell_to_fund_charges_only_the_amount_sold() -> None:
    result = _run()
    equity = PARKED * _qqq(SIGNAL)  # all in QQQ at the signal close
    assert _point(result, SIGNAL) == approx((equity, 0.0, equity))
    [entry] = [e for e in result.events if e["event"] == "entry"]
    units = equity / 3 / 146.0  # the equity/3 cap, on total equity (v1 would use 100)
    assert entry["units"] == approx(units)
    need = units * 146.0 * (1 + COST)
    [sell] = [e for e in _vehicle_events(result) if e["event"] == "vehicle_sell"]
    assert sell["price"] == _qqq(ENTRY)
    assert sell["amount"] == approx(need / (1 - COST))  # just enough to cover the entry
    assert sell["cost"] == approx(need / (1 - COST) * COST)
    left = PARKED - need / (1 - COST) / _qqq(ENTRY)
    assert sell["units"] == approx(PARKED - left)
    # The close of ENTRY: no cash, the stock at 146, the rest of QQQ at its close.
    assert _point(result, ENTRY) == approx(
        (units * 146.0 + left * _qqq(ENTRY), 0.0, left * _qqq(ENTRY)), abs=1e-12
    )


def test_exit_proceeds_are_parked_at_the_open() -> None:
    result = _run()
    [trade] = result.trades
    assert (trade.reason, trade.exit_date) == ("time", DAYS[EXIT])
    proceeds = trade.units * 146.0 * (1 - COST)
    [_, _, park] = _vehicle_events(result)
    assert park["amount"] == approx(proceeds / (1 + COST))
    assert park["cost"] == approx(proceeds - proceeds / (1 + COST))
    held = PARKED - (trade.units * 146.0 * (1 + COST)) / (1 - COST) / _qqq(ENTRY)
    held += proceeds / (_qqq(EXIT) * (1 + COST))
    assert _point(result, EXIT) == approx(
        (held * _qqq(EXIT), 0.0, held * _qqq(EXIT)), abs=1e-12
    )
    assert _point(result, 270) == approx((held * _qqq(270), 0.0, held * _qqq(270)), abs=1e-12)


def test_the_cash_cap_counts_qqq_at_the_open_net_of_the_switching_cost() -> None:
    # Three identical signals open 1% up: A and B fill in full, C gets what QQQ can still pay
    # for after its 0.2% selling cost, and every QQQ unit is sold.
    bars = with_bar(series(DAYS, pullback_closes(len(DAYS))), ENTRY, open=146.0 * 1.01)
    sectors = {"AAA": "Energy", "BBB": "Utilities", "CCC": "Financials"}
    result = _run(bars={symbol: bars for symbol in sectors}, sectors=sectors, end=260)
    entries = [e for e in result.events if e["event"] == "entry"]
    assert [(e["symbol"], e["trimmed"]) for e in entries] == [
        ("AAA", False), ("BBB", False), ("CCC", True),
    ]
    fill = 146.0 * 1.01 * (1 + COST)
    available = PARKED * _qqq(ENTRY) * (1 - COST)
    spent = sum(float(str(e["units"])) * fill for e in entries)
    assert spent == approx(available)
    [sell] = [e for e in _vehicle_events(result) if e["event"] == "vehicle_sell"]
    assert sell["units"] == approx(PARKED)
    _, cash, vehicle = _point(result, ENTRY)
    assert (cash, vehicle) == (approx(0.0, abs=1e-9), approx(0.0, abs=1e-9))


def test_the_drawdown_pause_sees_the_qqq_value() -> None:
    # No stock ever signals; QQQ falls 20% on DAYS[250]. Only the QQQ variant pauses.
    closes = [_qqq(i) for i in range(250)] + [_qqq(249) * 0.8] * (len(DAYS) - 250)
    benchmark = series(DAYS, closes)
    flat = {"AAA": trend_bars(DAYS, 50.0, 0.0)}
    with_qqq = _run(bars=flat, benchmark=benchmark)
    [pause] = [e for e in with_qqq.events if e["event"] == "pause"]
    assert pause["date"] == DAYS[250].isoformat()
    assert pause["equity"] == approx(PARKED * _qqq(249) * 0.8)
    plain = _run(config=PLAIN, bars=flat, benchmark=benchmark)
    assert [e for e in plain.events if e["event"] == "pause"] == []
    assert _vehicle_events(plain) == []
    assert {point.vehicle_value for point in plain.equity_curve} == {0.0}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_simulator_cash_vehicle.py -v`
Expected: 5 failed, because the simulator ignores `cash_vehicle` so far: no `vehicle_*` events (`assert [] == [...]`); `AttributeError: 'EquityPoint' object has no attribute 'vehicle_value'`; `ValueError: not enough values to unpack` twice (no QQQ purchase after the exit, and no pause); and the cash-cap test spends 100.0, the cash alone, instead of about 101.02 (QQQ at the open, net of its cost).

- [ ] **Step 3: Implement the cash vehicle**

Replace the whole of `src/signalbench/backtest/simulator.py` with the file below. The changes: the module docstring, `DUST`, `EquityPoint.vehicle_value`, the `entry_skip` docstring, the vehicle state and `sleeve` at the top of each session, `filled`, QQQ in `open_equity` and in the open-time cash cap, the `_switch` call after the fills, QQQ in the close-of-session equity and in `PortfolioState.cash`, and the helpers `_vehicle_prices`, `_switch`, and `_settled`. Every other line is unchanged.

```python
"""Day-by-day simulation of decide() over NYSE sessions (spec 02, Simulator).

For each session t: at the open, fill the exits and then the entries decided at t-1;
at the close, mark to market, update the peak and pause state, and call decide(t).

With a cash vehicle (spec 06), idle cash waits in the regime symbol (QQQ): on a session with
fills, QQQ is sold at the open just enough to pay for the entries, or the cash left after the
fills buys QQQ at the open. The start equity is parked at the first open. Configs without a
cash vehicle never touch QQQ and behave exactly as before.
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
class _Opened:
    signal_date: date
    signal_close: float
    initial_stop: float

    @property
    def planned_risk(self) -> float:
        """Per-unit risk planned at the signal: signal close - stop (the unit of R)."""
        return self.signal_close - self.initial_stop


@dataclass(frozen=True)
class _Orders:
    """What decide() returned at a close, to fill at the next open."""

    decided_on: date
    exits: list[ExitOrder]
    entries: list[EntryOrder]


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
    cash = config.start_equity
    vehicle = config.cash_vehicle
    vehicle_units = 0.0
    positions: dict[str, Position] = {}
    opened: dict[str, _Opened] = {}
    risk = RiskState(peak=config.start_equity, paused=False, paused_since=None, paused_at=None)
    orders: _Orders | None = None
    trades: list[TradeRecord] = []
    events: list[Event] = []
    curve: list[EquityPoint] = []
    next_id = 1

    for index, day in enumerate(sessions):
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
                meta = _Opened(orders.decided_on, entry.signal_close, entry.stop)
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
        spendable = cash  # what decide() may size against: cash, plus the vehicle net of cost
        if vehicle is not None:
            benchmark = view.benchmark()
            vehicle_value = 0.0 if benchmark is None else vehicle_units * benchmark.close
            spendable = cash + vehicle_value * (1.0 - vehicle.cost_per_side)
        equity = cash + value + vehicle_value
        risk, change = step_risk(risk, equity, index, day, config)
        if change is not None:
            events.append(_event(day, change, equity=equity, peak=risk.peak))
        curve.append(EquityPoint(day, equity, cash, len(positions), vehicle_value))

        state = PortfolioState(
            cash=spendable,
            positions=tuple(positions.values()),
            pending=(),
            equity=equity,
            peak=risk.peak,
            paused=risk.paused,
            paused_since=risk.paused_since,
        )
        decision = decide(day, market, readings, state, config)
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
        orders = _Orders(day, sorted(exits, key=lambda e: e.position_id), decision.entries)

    return SimulationResult(
        start=sessions[0],
        end=sessions[-1],
        equity_curve=curve,
        trades=trades,
        events=events,
        open_positions=list(positions.values()),
        open_at_end=_open_at_end(market, sessions[-1], positions, opened),
    )


def _open_at_end(
    market: MarketView, end: date, positions: dict[str, Position], opened: dict[str, _Opened]
) -> list[OpenPositionRecord]:
    """Positions still held at the last close, marked the way the equity curve marks them."""
    view = market.at(end)
    records: list[OpenPositionRecord] = []
    for position in positions.values():
        snap = view.snapshot(position.symbol)
        close = position.entry_price if snap is None else snap.close
        records.append(
            OpenPositionRecord(
                position_id=position.id,
                symbol=position.symbol,
                setup=position.setup,
                sector=position.sector,
                signal_date=opened[position.id].signal_date,
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

In `src/signalbench/strategy/portfolio.py`, replace:

```python
class PortfolioState:
    cash: float
```

with:

```python
class PortfolioState:
    cash: float  # with a cash vehicle (spec 06): cash + the vehicle at the close, net of its cost
```

In `src/signalbench/strategy/portfolio.py`, replace:

```python
    equity: float  # cash + sum(units * close) at the as-of close
```

with:

```python
    equity: float  # cash + sum(units * close) at the as-of close, + the cash vehicle (spec 06)
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_simulator_cash_vehicle.py tests/test_simulator.py tests/test_v1_regression.py -v && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 19 passed (the v1 pins still hold: without `cash_vehicle` every new term is `+ 0.0` or skipped); the full suite 462 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/simulator.py src/signalbench/strategy/portfolio.py tests/test_simulator_cash_vehicle.py
git commit -m "feat: simulator keeps idle cash in the cash vehicle (spec 06)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Look-ahead check for a QQQ variant

**Files:**
- Modify: `tests/test_decide.py`

Spec 02's look-ahead test compares `decide()` on truncated and full data. The QQQ variant adds trading at QQQ's open and valuing it at QQQ's close inside the simulator, so this case compares whole simulations: a QQQ-variant run on data cut at `as_of` must equal the full-data run up to `as_of`, trade for trade, event for event, and point for point on the equity curve (QQQ value included). QQQ's opens differ from its closes here, so reading the wrong bar would show.

- [ ] **Step 1: Write the test**

In `tests/test_decide.py`, replace:

```python
from signalbench.market.bars import AdjustedBar
from signalbench.strategy.decide import decide
```

with:

```python
from signalbench.backtest.simulator import simulate
from signalbench.market.bars import AdjustedBar
from signalbench.strategy.config import CashVehicle
from signalbench.strategy.decide import decide
```

In `tests/test_decide.py`, replace:

```python
def test_decide_is_deterministic() -> None:
```

with:

```python
def test_a_qqq_variant_run_on_truncated_data_equals_the_full_run_so_far() -> None:
    """Spec 06: the cash vehicle trades QQQ only at the open or close of the simulated session,
    like every stock, so a run cut at as_of is exactly the full run up to as_of."""
    rng = random.Random(20260928)
    days = weekdays(date(2021, 1, 4), 420)
    symbols = {name: _random_walk(rng, days) for name in ("AAA", "BBB", "CCC", "DDD")}
    sectors = {"AAA": "Energy", "BBB": "Energy", "CCC": "Utilities", "DDD": "Financials"}
    qqq = []
    for i, day in enumerate(days):
        close = (300.0 + 0.3 * i) * (1 + rng.gauss(0.0, 0.01))
        qqq.append(make_bar(day, close, open_=close * (1 + rng.gauss(0.0, 0.005))))
    base = CONFIG.with_setups(("breakout",))
    config = replace(
        base,
        breakout=replace(base.breakout, time_limit=None),
        cash_vehicle=CashVehicle(symbol="QQQ", cost_per_side=0.002),
    )
    full = make_market(symbols, qqq, days, config, sectors=sectors)
    whole = simulate(full, NULL, config, days[250], days[410])
    kinds = {event["event"] for event in whole.events}
    assert {"entry", "exit", "vehicle_buy", "vehicle_sell"} <= kinds  # the sample trades QQQ
    for index in sorted(rng.sample(range(255, 410), 8)):
        as_of = days[index]
        truncated = make_market(
            {name: _truncate(bars, as_of) for name, bars in symbols.items()},
            _truncate(qqq, as_of), days, config, sectors=sectors,
        )
        cut = simulate(truncated, NULL, config, days[250], as_of)
        assert cut.equity_curve == whole.equity_curve[: index - 250 + 1]
        assert cut.events == [e for e in whole.events if str(e["date"]) <= as_of.isoformat()]
        assert cut.trades == [t for t in whole.trades if t.exit_date <= as_of]


def test_decide_is_deterministic() -> None:
```

(`gauss` is fine here: the test compares two runs in one process, never a stored digest.)

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_decide.py -v`
Expected: 16 passed. It passes at once because the simulator reads QQQ only through `AsOfView.benchmark()`, which never returns a bar after the session being simulated; the test guards that from now on.

- [ ] **Step 3: Full checks**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 463 passed; ruff and mypy clean.

- [ ] **Step 4: Commit**

```bash
git add tests/test_decide.py
git commit -m "test: look-ahead check for a QQQ cash-vehicle run

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Cash-vehicle stats

**Files:**
- Modify: `src/signalbench/backtest/metrics.py`, `tests/test_run_metrics.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_run_metrics.py`, replace:

```python
from signalbench.backtest.metrics import (
    TradeStats,
    cagr,
    max_drawdown,
    run_metrics,
    sharpe,
    trade_stats,
)
from signalbench.backtest.simulator import EquityPoint, SimulationResult, TradeRecord
```

with:

```python
from signalbench.backtest.metrics import (
    TradeStats,
    VehicleStats,
    cagr,
    max_drawdown,
    run_metrics,
    sharpe,
    trade_stats,
    vehicle_stats,
)
from signalbench.backtest.simulator import EquityPoint, SimulationResult, TradeRecord
from signalbench.strategy.config import CashVehicle
```

Append to `tests/test_run_metrics.py`:

```python
def test_vehicle_stats_by_hand() -> None:
    days = [date(2012, 1, 3), date(2012, 1, 4), date(2012, 1, 5), date(2012, 1, 6)]
    curve = [
        EquityPoint(days[0], 100.0, 0.0, 0, 100.0),  # all in QQQ
        EquityPoint(days[1], 100.0, 0.0, 1, 60.0),  # 40 in a stock
        EquityPoint(days[2], 100.0, 10.0, 1, 40.0),  # 50 in a stock, 10 in cash
        EquityPoint(days[3], 100.0, 0.0, 0, 100.0),
    ]

    def switch(day: date, kind: str, cost: float) -> dict[str, object]:
        return {"date": day.isoformat(), "event": kind, "symbol": "QQQ", "units": 0.1,
                "price": 400.0, "amount": cost / 0.002, "cost": cost}

    events: list[dict[str, object]] = [
        switch(days[0], "vehicle_buy", 0.2),
        {"date": "2012-01-04", "event": "entry", "symbol": "AAA"},
        switch(days[1], "vehicle_sell", 0.08),
        switch(days[3], "vehicle_buy", 0.1),
    ]
    result = SimulationResult(days[0], days[-1], curve, [], events, open_positions=[])
    assert vehicle_stats(result, CashVehicle("QQQ", 0.002)) == VehicleStats(
        symbol="QQQ",
        cost_per_side=0.002,
        share_vehicle=approx(0.75),
        share_stocks=approx(0.225),
        share_cash=approx(0.025),
        switches=3,
        buys=2,
        sells=1,
        switch_cost=approx(0.38),
    )
```

By hand: QQQ (1 + 0.6 + 0.4 + 1) / 4 = 0.75; stocks (0 + 0.4 + 0.5 + 0) / 4 = 0.225; cash 0.1 / 4 = 0.025; cost 0.2 + 0.08 + 0.1 = 0.38.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_run_metrics.py -v`
Expected: collection error, `ImportError: cannot import name 'VehicleStats' from 'signalbench.backtest.metrics'`.

- [ ] **Step 3: Implement `vehicle_stats`**

In `src/signalbench/backtest/metrics.py`, replace:

```python
from collections.abc import Sequence
```

with:

```python
from collections.abc import Callable, Sequence
```

In `src/signalbench/backtest/metrics.py`, replace:

```python
from statistics import fmean, median, stdev

import numpy as np

from signalbench.backtest.simulator import SimulationResult, TradeRecord
from signalbench.strategy.config import BacktestParams
```

with:

```python
from statistics import fmean, median, stdev
from typing import cast

import numpy as np

from signalbench.backtest.simulator import EquityPoint, SimulationResult, TradeRecord
from signalbench.strategy.config import BacktestParams, CashVehicle
```

Append to `src/signalbench/backtest/metrics.py`:

```python
@dataclass(frozen=True)
class VehicleStats:
    """How a run used its cash vehicle (spec 06). Shares are averages over the run's sessions
    of each part's share of equity at the close; they sum to 1."""

    symbol: str
    cost_per_side: float
    share_vehicle: float
    share_stocks: float
    share_cash: float
    switches: int  # buys + sells
    buys: int
    sells: int
    switch_cost: float  # in the same units as equity (start equity 100)


def vehicle_stats(result: SimulationResult, vehicle: CashVehicle) -> VehicleStats:
    points = [point for point in result.equity_curve if point.equity > 0.0]

    def share(part: Callable[[EquityPoint], float]) -> float:
        return fmean(part(point) / point.equity for point in points) if points else 0.0

    buys = [e for e in result.events if e["event"] == "vehicle_buy"]
    sells = [e for e in result.events if e["event"] == "vehicle_sell"]
    return VehicleStats(
        symbol=vehicle.symbol,
        cost_per_side=vehicle.cost_per_side,
        share_vehicle=share(lambda p: p.vehicle_value),
        share_stocks=share(lambda p: p.equity - p.cash - p.vehicle_value),
        share_cash=share(lambda p: p.cash),
        switches=len(buys) + len(sells),
        buys=len(buys),
        sells=len(sells),
        switch_cost=math.fsum(cast(float, e["cost"]) for e in buys + sells),
    )
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_run_metrics.py -v && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 5 passed; the full suite 464 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/metrics.py tests/test_run_metrics.py
git commit -m "feat: cash-vehicle stats: equity shares, switches, and their cost

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Report — the QQQ sleeve, the sensitivity line, the caveats, and report names

**Files:**
- Modify: `src/signalbench/backtest/report.py`, `tests/test_report.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_report.py`, replace:

```python
from signalbench.backtest.metrics import TradeStats, run_metrics
from signalbench.backtest.passbar import evaluate_pass_bar, passes
from signalbench.backtest.report import (
    CAVEATS,
    JEV_CAVEATS,
    jev_payload,
```

with:

```python
from signalbench.backtest.metrics import TradeStats, VehicleStats, run_metrics
from signalbench.backtest.passbar import evaluate_pass_bar, passes
from signalbench.backtest.report import (
    CAVEATS,
    JEV_CAVEATS,
    QQQ_CAVEATS,
    cash_vehicle_payload,
    jev_payload,
```

Append to `tests/test_report.py`:

```python
def test_other_versions_put_the_version_in_the_report_name() -> None:
    path = report_path(Path("reports/backtests"), date(2026, 9, 29), "breakout", "off", "v2-t60-qqq")
    assert path == Path("reports/backtests/2026-09-29-v2-t60-qqq-breakout-off.md")
    v1 = report_path(Path("reports/backtests"), date(2026, 9, 29), "breakout", "off", "v1")
    assert v1 == Path("reports/backtests/2026-09-29-breakout-off.md")


def _with_qqq(run: BacktestRun) -> BacktestRun:
    stats = VehicleStats(
        symbol="QQQ", cost_per_side=0.002, share_vehicle=0.312, share_stocks=0.68,
        share_cash=0.008, switches=812, buys=400, sells=412, switch_cost=12.345,
    )
    sensitivity = BenchmarkStats("QQQ switching at 0.05%", 24.736, 0.247, 1.1034, 0.333)
    run.metrics["cash_vehicle"] = cash_vehicle_payload(stats, sensitivity, 0.0005)
    return run


def test_a_qqq_report_shows_shares_switches_sensitivity_and_caveats() -> None:
    run = _with_qqq(_run(setup="breakout", version="v2-t30-qqq"))
    assert json.loads(json.dumps(run.metrics)) == run.metrics
    assert run.metrics["cash_vehicle"]["sensitivity"] == {
        "cost_per_side": 0.0005, "total_return": 24.736, "cagr": 0.247, "sharpe": 1.1034,
        "max_drawdown": 0.333,
    }
    text = render_report(run)
    assert "**POST-HOC** (v2-t30-qqq): cannot overturn a v1 result on its own." in text
    assert "A PASS only means the variant did not fail on the past; nothing goes live from it." in text
    assert "## Idle cash in QQQ" in text
    assert "| Switching cost per side | 0.20% |" in text
    assert "| Average share of equity in QQQ | 31.2% |" in text
    assert "| Average share of equity in stocks | 68.0% |" in text
    assert "| Average share of equity in cash | 0.8% |" in text
    assert "| QQQ switches (buys / sells) | 812 (400 / 412) |" in text
    assert "| Total switching cost (equity units) | 12.35 |" in text
    assert "Exposure above counts sessions holding a stock" in text
    assert (
        "**Sensitivity (information only): QQQ switching at 0.05%** — total return 2473.6%, "
        "CAGR 24.7%, Sharpe 1.10, max drawdown 33.3%. Only the 0.20% run is stored and judged."
    ) in text
    for caveat in (*CAVEATS, *QQQ_CAVEATS):
        assert f"- {caveat}" in text
    assert text.index("## Idle cash in QQQ") < text.index("## Skips by reason")


def test_reports_without_a_cash_vehicle_have_no_qqq_section() -> None:
    text = render_report(_run(setup="breakout", version="v2-none-cash"))
    assert "**POST-HOC** (v2-none-cash)" in text
    assert "Idle cash in" not in text and "Sensitivity" not in text
    for caveat in QQQ_CAVEATS:
        assert caveat not in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_report.py -v`
Expected: collection error, `ImportError: cannot import name 'QQQ_CAVEATS' from 'signalbench.backtest.report'`.

- [ ] **Step 3: Implement the report additions**

In `src/signalbench/backtest/report.py`, replace:

```python
from signalbench.backtest.metrics import RunMetrics, TradeStats
```

with:

```python
from signalbench.backtest.metrics import RunMetrics, TradeStats, VehicleStats
```

In `src/signalbench/backtest/report.py`, replace:

```python
PASS_BAR_ROWS = (
```

with:

```python
QQQ_CAVEATS = (
    "Post-hoc: the ideas came from looking at v1's results on the same data.",
    (
        "QQQ's 2012–2026 run was exceptional; holding more QQQ helps less or hurts if the next "
        "decade differs."
    ),
    (
        "Taxes are not modeled. In a non-registered account each QQQ switch is a disposition, "
        "and selling at a loss and rebuying within 30 days can be a superficial loss. "
        "Fractional units of the ETF are assumed."
    ),
    (
        "QQQ's adjusted prices stand in for the fund actually used, a CAD-listed, CAD-hedged "
        "Nasdaq-100 ETF, the same way US prices stand in for the hedged CDRs."
    ),
)
PASS_BAR_ROWS = (
```

In `src/signalbench/backtest/report.py`, replace:

```python
def pass_bar_payload(bar: dict[str, Criterion]) -> dict[str, Any]:
```

with:

```python
def cash_vehicle_payload(
    stats: VehicleStats, sensitivity: BenchmarkStats, sensitivity_cost: float
) -> dict[str, Any]:
    """Spec 06 facts stored under metrics["cash_vehicle"] for runs with a cash vehicle. The
    sensitivity run (the same config at `sensitivity_cost` per switch) is information only."""
    payload: dict[str, Any] = asdict(stats)
    payload["sensitivity"] = {
        "cost_per_side": sensitivity_cost,
        "total_return": sensitivity.total_return,
        "cagr": sensitivity.cagr,
        "sharpe": sensitivity.sharpe,
        "max_drawdown": sensitivity.max_drawdown,
    }
    return payload


def pass_bar_payload(bar: dict[str, Criterion]) -> dict[str, Any]:
```

In `src/signalbench/backtest/report.py`, replace:

```python
def report_path(directory: Path, run_date: date, setup: str, jev_mode: str) -> Path:
    return directory / f"{run_date.isoformat()}-{setup}-{jev_mode}.md"
```

with:

```python
def report_path(
    directory: Path, run_date: date, setup: str, jev_mode: str, version: str = "v1"
) -> Path:
    """<date>-<setup>-<jev>.md for v1; other versions add theirs (spec 06), so variants run on
    the same day get their own files: <date>-<version>-<setup>-<jev>.md."""
    name = f"{setup}-{jev_mode}" if version == "v1" else f"{version}-{setup}-{jev_mode}"
    return directory / f"{run_date.isoformat()}-{name}.md"
```

In `src/signalbench/backtest/report.py`, replace:

```python
def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"
```

with:

```python
def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _cost(value: float) -> str:
    return f"{value * 100:.2f}%"
```

In `src/signalbench/backtest/report.py`, replace:

```python
        lines += [
            f"**POST-HOC** ({run.strategy_version}): cannot overturn a v1 result on its own.",
            "",
        ]
```

with:

```python
        lines += [
            (
                f"**POST-HOC** ({run.strategy_version}): cannot overturn a v1 result on its own. "
                "A PASS only means the variant did not fail on the past; nothing goes live from it."
            ),
            "",
        ]
```

In `src/signalbench/backtest/report.py`, replace:

```python
def _skips_and_caveats(m: dict[str, Any]) -> list[str]:
```

with:

```python
def _cash_vehicle(m: dict[str, Any]) -> list[str]:
    vehicle: dict[str, Any] | None = m.get("cash_vehicle")
    if vehicle is None:
        return []
    symbol = vehicle["symbol"]
    sensitivity = vehicle["sensitivity"]
    return [
        f"## Idle cash in {symbol}",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Switching cost per side | {_cost(vehicle['cost_per_side'])} |",
        f"| Average share of equity in {symbol} | {_pct(vehicle['share_vehicle'])} |",
        f"| Average share of equity in stocks | {_pct(vehicle['share_stocks'])} |",
        f"| Average share of equity in cash | {_pct(vehicle['share_cash'])} |",
        (
            f"| {symbol} switches (buys / sells) | {vehicle['switches']} "
            f"({vehicle['buys']} / {vehicle['sells']}) |"
        ),
        f"| Total switching cost (equity units) | {vehicle['switch_cost']:.2f} |",
        "",
        (
            f"Exposure above counts sessions holding a stock; {symbol} is not counted. Total "
            f"return, Sharpe, and drawdown are of total equity, {symbol} included."
        ),
        "",
        (
            f"**Sensitivity (information only): {symbol} switching at "
            f"{_cost(sensitivity['cost_per_side'])}** — total return "
            f"{_pct(sensitivity['total_return'])}, CAGR {_pct(sensitivity['cagr'])}, Sharpe "
            f"{sensitivity['sharpe']:.2f}, max drawdown {_pct(sensitivity['max_drawdown'])}. "
            f"Only the {_cost(vehicle['cost_per_side'])} run is stored and judged."
        ),
    ]


def _skips_and_caveats(m: dict[str, Any]) -> list[str]:
```

In `src/signalbench/backtest/report.py`, replace:

```python
        *([f"- {caveat}" for caveat in JEV_CAVEATS] if "jev" in m else []),
    ]
```

with:

```python
        *([f"- {caveat}" for caveat in JEV_CAVEATS] if "jev" in m else []),
        *([f"- {caveat}" for caveat in QQQ_CAVEATS] if "cash_vehicle" in m else []),
    ]
```

In `src/signalbench/backtest/report.py`, replace:

```python
        _benchmarks(run.metrics),
        _skips_and_caveats(run.metrics),
```

with:

```python
        _benchmarks(run.metrics),
        _cash_vehicle(run.metrics),
        _skips_and_caveats(run.metrics),
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_report.py -v && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 15 passed; the full suite 467 passed; ruff and mypy clean. (`report_path` keeps v1 names, so the runner tests still pass until Task 8 passes the version in.)

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/report.py tests/test_report.py
git commit -m "feat: report the QQQ sleeve, the 0.05% sensitivity line, and the QQQ caveats

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Runner — sleeve stats, the 0.05% rerun, and report names

**Files:**
- Modify: `src/signalbench/backtest/runner.py`, `tests/test_runner.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_runner.py`, replace:

```python
from datetime import UTC, date, datetime, time
```

with:

```python
from dataclasses import replace
from datetime import UTC, date, datetime, time
```

In `tests/test_runner.py`, replace:

```python
from signalbench.market.bars import AdjustedBar
from strategy_helpers import (
    WeekdaySessions,
    load_test_config,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)
```

with:

```python
from signalbench.market.bars import AdjustedBar
from signalbench.strategy.config import CashVehicle
from strategy_helpers import (
    WeekdaySessions,
    load_test_config,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
    with_bar,
)
```

The fixture config's version is `test`, so its reports now carry the version.

In `tests/test_runner.py`, replace:

```python
    assert path == tmp_path / "2026-09-24-pullback-off.md"
```

with:

```python
    assert path == tmp_path / "2026-09-24-test-pullback-off.md"
```

In `tests/test_runner.py`, replace:

```python
    assert second.name == f"2026-09-24-pullback-off-{str(second_run.id)[:8]}.md"
```

with:

```python
    assert second.name == f"2026-09-24-test-pullback-off-{str(second_run.id)[:8]}.md"
```

In `tests/test_runner.py`, replace:

```python
    assert path.name == "2026-09-24-sentiment-off.md"
```

with:

```python
    assert path.name == "2026-09-24-test-sentiment-off.md"
```

In `tests/test_runner.py`, replace:

```python
    assert path.name == "2026-09-24-pullback-filter.md"
```

with:

```python
    assert path.name == "2026-09-24-test-pullback-filter.md"
```

Append to `tests/test_runner.py`:

```python
def test_a_qqq_variant_stores_the_sleeve_and_the_sensitivity_of_an_unstored_second_run(
    session: Session, tmp_path: Path
) -> None:
    # AAA breaks out on a 2x volume day (DAYS[262]) and exits for time 30 sessions later;
    # QQQ holds the rest of the equity.
    aaa = with_bar(trend_bars(DAYS, 50.0, 0.1), 262, volume=2_000_000)
    _store(session, "AAA", TickerKind.us_stock, aaa)
    _store(session, "BBB", TickerKind.us_stock, trend_bars(DAYS, 60.0, 0.05))
    _store(session, "QQQ", TickerKind.benchmark, trend_bars(DAYS, 300.0, 0.5))
    config = replace(
        load_test_config(), version="v2-t30-qqq", cash_vehicle=CashVehicle("QQQ", 0.002)
    )
    run, path = run_backtest(
        session, setup="breakout", jev_mode="off", config=config, config_sha256="f" * 64,
        universe=UNIVERSE, calendar=WeekdaySessions(), git_sha="abc123",
        run_date=date(2026, 9, 24), reports_dir=tmp_path, now=LATER,
    )
    assert len(session.exec(select(BacktestRun)).all()) == 1  # the 0.05% run is not stored
    assert [(t["signal_date"], t["reason"]) for t in run.trade_log["trades"]] == [
        (DAYS[262].isoformat(), "time")
    ]
    kinds = [e["event"] for e in run.trade_log["events"]]
    assert kinds.count("vehicle_buy") == 2 and kinds.count("vehicle_sell") == 1
    vehicle = run.metrics["cash_vehicle"]
    assert (vehicle["symbol"], vehicle["cost_per_side"]) == ("QQQ", 0.002)
    assert (vehicle["switches"], vehicle["buys"], vehicle["sells"]) == (3, 2, 1)
    assert vehicle["share_vehicle"] + vehicle["share_stocks"] + vehicle["share_cash"] == (
        pytest.approx(1.0)
    )
    assert vehicle["share_vehicle"] > 0.5
    sensitivity = vehicle["sensitivity"]
    assert sensitivity["cost_per_side"] == 0.0005
    assert sensitivity["total_return"] > run.metrics["total_return"]  # cheaper switching
    assert run.pass_bar["sharpe"]["value"] == run.metrics["sharpe"]  # total equity, QQQ in
    stored = {
        "AAA": aaa, "BBB": trend_bars(DAYS, 60.0, 0.05), "QQQ": trend_bars(DAYS, 300.0, 0.5)
    }
    assert run.data_fingerprint == data_fingerprint(stored.items(), [])  # QQQ was already in
    assert path.name == "2026-09-24-v2-t30-qqq-breakout-off.md"
    text = path.read_text(encoding="utf-8")
    assert "**POST-HOC** (v2-t30-qqq)" in text
    assert "## Idle cash in QQQ" in text
    assert "**Sensitivity (information only): QQQ switching at 0.05%**" in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_runner.py -v`
Expected: 5 failed, 17 passed: the four renamed reports still get v1-style names, and the new test stops at `KeyError: 'cash_vehicle'`.

- [ ] **Step 3: Implement the runner changes**

In `src/signalbench/backtest/runner.py`, replace:

```python
"""Load real data, run one backtest, store it, and write its report (specs 02 and 03)."""
```

with:

```python
"""Load real data, run one backtest, store it, and write its report (specs 02, 03, and 06)."""
```

In `src/signalbench/backtest/runner.py`, replace:

```python
from signalbench.backtest.metrics import run_metrics, trade_stats
```

with:

```python
from signalbench.backtest.metrics import run_metrics, trade_stats, vehicle_stats
```

In `src/signalbench/backtest/runner.py`, replace:

```python
from signalbench.backtest.report import (
    jev_payload,
```

with:

```python
from signalbench.backtest.report import (
    cash_vehicle_payload,
    jev_payload,
```

In `src/signalbench/backtest/runner.py`, replace:

```python
SENTIMENT_START = date(2016, 1, 1)  # spec 03: filings are backfilled from 2016
```

with:

```python
SENTIMENT_START = date(2016, 1, 1)  # spec 03: filings are backfilled from 2016
SENSITIVITY_COST = 0.0005  # spec 06: cash-vehicle switching cost of the information-only rerun
```

In `src/signalbench/backtest/runner.py`, replace:

```python
    Refuses (RunRefusedError) when stored runs of this strategy version used another config,
```

with:

```python
    A config with a cash vehicle (spec 06) is simulated a second time at SENSITIVITY_COST per
    switch; only its total return, CAGR, Sharpe, and drawdown are kept, in the stored metrics.

    Refuses (RunRefusedError) when stored runs of this strategy version used another config,
```

In `src/signalbench/backtest/runner.py`, replace:

```python
    bar = evaluate_pass_bar(metrics, qqq.sharpe, config.backtest)
    payload = metrics_payload(metrics, [qqq, survivor], config.backtest)
```

with:

```python
    bar = evaluate_pass_bar(metrics, qqq.sharpe, config.backtest)
    payload = metrics_payload(metrics, [qqq, survivor], config.backtest)
    vehicle = config.cash_vehicle
    if vehicle is not None:
        # Spec 06: the same config again with cheaper switching, reported and never stored.
        cheap = replace(config, cash_vehicle=replace(vehicle, cost_per_side=SENSITIVITY_COST))
        rerun = simulate(market, readings, cheap, start, last)
        sensitivity = benchmark_stats(
            f"{vehicle.symbol} switching at {SENSITIVITY_COST:.2%}",
            [point.equity for point in rerun.equity_curve],
            run_sessions,
        )
        payload["cash_vehicle"] = cash_vehicle_payload(
            vehicle_stats(result, vehicle), sensitivity, SENSITIVITY_COST
        )
```

In `src/signalbench/backtest/runner.py`, replace:

```python
    path = report_path(reports_dir, run_date, setup, jev_mode)
```

with:

```python
    path = report_path(reports_dir, run_date, setup, jev_mode, config.version)
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_runner.py tests/test_v1_regression.py -v && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 24 passed; the full suite 468 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/runner.py tests/test_runner.py
git commit -m "feat: runner stores the QQQ sleeve stats and a 0.05% sensitivity rerun

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: CLI — confirm `--config` carries a v2 config

**Files:**
- Modify: `tests/test_cli.py`

`backtest run --config PATH` already loads any `data/strategy_<version>.yaml`, applies the pre-registration guard (committed and unchanged, path named after the version, survey cost), and passes the parsed config to `run_backtest`. No CLI code changes; this test proves it for a QQQ variant with no time limit.

- [ ] **Step 1: Write the test**

In `tests/test_cli.py`, replace:

```python
from signalbench.strategy.config import config_sha256, load_strategy_config
```

with:

```python
from signalbench.strategy.config import (
    CashVehicle,
    StrategyConfig,
    config_sha256,
    load_strategy_config,
)
```

Append to `tests/test_cli.py`:

```python
def test_backtest_run_passes_a_qqq_variant_config_through(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Spec 06 needs no new CLI option: --config carries `time_limit: null` and `cash_vehicle`."""
    calls: dict[str, object] = {}

    def fake_run(_session: Session, **kwargs: object) -> tuple[BacktestRun, Path]:
        calls.update(kwargs)
        return _stored_run(session), tmp_path / "2026-09-29-v2-none-qqq-breakout-off.md"

    config = _registered_config(monkeypatch, tmp_path, version="v2-none-qqq")
    body = config.read_text(encoding="utf-8").replace("    time_limit: 30\n", "    time_limit: null\n")
    body = body.replace(
        "cost_per_side: 0.002\n",
        "cost_per_side: 0.002\ncash_vehicle:\n  symbol: QQQ\n  cost_per_side: 0.002\n",
        1,
    )
    config.write_text(body, encoding="utf-8")
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "git_sha", lambda _repo: "abc123")
    monkeypatch.setattr(cli, "NyseSessions", lambda: "calendar")
    monkeypatch.setattr(cli, "run_backtest", fake_run)
    result = runner.invoke(app, ["backtest", "run", "--setup", "breakout", "--config", str(config)])
    assert result.exit_code == 0, result.stderr
    strategy = calls["config"]
    assert isinstance(strategy, StrategyConfig)
    assert strategy.version == "v2-none-qqq"
    assert strategy.breakout.time_limit is None
    assert strategy.cash_vehicle == CashVehicle(symbol="QQQ", cost_per_side=0.002)
    assert calls["config_sha256"] == config_sha256(config.read_bytes())
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 35 passed (the new test passes at once: nothing in the CLI needed changing).

- [ ] **Step 3: Full checks**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 469 passed; ruff and mypy clean.

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: backtest run passes a QQQ-variant config through unchanged

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The six variant configs (written, not committed)

**Files:**
- Create: `tests/test_strategy_v2_files.py` (committed in this task)
- Create: `data/strategy_v2-t30-cash.yaml`, `data/strategy_v2-t30-qqq.yaml`, `data/strategy_v2-t60-cash.yaml`, `data/strategy_v2-t60-qqq.yaml`, `data/strategy_v2-none-cash.yaml`, `data/strategy_v2-none-qqq.yaml` (**not** committed here: Task 12 commits them on their own, after the owner reads them)

Each file is `data/strategy_v1.yaml` with a new header comment and only three changes: `version`, `setups.breakout.time_limit`, and (QQQ variants) a `cash_vehicle` block right after the top-level `cost_per_side`. The test checks exactly that, line by line, and that each parses to v1 plus those changes.

- [ ] **Step 1: Write the test**

Create `tests/test_strategy_v2_files.py`:

```python
"""The six spec 06 variants: each is data/strategy_v1.yaml with only `version`, the Breakout
`time_limit`, and `cash_vehicle` changed, under a post-hoc header (spec 06, Variants)."""

from dataclasses import replace
from pathlib import Path

import pytest

from signalbench.strategy.config import CashVehicle, load_strategy_config
from signalbench.strategy.spread import load_spread_survey

DATA = Path(__file__).resolve().parents[1] / "data"
QQQ = CashVehicle(symbol="QQQ", cost_per_side=0.002)
VARIANTS: dict[str, tuple[int | None, CashVehicle | None]] = {
    "v2-t30-cash": (30, None),
    "v2-t30-qqq": (30, QQQ),
    "v2-t60-cash": (60, None),
    "v2-t60-qqq": (60, QQQ),
    "v2-none-cash": (None, None),
    "v2-none-qqq": (None, QQQ),
}


def _variant(version: str) -> Path:
    path = DATA / f"strategy_{version}.yaml"
    if not path.exists():
        pytest.skip(f"{path.name} is written by spec 06 Task 10 and committed after review")
    return path


def _body(path: Path) -> str:
    """The file without its header comment lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "".join(f"{line}\n" for line in lines if not line.startswith("#"))


def _replace_once(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


@pytest.mark.parametrize("version", list(VARIANTS))
def test_each_variant_is_v1_with_only_the_declared_fields_changed(version: str) -> None:
    path = _variant(version)
    time_limit, vehicle = VARIANTS[version]
    expected = _replace_once(_body(DATA / "strategy_v1.yaml"), "version: v1\n", f"version: {version}\n")
    written = "null" if time_limit is None else str(time_limit)
    expected = _replace_once(expected, "    time_limit: 30\n", f"    time_limit: {written}\n")
    if vehicle is not None:
        expected = _replace_once(
            expected,
            "cost_per_side: 0.002\nsetup_priority",
            "cost_per_side: 0.002\ncash_vehicle:\n  symbol: QQQ\n  cost_per_side: 0.002\nsetup_priority",
        )
    assert _body(path) == expected
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("# Post-hoc v2 variant (spec 06):")


@pytest.mark.parametrize("version", list(VARIANTS))
def test_each_variant_parses_to_v1_plus_its_changes(version: str) -> None:
    path = _variant(version)
    time_limit, vehicle = VARIANTS[version]
    v1, v1_sha = load_strategy_config(DATA / "strategy_v1.yaml")
    config, sha = load_strategy_config(path)
    assert config == replace(
        v1, version=version, breakout=replace(v1.breakout, time_limit=time_limit),
        cash_vehicle=vehicle,
    )
    assert config.cost_per_side == load_spread_survey(DATA / "cdr_spread_survey.yaml").cost_per_side
    assert sha != v1_sha
```

- [ ] **Step 2: Run it before the files exist**

Run: `uv run pytest tests/test_strategy_v2_files.py -v`
Expected: 12 skipped (`strategy_v2-….yaml is written by spec 06 Task 10 and committed after review`).

- [ ] **Step 3: Write the six configs**

Create `data/strategy_v2-t30-cash.yaml`:

```yaml
# Post-hoc v2 variant (spec 06): Breakout time_limit 30, idle cash stays cash.
# A copy of data/strategy_v1.yaml with only version, setups.breakout.time_limit, and
# cash_vehicle changed, committed with the other five variants before any v2 run.
# Do not edit: its reports are post-hoc (2012-2026 was already seen), and any change is a
# new version. cost_per_side comes from data/cdr_spread_survey.yaml via
# `signalbench backtest cost`.
# No cash_vehicle key: idle cash stays cash, as in v1.
version: v2-t30-cash
start_equity: 100
risk_pct: 0.02
max_positions: 3
max_per_sector: 2
pause_drawdown: 0.15
auto_resume_sessions: 10
gap_up_limit: 0.01
cost_per_side: 0.002
setup_priority: [pullback, breakout, sentiment]
regime:
  symbol: QQQ
  sma: 200
indicators:
  atr_period: 14
  rsi_period: 2
liquidity:
  min_median_traded_value_usd: 50000000
  sessions: 20
earnings:
  blackout_sessions: 3
  exit_sessions: 2
setups:
  pullback:
    trend_sma_fast: 50
    trend_sma_slow: 200
    rsi_max: 10
    stop_low_sessions: 3
    stop_atr_mult: 0.5
    target_r: 2.0
    time_limit: 10
  breakout:
    trend_sma: 50
    lookback: 20
    volume_lookback: 50
    volume_mult: 1.5
    stop_atr_mult: 2.0
    trail_atr_mult: 3.0
    time_limit: 30
  sentiment:
    trend_sma: 20
    stop_atr_mult: 2.0
    target_r: 2.0
    time_limit: 10
backtest:
  start: 2012-01-01
  h1_end: 2018-12-31
  h2_start: 2019-01-01
  recent_since: 2026-09-15
  pass_bar:
    min_trades: 30
    min_mean_r: 0.10
```

Create `data/strategy_v2-t30-qqq.yaml`:

```yaml
# Post-hoc v2 variant (spec 06): Breakout time_limit 30, idle cash in QQQ.
# A copy of data/strategy_v1.yaml with only version, setups.breakout.time_limit, and
# cash_vehicle changed, committed with the other five variants before any v2 run.
# Do not edit: its reports are post-hoc (2012-2026 was already seen), and any change is a
# new version. cost_per_side comes from data/cdr_spread_survey.yaml via
# `signalbench backtest cost`.
# cash_vehicle.cost_per_side is the same 0.2% CDR cost rule (owner decision, 2026-09-28).
version: v2-t30-qqq
start_equity: 100
risk_pct: 0.02
max_positions: 3
max_per_sector: 2
pause_drawdown: 0.15
auto_resume_sessions: 10
gap_up_limit: 0.01
cost_per_side: 0.002
cash_vehicle:
  symbol: QQQ
  cost_per_side: 0.002
setup_priority: [pullback, breakout, sentiment]
regime:
  symbol: QQQ
  sma: 200
indicators:
  atr_period: 14
  rsi_period: 2
liquidity:
  min_median_traded_value_usd: 50000000
  sessions: 20
earnings:
  blackout_sessions: 3
  exit_sessions: 2
setups:
  pullback:
    trend_sma_fast: 50
    trend_sma_slow: 200
    rsi_max: 10
    stop_low_sessions: 3
    stop_atr_mult: 0.5
    target_r: 2.0
    time_limit: 10
  breakout:
    trend_sma: 50
    lookback: 20
    volume_lookback: 50
    volume_mult: 1.5
    stop_atr_mult: 2.0
    trail_atr_mult: 3.0
    time_limit: 30
  sentiment:
    trend_sma: 20
    stop_atr_mult: 2.0
    target_r: 2.0
    time_limit: 10
backtest:
  start: 2012-01-01
  h1_end: 2018-12-31
  h2_start: 2019-01-01
  recent_since: 2026-09-15
  pass_bar:
    min_trades: 30
    min_mean_r: 0.10
```

Create `data/strategy_v2-t60-cash.yaml`:

```yaml
# Post-hoc v2 variant (spec 06): Breakout time_limit 60, idle cash stays cash.
# A copy of data/strategy_v1.yaml with only version, setups.breakout.time_limit, and
# cash_vehicle changed, committed with the other five variants before any v2 run.
# Do not edit: its reports are post-hoc (2012-2026 was already seen), and any change is a
# new version. cost_per_side comes from data/cdr_spread_survey.yaml via
# `signalbench backtest cost`.
# No cash_vehicle key: idle cash stays cash, as in v1.
version: v2-t60-cash
start_equity: 100
risk_pct: 0.02
max_positions: 3
max_per_sector: 2
pause_drawdown: 0.15
auto_resume_sessions: 10
gap_up_limit: 0.01
cost_per_side: 0.002
setup_priority: [pullback, breakout, sentiment]
regime:
  symbol: QQQ
  sma: 200
indicators:
  atr_period: 14
  rsi_period: 2
liquidity:
  min_median_traded_value_usd: 50000000
  sessions: 20
earnings:
  blackout_sessions: 3
  exit_sessions: 2
setups:
  pullback:
    trend_sma_fast: 50
    trend_sma_slow: 200
    rsi_max: 10
    stop_low_sessions: 3
    stop_atr_mult: 0.5
    target_r: 2.0
    time_limit: 10
  breakout:
    trend_sma: 50
    lookback: 20
    volume_lookback: 50
    volume_mult: 1.5
    stop_atr_mult: 2.0
    trail_atr_mult: 3.0
    time_limit: 60
  sentiment:
    trend_sma: 20
    stop_atr_mult: 2.0
    target_r: 2.0
    time_limit: 10
backtest:
  start: 2012-01-01
  h1_end: 2018-12-31
  h2_start: 2019-01-01
  recent_since: 2026-09-15
  pass_bar:
    min_trades: 30
    min_mean_r: 0.10
```

Create `data/strategy_v2-t60-qqq.yaml`:

```yaml
# Post-hoc v2 variant (spec 06): Breakout time_limit 60, idle cash in QQQ.
# A copy of data/strategy_v1.yaml with only version, setups.breakout.time_limit, and
# cash_vehicle changed, committed with the other five variants before any v2 run.
# Do not edit: its reports are post-hoc (2012-2026 was already seen), and any change is a
# new version. cost_per_side comes from data/cdr_spread_survey.yaml via
# `signalbench backtest cost`.
# cash_vehicle.cost_per_side is the same 0.2% CDR cost rule (owner decision, 2026-09-28).
version: v2-t60-qqq
start_equity: 100
risk_pct: 0.02
max_positions: 3
max_per_sector: 2
pause_drawdown: 0.15
auto_resume_sessions: 10
gap_up_limit: 0.01
cost_per_side: 0.002
cash_vehicle:
  symbol: QQQ
  cost_per_side: 0.002
setup_priority: [pullback, breakout, sentiment]
regime:
  symbol: QQQ
  sma: 200
indicators:
  atr_period: 14
  rsi_period: 2
liquidity:
  min_median_traded_value_usd: 50000000
  sessions: 20
earnings:
  blackout_sessions: 3
  exit_sessions: 2
setups:
  pullback:
    trend_sma_fast: 50
    trend_sma_slow: 200
    rsi_max: 10
    stop_low_sessions: 3
    stop_atr_mult: 0.5
    target_r: 2.0
    time_limit: 10
  breakout:
    trend_sma: 50
    lookback: 20
    volume_lookback: 50
    volume_mult: 1.5
    stop_atr_mult: 2.0
    trail_atr_mult: 3.0
    time_limit: 60
  sentiment:
    trend_sma: 20
    stop_atr_mult: 2.0
    target_r: 2.0
    time_limit: 10
backtest:
  start: 2012-01-01
  h1_end: 2018-12-31
  h2_start: 2019-01-01
  recent_since: 2026-09-15
  pass_bar:
    min_trades: 30
    min_mean_r: 0.10
```

Create `data/strategy_v2-none-cash.yaml`:

```yaml
# Post-hoc v2 variant (spec 06): Breakout time_limit null (no time limit), idle cash stays cash.
# A copy of data/strategy_v1.yaml with only version, setups.breakout.time_limit, and
# cash_vehicle changed, committed with the other five variants before any v2 run.
# Do not edit: its reports are post-hoc (2012-2026 was already seen), and any change is a
# new version. cost_per_side comes from data/cdr_spread_survey.yaml via
# `signalbench backtest cost`.
# No cash_vehicle key: idle cash stays cash, as in v1.
version: v2-none-cash
start_equity: 100
risk_pct: 0.02
max_positions: 3
max_per_sector: 2
pause_drawdown: 0.15
auto_resume_sessions: 10
gap_up_limit: 0.01
cost_per_side: 0.002
setup_priority: [pullback, breakout, sentiment]
regime:
  symbol: QQQ
  sma: 200
indicators:
  atr_period: 14
  rsi_period: 2
liquidity:
  min_median_traded_value_usd: 50000000
  sessions: 20
earnings:
  blackout_sessions: 3
  exit_sessions: 2
setups:
  pullback:
    trend_sma_fast: 50
    trend_sma_slow: 200
    rsi_max: 10
    stop_low_sessions: 3
    stop_atr_mult: 0.5
    target_r: 2.0
    time_limit: 10
  breakout:
    trend_sma: 50
    lookback: 20
    volume_lookback: 50
    volume_mult: 1.5
    stop_atr_mult: 2.0
    trail_atr_mult: 3.0
    time_limit: null
  sentiment:
    trend_sma: 20
    stop_atr_mult: 2.0
    target_r: 2.0
    time_limit: 10
backtest:
  start: 2012-01-01
  h1_end: 2018-12-31
  h2_start: 2019-01-01
  recent_since: 2026-09-15
  pass_bar:
    min_trades: 30
    min_mean_r: 0.10
```

Create `data/strategy_v2-none-qqq.yaml`:

```yaml
# Post-hoc v2 variant (spec 06): Breakout time_limit null (no time limit), idle cash in QQQ.
# A copy of data/strategy_v1.yaml with only version, setups.breakout.time_limit, and
# cash_vehicle changed, committed with the other five variants before any v2 run.
# Do not edit: its reports are post-hoc (2012-2026 was already seen), and any change is a
# new version. cost_per_side comes from data/cdr_spread_survey.yaml via
# `signalbench backtest cost`.
# cash_vehicle.cost_per_side is the same 0.2% CDR cost rule (owner decision, 2026-09-28).
version: v2-none-qqq
start_equity: 100
risk_pct: 0.02
max_positions: 3
max_per_sector: 2
pause_drawdown: 0.15
auto_resume_sessions: 10
gap_up_limit: 0.01
cost_per_side: 0.002
cash_vehicle:
  symbol: QQQ
  cost_per_side: 0.002
setup_priority: [pullback, breakout, sentiment]
regime:
  symbol: QQQ
  sma: 200
indicators:
  atr_period: 14
  rsi_period: 2
liquidity:
  min_median_traded_value_usd: 50000000
  sessions: 20
earnings:
  blackout_sessions: 3
  exit_sessions: 2
setups:
  pullback:
    trend_sma_fast: 50
    trend_sma_slow: 200
    rsi_max: 10
    stop_low_sessions: 3
    stop_atr_mult: 0.5
    target_r: 2.0
    time_limit: 10
  breakout:
    trend_sma: 50
    lookback: 20
    volume_lookback: 50
    volume_mult: 1.5
    stop_atr_mult: 2.0
    trail_atr_mult: 3.0
    time_limit: null
  sentiment:
    trend_sma: 20
    stop_atr_mult: 2.0
    target_r: 2.0
    time_limit: 10
backtest:
  start: 2012-01-01
  h1_end: 2018-12-31
  h2_start: 2019-01-01
  recent_since: 2026-09-15
  pass_bar:
    min_trades: 30
    min_mean_r: 0.10
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_strategy_v2_files.py -v && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 12 passed; the full suite 481 passed; ruff and mypy clean.

Also compare each file with v1 by eye (`--strip-trailing-cr` because v1 is checked out with CRLF on Windows):

Run: `for v in t30-cash t30-qqq t60-cash t60-qqq none-cash none-qqq; do echo "== $v"; diff --strip-trailing-cr data/strategy_v1.yaml data/strategy_v2-$v.yaml; done`
Expected: for each file, only the header comment lines, the `version:` line, the Breakout `time_limit:` line (except the two `t30` files, where it stays 30), and for the three QQQ files the added `cash_vehicle:` block of three lines. (The loop exits 1: `diff` does when files differ.)

- [ ] **Step 5: Commit the test only**

```bash
git add tests/test_strategy_v2_files.py
git commit -m "test: the six spec 06 variant configs are v1 plus their declared changes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Leave the six `data/strategy_v2-*.yaml` files untracked. `git status --short` should list exactly those six (plus the owner's `README.md` change).

---

### Task 11: Docs — spec 06 implementation choices and the owner's README line

**Files:**
- Modify: `docs/superpowers/specs/2026-09-28-swing-assistant-06-breakout-v2-design.md`

- [ ] **Step 1: Record the implementation choices in spec 06**

Append to `docs/superpowers/specs/2026-09-28-swing-assistant-06-breakout-v2-design.md`:

```markdown
- 2026-09-28: implementation choices (plan `2026-09-28-swing-06-breakout-v2.md`):
  - `cash_vehicle.symbol` must be the regime symbol (QQQ), the only series besides the universe that a run loads; its cost must be in [0, 0.05). `time_limit: null` must be written out (the key stays required) and only Breakout accepts it. The `-cash` configs have no `cash_vehicle` key.
  - The start equity is parked in QQQ at the first session's open (the spec is silent on the start). After that QQQ trades only on sessions with an exit or entry fill, at most once per session: the day's net need, at QQQ's open, filled like a stock (open × (1 − cost) when selling, open × (1 + cost) when buying), so the cost falls only on the amount that moves. On a session without a QQQ bar the sleeve does not trade.
  - `decide()` sizes on total equity, with a cash cap of cash plus QQQ at the close, net of the selling cost; the open-time cash cap uses QQQ at the open, net of the same cost.
  - Reports of versions other than v1 are named `<date>-<version>-<setup>-<jev>.md`, and their POST-HOC line adds that a PASS decides nothing. QQQ reports add an "Idle cash in QQQ" section (average shares of equity in QQQ, stocks, and cash; switches; total switching cost), the 0.05% sensitivity line from an unstored rerun, and four caveats: the spec's three plus the CAD-hedged ETF stand-in. A config without `cash_vehicle` stores exactly the v1 metrics keys, events, and fingerprint (pinned by `tests/test_v1_regression.py`).
```

- [ ] **Step 2: Run the checks**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 481 passed; ruff and mypy clean.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-09-28-swing-assistant-06-breakout-v2-design.md
git commit -m "docs: spec 06 implementation choices

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Tell the owner the README line**

`README.md` carries the owner's uncommitted edits, so do not touch it. Ask the owner to change the `backtest run` row of the CLI table themselves, from "…and write `reports/backtests/<date>-<setup>-<jev>.md`." to "…and write `reports/backtests/<date>-<setup>-<jev>.md` (`<date>-<version>-<setup>-<jev>.md` for versions other than v1). A config with `cash_vehicle` keeps idle cash in QQQ and adds a 0.05% switching sensitivity line (spec 06)."

---

> ## ⛔ Controller: stop here until the owner has read the six configs and says go
>
> Tasks 12–16 commit the pre-registration and then run on the real database. Report the Task 1–11 results to the owner with the six `diff` outputs from Task 10 Step 4, the decisions above marked **Confirm with the owner** (the start equity parked at the first open), and the README line from Task 11. Wait for a clear go in chat. Once the configs are committed, **no parameter may change, whatever the results**: a change is a new version, and all six count as six tries.

---

### Task 12: ⛔ Commit the six configs on their own

**Gated:** the owner said go after reading the six files.

- [ ] **Step 1: Check the files are the ones the test checks**

Run: `uv run pytest tests/test_strategy_v2_files.py -q`
Expected: 12 passed.

- [ ] **Step 2: Commit the six files, and nothing else**

```bash
git add data/strategy_v2-t30-cash.yaml data/strategy_v2-t30-qqq.yaml data/strategy_v2-t60-cash.yaml data/strategy_v2-t60-qqq.yaml data/strategy_v2-none-cash.yaml data/strategy_v2-none-qqq.yaml
git commit -m "data: pre-registered spec 06 Breakout v2 variants (six configs)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Run: `git show --stat HEAD`
Expected: exactly the six `data/strategy_v2-*.yaml` files, nothing else.

---

### Task 13: ⛔ Run `v2-t30-cash` and check it reproduces v1 Breakout

**Gated:** Task 12 committed.

- [ ] **Step 1: Database up, tree clean**

Run: `docker compose up -d`
Then: `git status --short --untracked-files=no -- src data alembic pyproject.toml uv.lock`
Expected: no output (the runs record `git_sha`; the owner's `README.md` change does not matter here). Do **not** run `signalbench ingest prices`.

- [ ] **Step 2: Run it**

Run: `uv run signalbench backtest run --setup breakout --config data/strategy_v2-t30-cash.yaml`
Expected: `run <uuid>: FAIL`, the four criteria (`-- sharpe: 0.949 vs 0.996`), and `report: …/reports/backtests/<date>-v2-t30-cash-breakout-off.md`.

- [ ] **Step 3: Compare with the stored v1 Breakout report**

Run: `diff <(grep -hE "^\| (Sessions|data_fingerprint|Trades|Mean R|Total return|CAGR|Sharpe|Max drawdown|Exposure)\b" reports/backtests/2026-09-24-breakout-off.md) <(grep -hE "^\| (Sessions|data_fingerprint|Trades|Mean R|Total return|CAGR|Sharpe|Max drawdown|Exposure)\b" reports/backtests/<date>-v2-t30-cash-breakout-off.md) && echo identical`
Expected: `identical`. Those eleven lines of the v1 report are: sessions `2012-01-03 to 2026-09-24`; `data_fingerprint` `567662d64c503dc20a006c69ed7ee770a0600018835b37927475b7acf9943901`; 419 trades; mean R 0.311, H1 0.358 (206), H2 0.265 (213); total return 610.6%; CAGR 14.2%; Sharpe 0.95; max drawdown 24.8%; exposure 86.7%. The pass bar's Sharpe row must also read `0.949 | >= 0.996`. Only the header differs (version, run id, `config_sha256`, `git_sha`, run time, and the POST-HOC line).

- If the `data_fingerprint` differs, the stored data changed since the v1 run (for example, prices were ingested). **Stop and report to the owner**; do not run the other five.
- If the fingerprint matches but any number differs, it is a bug in this plan's code (spec 06: "any difference is a bug"). **Stop and report**; do not run the other five until it is fixed and this check passes. A fixed rerun of the same unchanged config is allowed; its report gets an id suffix, and both stored runs are mentioned in Task 16.

---

### Task 14: ⛔ Run the other five

**Gated:** Task 13 matched v1 exactly.

- [ ] **Step 1: Run them, one after another**

Run each, in this order, and keep every result whatever it is:

```bash
uv run signalbench backtest run --setup breakout --config data/strategy_v2-t30-qqq.yaml
uv run signalbench backtest run --setup breakout --config data/strategy_v2-t60-cash.yaml
uv run signalbench backtest run --setup breakout --config data/strategy_v2-t60-qqq.yaml
uv run signalbench backtest run --setup breakout --config data/strategy_v2-none-cash.yaml
uv run signalbench backtest run --setup breakout --config data/strategy_v2-none-qqq.yaml
```

Expected for each: `run <uuid>: PASS` or `FAIL`, the four criteria, and `report: …/reports/backtests/<date>-v2-<variant>-breakout-off.md`. Every report has the **POST-HOC** line. The three QQQ reports also have the "Idle cash in QQQ" section, the "Sensitivity (information only): QQQ switching at 0.05%" line, and the four QQQ caveats. The two `none` reports may list positions open at the end: with no time limit, a Breakout position exits only on its trailing stop, for earnings, or at the run's end. **Do not change any parameter or rerun with other settings, whatever the results.**

- [ ] **Step 2: Sanity checks**

Every `data_fingerprint` equals Task 13's. The three `-cash` reports have no QQQ section. In each QQQ report, the three average shares add up to 100% (within rounding), and the switches count is at least 1.

---

### Task 15: ⛔ Commit the six reports

**Gated:** Tasks 13–14 done.

- [ ] **Step 1: Commit**

```bash
git add reports/backtests/<date>-v2-t30-cash-breakout-off.md reports/backtests/<date>-v2-t30-qqq-breakout-off.md reports/backtests/<date>-v2-t60-cash-breakout-off.md reports/backtests/<date>-v2-t60-qqq-breakout-off.md reports/backtests/<date>-v2-none-cash-breakout-off.md reports/backtests/<date>-v2-none-qqq-breakout-off.md
git commit -m "docs: spec 06 Breakout v2 reports, all six post-hoc variants

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(`<date>` is the run date printed in each report path. If Task 13 needed a rerun, commit the report of the run that matched v1 and name the other in Task 16.)

---

### Task 16: ⛔ Research-log entry and the spec 06 results line

**Files:**
- Modify: `docs/research-log.md`, `docs/superpowers/specs/2026-09-28-swing-assistant-06-breakout-v2-design.md`

- [ ] **Step 1: One research-log entry with all six**

Insert a new entry in `docs/research-log.md` after the "Breakout with idle cash in QQQ (rough estimate)" entry and before `## Open questions`, in the log's style:

- Heading: `## <run date> — Breakout v2: idle cash in QQQ and holding time (spec 06)`.
- **Kind:** post-hoc (pre-registered in six configs committed before any run, commit `<Task 12 sha>`; 2012–2026 was already seen). Six tries.
- **Test:** one line on the two ideas and the six combinations; costs 0.2% per side on stocks and QQQ.
- **Result:** one table with rows v1 Breakout (from its report), the six variants, and QQQ buy-and-hold; columns Trades, Mean R (worse half), Total return, CAGR, Sharpe, Max drawdown, Average share in QQQ (QQQ rows only), Result (PASS or FAIL, with the failing criteria). Then one line with the three 0.05% sensitivity Sharpes.
- **Reproduction:** `v2-t30-cash` reproduced v1 Breakout exactly (419 trades, 610.6%, Sharpe 0.949; same `data_fingerprint`), or what happened instead.
- **Reports:** the six paths.
- **Takeaway:** two or three plain sentences. A PASS decides nothing: all six plus v1 Breakout go to forward paper trading (spec 06, order of work 5).

Also update the "Open questions" list: the idle-cash and holding-time items now point to this entry.

- [ ] **Step 2: One line in the spec 06 changelog**

Append to `docs/superpowers/specs/2026-09-28-swing-assistant-06-breakout-v2-design.md` one line: `- <run date>: results. v2-t30-cash reproduced v1 Breakout (419 trades, 610.6%, Sharpe 0.949). <variant>: PASS/FAIL (Sharpe … vs QQQ …; mean R …), …for all six…. Reports in reports/backtests/, logged in docs/research-log.md. Next: forward paper trading of all six plus v1 Breakout (owner decision).`

- [ ] **Step 3: Commit**

```bash
git add docs/research-log.md docs/superpowers/specs/2026-09-28-swing-assistant-06-breakout-v2-design.md
git commit -m "docs: research log and spec 06 changelog for the six Breakout v2 variants

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Send the owner the six report paths and the log table. Ask before pushing the branch or opening a PR (`feat/swing-03-jev-reader` is not merged; this branch sits on top of it).

---

## Spec coverage check

| Spec 06 requirement | Task |
| --- | --- |
| Six configs, copies of v1 with only `version`, Breakout `time_limit`, `cash_vehicle` changed, committed together before any real run | 10, 12 |
| Existing guard unchanged (path, committed and unchanged, survey cost) | 9 (no CLI change), 12–14 |
| `cash_vehicle: {symbol: QQQ, cost_per_side: 0.002}`; idle cash always in QQQ, regime ignored for the sleeve | 3, 4, 10 |
| `time_limit: null` for Breakout only; exits only on trailing stop, earnings, or the run's end | 2, 3 |
| Open: exits fill, proceeds to cash | 4 |
| Open: sell QQQ at its open just enough to fund entries, cost on the amount sold | 4 |
| Open: park leftover cash in QQQ at its open; nothing traded on a day without fills | 4 |
| Close: QQQ valued at its close; equity = cash + QQQ + positions | 4 |
| Sizing on total equity; cash cap = cash + QQQ at the open net of switching cost | 4 |
| Drawdown pause on total equity | 4 |
| Look-ahead: QQQ only at the session's open or close; look-ahead test gains a QQQ case | 4, 5 |
| `vehicle_buy` / `vehicle_sell` events with amount and cost | 4 |
| Configs without `cash_vehicle` behave byte for byte as before | 1 (pinned), 4, 8 |
| Pass bar unchanged; Sharpe of total equity vs QQQ; R per trade on planned risk | 4, 8 |
| Every report POST-HOC; a PASS decides nothing | 7 |
| Report: average QQQ share, stock exposure shown separately, switch count and cost | 6, 7 |
| Report: "Sensitivity (information only): QQQ switching at 0.05%" from a second, unstored simulation | 7, 8 |
| Caveats in each QQQ report | 7 |
| Test: config parsing and validation; missing `cash_vehicle` means none | 3 |
| Test: no time exit without a limit | 2, 3 |
| Test: hand-worked simulator cases (sell-to-fund, parking, quiet day, sizing, pause, equity curve) | 4 |
| Test: regression, a v1 config gives the same result and fingerprint | 1 |
| `v2-t30-cash` reproduces v1 Breakout on the real data, recorded in the log | 13, 16 |
| One research-log entry with all six plus v1 Breakout and QQQ; one spec changelog line | 16 |
