# Swing Assistant 02 — Strategy and Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Pullback and Breakout setups (and the Sentiment rule behind the `ReadingsView` port), risk sizing, and one pure `decide()`; simulate them day by day over 2012–present on stored data; and produce pre-registered pass-bar reports that decide which setups go live.

**Architecture:** A new `signalbench.strategy` package holds everything the live scan will reuse: a strictly loaded YAML config, causal indicators on plain float lists, a `MarketView` that precomputes indicators once and hands out look-ahead-safe `AsOfView`s, the `ReadingsView` port, portfolio and decision types, and `decide()` built from small pure modules (setups, exits, entries). The `signalbench.backtest` package adds the simulator, run metrics, benchmarks, the pass bar, the data fingerprint, git provenance, the markdown report, and a runner that loads real data and stores a `backtest_runs` row (migration `0010`). Real runs happen only after the spread survey sets `cost_per_side` and `data/strategy_v1.yaml` is committed.

**Tech Stack:** Python 3.11, plain dataclasses and floats (no pandas/numpy in strategy code), `exchange_calendars` (NYSE sessions, wrapped in one module), SQLModel/SQLAlchemy JSON columns, Alembic, PyYAML, Typer, pytest, ruff, mypy strict.

**Spec:** [`docs/superpowers/specs/2026-09-22-swing-assistant-02-strategy-backtest-design.md`](../specs/2026-09-22-swing-assistant-02-strategy-backtest-design.md)

---

## Conventions for every task

- Run commands from the repo root. Use `uv run …` for everything. Stay on branch `feat/swing-02-strategy-backtest`; never push without asking the owner.
- **Never stage `.env.example` or `data/cdr_spread_survey.yaml`** until Task 21 says so. Both are the owner's work in progress. Stage explicit paths only; never `git add -A` or `git add .`.
- mypy runs strict on `src/` only. In SQLModel `select()`, wrap `date` columns in `col()` and use `col(X).in_(…)` for column operators.
- **Strategy code is pure.** Nothing under `src/signalbench/strategy/` imports the database, a clock, the network, pandas, numpy, or `exchange_calendars`. It receives session lists and bars.
- Tests never touch the network or the real database. They use the in-memory SQLite `session` fixture from `tests/conftest.py` and synthetic bars from `tests/strategy_helpers.py` (created in Task 5). Test files import it as `from strategy_helpers import …`; pytest puts `tests/` on `sys.path` because it has no `__init__.py`.
- ruff 0.16 runs a broad default rule set, including import sorting (`I001`). If ruff only reports import order, run `uv run ruff check --fix .` and re-run. `strategy_helpers` sorts after `signalbench` (both are first-party).
- After each task: `uv run pytest -q`, `uv run ruff check .`, and `uv run mypy src` all pass before committing.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Every code block in this plan was run in a scratch copy of the repo before the plan was written: the full suite ended at 233 passed, 1 skipped (the `strategy_v1.yaml` check, until Task 21), with ruff and mypy clean. If a test's expected number disagrees with your run, look for a transcription slip in the module before touching the test.

## Formulas (the pre-registered definitions)

| Quantity | Definition |
| --- | --- |
| SMA(n) at i | mean of close[i−n+1 … i]; `None` before i = n−1 |
| Wilder RSI(p) | change_i = close_i − close_{i−1}; gain = max(change, 0), loss = max(−change, 0). Seed at i = p with the simple means of changes 1…p; then avg = (avg_prev × (p−1) + current) / p. RSI = 100 − 100 / (1 + avg_gain / avg_loss); 100 when avg_loss = 0 (50 if both are 0) |
| True range at i ≥ 1 | max(high − low, \|high − close_{i−1}\|, \|low − close_{i−1}\|), on adjusted OHLC |
| Wilder ATR(p) | seed at i = p with the mean of TR_1…TR_p; then ATR_i = (ATR_{i−1} × (p−1) + TR_i) / p |
| Prior max close (20) | max close of the 20 sessions before i (i excluded) |
| Prior mean volume (50) | mean volume of the 50 sessions before i (i excluded) |
| Min low (3) | min low of sessions i−2 … i |
| Median traded value (20) | median of raw close × volume over sessions i−19 … i (US$), the spec 01 liquidity measure |
| Equity | cash + Σ units × close at the as-of close |
| R | entry fill − initial stop; trade R = (exit fill − entry fill) / R, costs included |

`None` always means "no signal". Every indicator at index i uses only bars 0…i.

## Decisions this plan makes where the spec is silent

- **Migration number:** `backtest_runs` is `0010_backtest_runs`, because spec 01 already used `0009` for `raw_documents.form`. Specs 03–05 shift to `0011`–`0013`.
- **Extra skip reasons:** besides the spec list, `held` (signal on a symbol already held or pending), `no_cash` (sizing leaves zero units), and `no_bar` (no bar at the entry open). Symbols that fail the liquidity filter are ignored without a skip record, or the log would fill with them.
- **Gate order for a signal:** held → regime → paused → earnings_blackout → blocked. Then ranking, then no_slot / sector_cap / no_cash.
- **Slots at decision time:** a position with an exit order for tomorrow's open still holds its slot tonight.
- **`EntryOrder.risk_amount`** is the risk after the caps: units × (signal close − stop).
- **Cash at the open:** sizing uses the signal close; if the open plus cost would spend more than the cash left, units are trimmed to the cash (the entry event records `trimmed: true`). Cash never goes negative.
- **The equity/3 cap** is `equity / max_positions` (3 in v1).
- **Halves and the recent sample** are split by entry date. A half with no trades has mean R 0, which fails criterion 3.
- **Held-position bookkeeping in the simulator:** `sessions_held` and `highest_close` are updated at each close before `decide()`, so the entry session counts as 1 and "highest close since entry" includes the entry session's close.
- **`config_sha256`** hashes the file bytes with CRLF normalised to LF, because this repo checks files out with CRLF on Windows and LF in CI.
- **`cost_per_side`** = max(0.002, median spread / 2 + 0.001), rounded to 6 decimals. A median of exactly 1% is allowed; above 1% stops.
- **`combined`** = Pullback and Breakout together, reported for information only.
- **Pre-registration guard:** `backtest run` refuses to run unless its config file is tracked in git and unchanged from `HEAD`. `git_sha` gets a `-dirty` suffix when tracked files under `src`, `data`, `alembic`, `pyproject.toml`, or `uv.lock` have uncommitted changes.
- **Trade log extras:** besides fills, skips, pauses, and resumes, the log records `stop_update` and `exit_deferred` events (the latter when a symbol has no bar at the exit open; `decide()` re-issues the exit that evening).

## File map

| Path | Action | Responsibility |
| --- | --- | --- |
| `pyproject.toml`, `uv.lock` | Modify | `exchange-calendars` dependency; mypy override |
| `src/signalbench/market/calendar.py` | Create | `Sessions` protocol and `NyseSessions` (the only `exchange_calendars` import) |
| `src/signalbench/market/bars.py` | Modify | `AdjustedBar.traded_value` (raw close × volume) |
| `src/signalbench/strategy/__init__.py` | Create | Package marker |
| `src/signalbench/strategy/config.py` | Create | `StrategyConfig` and the strict YAML loader with `config_sha256` |
| `src/signalbench/strategy/indicators.py` | Create | SMA, Wilder RSI and ATR, prior max/mean, rolling min/median |
| `src/signalbench/strategy/readings.py` | Create | `ReadingsView` port and `NullReadingsView` |
| `src/signalbench/strategy/portfolio.py` | Create | `Position`, `PendingEntry`, `PortfolioState` |
| `src/signalbench/strategy/decision.py` | Create | `EntryOrder`, `ExitOrder`, `StopUpdate`, `Skip`, `Decision`, reason literals |
| `src/signalbench/strategy/market_view.py` | Create | `MarketView`, `AsOfView`, `Snapshot`, `LookAheadError` |
| `src/signalbench/strategy/setups.py` | Create | Pullback, Breakout, Sentiment rules; `first_signal` by priority |
| `src/signalbench/strategy/exits.py` | Create | Exit order (stop, earnings, target, time); Breakout trailing stop |
| `src/signalbench/strategy/entries.py` | Create | Gates, ranking, slots, sector cap, sizing |
| `src/signalbench/strategy/decide.py` | Create | The pure `decide()` |
| `src/signalbench/strategy/spread.py` | Create | Spread survey loader and `cost_per_side` |
| `src/signalbench/backtest/simulator.py` | Create | Day-by-day simulation, fills, costs, pause/resume, trade log |
| `src/signalbench/backtest/metrics.py` | Modify | Append run metrics (existing functions untouched) |
| `src/signalbench/backtest/benchmarks.py` | Create | QQQ buy-and-hold and the survivor benchmark |
| `src/signalbench/backtest/passbar.py` | Create | The four v1 criteria |
| `src/signalbench/backtest/fingerprint.py` | Modify | `data_fingerprint()` |
| `src/signalbench/backtest/report.py` | Create | Stored JSON payloads and the markdown report |
| `src/signalbench/backtest/provenance.py` | Create | `git_sha()` and `committed_unchanged()` |
| `src/signalbench/backtest/runner.py` | Create | Load DB data, run, store `BacktestRun`, write the report |
| `src/signalbench/db/models.py` | Modify | `BacktestRun` |
| `alembic/versions/0010_backtest_runs.py` | Create | `backtest_runs` table |
| `src/signalbench/cli.py` | Modify | `backtest run`, `backtest show`, `backtest cost` |
| `tests/fixtures/strategy_test.yaml` | Create | Test copy of the v1 parameters (cost at the 0.2% floor) |
| `tests/strategy_helpers.py` | Create | Synthetic bars, sessions, markets, snapshots, positions |
| `tests/test_*.py` | Create/modify | One test file per module (listed in each task) |
| `README.md`, spec 02 changelog | Modify | CLI reference, layout, implementation notes |
| `data/strategy_v1.yaml` | Create (gated, Task 21) | The pre-registered parameters |
| `reports/backtests/*.md` | Create (gated, Task 22) | The committed v1 reports |

---

### Task 1: NYSE session calendar

**Files:**
- Modify: `pyproject.toml`, `uv.lock`
- Create: `src/signalbench/market/calendar.py`, `tests/test_calendar.py`

- [ ] **Step 1: Write the failing test**

`tests/test_calendar.py`:

```python
from datetime import date

import pytest

from signalbench.market.calendar import NyseSessions, Sessions


@pytest.fixture(scope="module")
def nyse() -> NyseSessions:
    return NyseSessions(start=date(2022, 1, 1))


def test_thanksgiving_2022_is_not_a_session(nyse: NyseSessions) -> None:
    assert nyse.sessions_between(date(2022, 11, 21), date(2022, 11, 28)) == [
        date(2022, 11, 21),
        date(2022, 11, 22),
        date(2022, 11, 23),
        date(2022, 11, 25),
        date(2022, 11, 28),
    ]
    assert nyse.is_session(date(2022, 11, 24)) is False
    assert nyse.is_session(date(2022, 11, 25)) is True


def test_next_sessions_are_strictly_after_the_day(nyse: NyseSessions) -> None:
    assert nyse.next_sessions(date(2022, 11, 23), 3) == [
        date(2022, 11, 25),
        date(2022, 11, 28),
        date(2022, 11, 29),
    ]
    # Juneteenth observed on Monday 2022-06-20.
    assert nyse.next_sessions(date(2022, 6, 17), 1) == [date(2022, 6, 21)]


def test_empty_range(nyse: NyseSessions) -> None:
    assert nyse.sessions_between(date(2022, 11, 28), date(2022, 11, 21)) == []


def test_nyse_sessions_satisfies_the_protocol(nyse: NyseSessions) -> None:
    calendar: Sessions = nyse
    assert isinstance(calendar.sessions_between(date(2022, 1, 3), date(2022, 1, 3))[0], date)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_calendar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.market.calendar'`.

- [ ] **Step 3: Add the dependency and the mypy override**

In `pyproject.toml` `dependencies`, add this line after `"tzdata>=2024.1",`:

```toml
  "exchange-calendars>=4.13",
```

In the first `[[tool.mypy.overrides]]` block, change `module = ["yfinance"]` to:

```toml
module = ["yfinance", "exchange_calendars"]
```

`exchange_calendars` ships no type information, so mypy treats it as `Any`; the wrapper below converts everything to `datetime.date` at the boundary.

Run: `uv lock && uv sync --group dev`
Expected: `exchange-calendars` (4.13 or later), `pyluach`, `toolz`, and `korean-lunar-calendar` are added.

- [ ] **Step 4: Write the wrapper**

`src/signalbench/market/calendar.py`:

```python
"""NYSE sessions as plain dates. The only module that imports exchange_calendars."""

from datetime import date, timedelta
from typing import Any, Protocol

HISTORY_START = date(2010, 1, 1)


class Sessions(Protocol):
    def sessions_between(self, start: date, end: date) -> list[date]:
        """Sessions with start <= session <= end, in order."""
        ...

    def next_sessions(self, day: date, count: int) -> list[date]:
        """The first `count` sessions strictly after `day`."""
        ...

    def is_session(self, day: date) -> bool: ...


class NyseSessions:
    """The XNYS calendar from exchange_calendars, exposed as `datetime.date` values."""

    def __init__(self, start: date = HISTORY_START) -> None:
        import exchange_calendars as xcals

        self._calendar: Any = xcals.get_calendar("XNYS", start=start.isoformat())

    def sessions_between(self, start: date, end: date) -> list[date]:
        if end < start:
            return []
        return [stamp.date() for stamp in self._calendar.sessions_in_range(start, end)]

    def next_sessions(self, day: date, count: int) -> list[date]:
        # Weekends plus the longest NYSE closure stay far inside 2 * count + 10 calendar days.
        horizon = day + timedelta(days=2 * count + 10)
        found = self.sessions_between(day + timedelta(days=1), horizon)
        if len(found) < count:
            raise ValueError(f"Only {len(found)} NYSE sessions known after {day}; need {count}")
        return found[:count]

    def is_session(self, day: date) -> bool:
        return bool(self._calendar.is_session(day))
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_calendar.py -v && uv run mypy src && uv run ruff check .`
Expected: 4 passed; mypy `Success`; ruff `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/signalbench/market/calendar.py tests/test_calendar.py
git commit -m "feat: NYSE session calendar wrapper over exchange_calendars

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Strategy config

**Files:**
- Create: `src/signalbench/strategy/__init__.py` (empty), `src/signalbench/strategy/config.py`, `tests/fixtures/strategy_test.yaml`, `tests/test_strategy_config.py`

- [ ] **Step 1: Write the test fixture**

`tests/fixtures/strategy_test.yaml` (every spec 02 parameter; Task 21 copies it to `data/strategy_v1.yaml`):

```yaml
# Test copy of the v1 parameters. Not pre-registered; cost_per_side is the 0.2% floor.
# The real file is data/strategy_v1.yaml (spec 02, created after the spread survey).
version: test
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

- [ ] **Step 2: Write the failing test**

`tests/test_strategy_config.py`:

```python
from datetime import date
from pathlib import Path

import pytest
import yaml

from signalbench.strategy.config import (
    ConfigError,
    config_sha256,
    load_strategy_config,
    parse_strategy_config,
)

FIXTURE = Path(__file__).parent / "fixtures" / "strategy_test.yaml"


def _raw() -> dict[str, object]:
    loaded = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_fixture_loads_every_spec_parameter() -> None:
    config, sha = load_strategy_config(FIXTURE)
    assert config.version == "test"
    assert (config.start_equity, config.risk_pct) == (100.0, 0.02)
    assert (config.max_positions, config.max_per_sector) == (3, 2)
    assert (config.pause_drawdown, config.auto_resume_sessions) == (0.15, 10)
    assert (config.gap_up_limit, config.cost_per_side) == (0.01, 0.002)
    assert config.setup_priority == ("pullback", "breakout", "sentiment")
    assert config.enabled_setups == config.setup_priority
    assert (config.regime_symbol, config.regime_sma) == ("QQQ", 200)
    assert (config.atr_period, config.rsi_period) == (14, 2)
    assert (config.liquidity_min_traded_value, config.liquidity_sessions) == (50_000_000.0, 20)
    assert (config.earnings_blackout_sessions, config.earnings_exit_sessions) == (3, 2)
    assert config.pullback.trend_sma_fast == 50 and config.pullback.trend_sma_slow == 200
    assert config.pullback.rsi_max == 10.0 and config.pullback.stop_atr_mult == 0.5
    assert config.pullback.target_r == 2.0 and config.pullback.time_limit == 10
    assert config.breakout.lookback == 20 and config.breakout.volume_lookback == 50
    assert config.breakout.volume_mult == 1.5 and config.breakout.trail_atr_mult == 3.0
    assert config.breakout.time_limit == 30
    assert config.sentiment.trend_sma == 20 and config.sentiment.time_limit == 10
    assert config.backtest.start == date(2012, 1, 1)
    assert (config.backtest.h1_end, config.backtest.h2_start) == (date(2018, 12, 31), date(2019, 1, 1))
    assert config.backtest.recent_since == date(2026, 9, 15)
    assert (config.backtest.min_trades, config.backtest.min_mean_r) == (30, 0.10)
    assert len(sha) == 64


def test_sha_ignores_line_endings() -> None:
    assert config_sha256(b"a: 1\r\nb: 2\r\n") == config_sha256(b"a: 1\nb: 2\n")
    assert config_sha256(b"a: 1\n") != config_sha256(b"a: 2\n")


def test_unknown_key_is_rejected() -> None:
    raw = _raw()
    raw["risk_pct_typo"] = 0.03
    with pytest.raises(ConfigError, match="unknown keys \\['risk_pct_typo'\\]"):
        parse_strategy_config(raw)


def test_missing_nested_key_is_rejected() -> None:
    raw = _raw()
    setups = raw["setups"]
    assert isinstance(setups, dict)
    del setups["pullback"]["rsi_max"]
    with pytest.raises(ConfigError, match="config.setups.pullback: missing key 'rsi_max'"):
        parse_strategy_config(raw)


def test_cost_below_the_floor_is_rejected() -> None:
    raw = _raw()
    raw["cost_per_side"] = 0.001
    with pytest.raises(ConfigError, match="cost_per_side"):
        parse_strategy_config(raw)


def test_setup_priority_must_name_each_setup_once() -> None:
    raw = _raw()
    raw["setup_priority"] = ["pullback", "pullback", "breakout"]
    with pytest.raises(ConfigError, match="setup_priority"):
        parse_strategy_config(raw)


def test_with_setups_keeps_priority_order() -> None:
    config, _ = load_strategy_config(FIXTURE)
    assert config.with_setups(("breakout", "pullback")).enabled_setups == ("pullback", "breakout")
    assert config.with_setups(("breakout",)).enabled_setups == ("breakout",)
    assert config.sma_periods() == frozenset({20, 50, 200})
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/test_strategy_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.strategy'`.

- [ ] **Step 4: Implement**

Create an empty `src/signalbench/strategy/__init__.py` (0 bytes, like the other package markers).

`src/signalbench/strategy/config.py`:

```python
"""Pre-registered strategy parameters (spec 02), loaded strictly from YAML."""

import hashlib
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Literal, cast, get_args

import yaml

SetupName = Literal["pullback", "breakout", "sentiment"]
SETUP_NAMES: tuple[SetupName, ...] = get_args(SetupName)
COST_PER_SIDE_FLOOR = 0.002


@dataclass(frozen=True)
class PullbackParams:
    trend_sma_fast: int
    trend_sma_slow: int
    rsi_max: float
    stop_low_sessions: int
    stop_atr_mult: float
    target_r: float
    time_limit: int


@dataclass(frozen=True)
class BreakoutParams:
    trend_sma: int
    lookback: int
    volume_lookback: int
    volume_mult: float
    stop_atr_mult: float
    trail_atr_mult: float
    time_limit: int


@dataclass(frozen=True)
class SentimentParams:
    trend_sma: int
    stop_atr_mult: float
    target_r: float
    time_limit: int


@dataclass(frozen=True)
class BacktestParams:
    start: date
    h1_end: date
    h2_start: date
    recent_since: date
    min_trades: int
    min_mean_r: float


@dataclass(frozen=True)
class StrategyConfig:
    version: str
    start_equity: float
    risk_pct: float
    max_positions: int
    max_per_sector: int
    pause_drawdown: float
    auto_resume_sessions: int
    gap_up_limit: float
    cost_per_side: float
    setup_priority: tuple[SetupName, ...]
    enabled_setups: tuple[SetupName, ...]
    regime_symbol: str
    regime_sma: int
    atr_period: int
    rsi_period: int
    liquidity_min_traded_value: float
    liquidity_sessions: int
    earnings_blackout_sessions: int
    earnings_exit_sessions: int
    pullback: PullbackParams
    breakout: BreakoutParams
    sentiment: SentimentParams
    backtest: BacktestParams

    def with_setups(self, setups: tuple[SetupName, ...]) -> "StrategyConfig":
        """A copy that only fires `setups`, kept in `setup_priority` order."""
        unknown = set(setups) - set(self.setup_priority)
        if unknown:
            raise ValueError(f"Unknown setups: {sorted(unknown)}")
        ordered = tuple(name for name in self.setup_priority if name in setups)
        return replace(self, enabled_setups=ordered)

    def sma_periods(self) -> frozenset[int]:
        """Every SMA length any rule reads, so MarketView computes each one once."""
        return frozenset(
            {
                self.regime_sma,
                self.pullback.trend_sma_fast,
                self.pullback.trend_sma_slow,
                self.breakout.trend_sma,
                self.sentiment.trend_sma,
            }
        )


class ConfigError(ValueError):
    pass


class _Section:
    """One YAML mapping. Every key must be read exactly once; leftovers are an error."""

    def __init__(self, raw: object, where: str) -> None:
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: expected a mapping")
        self._raw = cast(dict[str, Any], raw)
        self._where = where
        self._used: set[str] = set()

    def _get(self, key: str) -> Any:
        if key not in self._raw:
            raise ConfigError(f"{self._where}: missing key {key!r}")
        self._used.add(key)
        return self._raw[key]

    def section(self, key: str) -> "_Section":
        return _Section(self._get(key), f"{self._where}.{key}")

    def integer(self, key: str, minimum: int = 1) -> int:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ConfigError(f"{self._where}.{key}: expected an integer >= {minimum}")
        return value

    def number(self, key: str, minimum: float = 0.0) -> float:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int | float) or value < minimum:
            raise ConfigError(f"{self._where}.{key}: expected a number >= {minimum}")
        return float(value)

    def text(self, key: str) -> str:
        value = self._get(key)
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{self._where}.{key}: expected a non-empty string")
        return value

    def day(self, key: str) -> date:
        value = self._get(key)
        if not isinstance(value, date):
            raise ConfigError(f"{self._where}.{key}: expected a YYYY-MM-DD date")
        return value

    def setups(self, key: str) -> tuple[SetupName, ...]:
        value = self._get(key)
        if not isinstance(value, list) or sorted(value) != sorted(SETUP_NAMES):
            raise ConfigError(f"{self._where}.{key}: expected each of {list(SETUP_NAMES)} once")
        return tuple(cast(list[SetupName], value))

    def done(self) -> None:
        extra = sorted(set(self._raw) - self._used)
        if extra:
            raise ConfigError(f"{self._where}: unknown keys {extra}")


def parse_strategy_config(raw: object, where: str = "config") -> StrategyConfig:
    top = _Section(raw, where)
    regime = top.section("regime")
    indicators = top.section("indicators")
    liquidity = top.section("liquidity")
    earnings = top.section("earnings")
    setups = top.section("setups")
    pull = setups.section("pullback")
    brk = setups.section("breakout")
    sent = setups.section("sentiment")
    back = top.section("backtest")
    bar = back.section("pass_bar")
    priority = top.setups("setup_priority")
    config = StrategyConfig(
        version=top.text("version"),
        start_equity=top.number("start_equity", minimum=1.0),
        risk_pct=top.number("risk_pct"),
        max_positions=top.integer("max_positions"),
        max_per_sector=top.integer("max_per_sector"),
        pause_drawdown=top.number("pause_drawdown"),
        auto_resume_sessions=top.integer("auto_resume_sessions"),
        gap_up_limit=top.number("gap_up_limit"),
        cost_per_side=top.number("cost_per_side", minimum=COST_PER_SIDE_FLOOR),
        setup_priority=priority,
        enabled_setups=priority,
        regime_symbol=regime.text("symbol"),
        regime_sma=regime.integer("sma"),
        atr_period=indicators.integer("atr_period"),
        rsi_period=indicators.integer("rsi_period"),
        liquidity_min_traded_value=liquidity.number("min_median_traded_value_usd"),
        liquidity_sessions=liquidity.integer("sessions"),
        earnings_blackout_sessions=earnings.integer("blackout_sessions"),
        earnings_exit_sessions=earnings.integer("exit_sessions"),
        pullback=PullbackParams(
            trend_sma_fast=pull.integer("trend_sma_fast"),
            trend_sma_slow=pull.integer("trend_sma_slow"),
            rsi_max=pull.number("rsi_max"),
            stop_low_sessions=pull.integer("stop_low_sessions"),
            stop_atr_mult=pull.number("stop_atr_mult"),
            target_r=pull.number("target_r"),
            time_limit=pull.integer("time_limit"),
        ),
        breakout=BreakoutParams(
            trend_sma=brk.integer("trend_sma"),
            lookback=brk.integer("lookback"),
            volume_lookback=brk.integer("volume_lookback"),
            volume_mult=brk.number("volume_mult"),
            stop_atr_mult=brk.number("stop_atr_mult"),
            trail_atr_mult=brk.number("trail_atr_mult"),
            time_limit=brk.integer("time_limit"),
        ),
        sentiment=SentimentParams(
            trend_sma=sent.integer("trend_sma"),
            stop_atr_mult=sent.number("stop_atr_mult"),
            target_r=sent.number("target_r"),
            time_limit=sent.integer("time_limit"),
        ),
        backtest=BacktestParams(
            start=back.day("start"),
            h1_end=back.day("h1_end"),
            h2_start=back.day("h2_start"),
            recent_since=back.day("recent_since"),
            min_trades=bar.integer("min_trades"),
            min_mean_r=bar.number("min_mean_r"),
        ),
    )
    for section in (top, regime, indicators, liquidity, earnings, setups, pull, brk, sent, back, bar):
        section.done()
    if config.backtest.h2_start <= config.backtest.h1_end:
        raise ConfigError(f"{where}.backtest: h2_start must be after h1_end")
    return config


def config_sha256(raw: bytes) -> str:
    """SHA-256 of the file with CRLF normalised to LF, so Windows and CI agree."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def load_strategy_config(path: Path) -> tuple[StrategyConfig, str]:
    raw = path.read_bytes()
    config = parse_strategy_config(yaml.safe_load(raw), where=path.name)
    return config, config_sha256(raw)
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_strategy_config.py -v && uv run mypy src && uv run ruff check .`
Expected: 7 passed; mypy and ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/strategy/__init__.py src/signalbench/strategy/config.py tests/fixtures/strategy_test.yaml tests/test_strategy_config.py
git commit -m "feat: strict strategy config loader with config sha256

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Indicators

**Files:**
- Create: `src/signalbench/strategy/indicators.py`, `tests/test_indicators.py`

- [ ] **Step 1: Write the failing test**

The expected values are worked by hand in the comments; see "Formulas" above.

`tests/test_indicators.py`:

```python
from pytest import approx

from signalbench.strategy.indicators import (
    prior_max,
    prior_mean,
    rolling_median,
    rolling_min,
    sma,
    true_range,
    wilder_atr,
    wilder_rsi,
)


def test_sma_by_hand() -> None:
    assert sma([1.0, 2.0, 3.0, 4.0, 5.0], 3) == [None, None, 2.0, 3.0, 4.0]
    assert sma([2.0, 4.0], 3) == [None, None]


def test_wilder_rsi2_by_hand() -> None:
    # changes: +1, -0.5, +1.5, -1
    # i=2 seed: gain (1 + 0)/2 = 0.5, loss (0 + 0.5)/2 = 0.25 -> RS 2 -> 66.667
    # i=3: gain (0.5 + 1.5)/2 = 1.0, loss (0.25 + 0)/2 = 0.125 -> RS 8 -> 88.889
    # i=4: gain (1.0 + 0)/2 = 0.5, loss (0.125 + 1)/2 = 0.5625 -> RS 0.8889 -> 47.059
    out = wilder_rsi([10.0, 11.0, 10.5, 12.0, 11.0], 2)
    assert out[:2] == [None, None]
    assert out[2] == approx(100 - 100 / 3)
    assert out[3] == approx(100 - 100 / 9)
    assert out[4] == approx(100 - 100 / (1 + 0.5 / 0.5625))


def test_wilder_rsi_edge_cases() -> None:
    assert wilder_rsi([1.0, 2.0, 3.0], 2)[2] == 100.0  # no losses
    assert wilder_rsi([5.0, 5.0, 5.0], 2)[2] == 50.0  # no movement
    assert wilder_rsi([1.0, 2.0], 2) == [None, None]


def test_true_range_uses_previous_close() -> None:
    highs = [11.0, 12.0, 10.0]
    lows = [9.0, 11.5, 8.0]
    closes = [10.0, 11.8, 9.0]
    # bar 1: max(0.5, |12 - 10|, |11.5 - 10|) = 2.0; bar 2: max(2, |10 - 11.8|, |8 - 11.8|) = 3.8
    assert true_range(highs, lows, closes) == [None, 2.0, approx(3.8)]


def test_wilder_atr_by_hand() -> None:
    # period 3. TRs for bars 1..5: 2, 1, 3, 2, 4 (ranges only; closes flat at 10).
    highs = [11.0, 11.0, 10.5, 11.5, 11.0, 12.0]
    lows = [9.0, 9.0, 9.5, 8.5, 9.0, 8.0]
    closes = [10.0] * 6
    out = wilder_atr(highs, lows, closes, 3)
    assert out[:3] == [None, None, None]
    assert out[3] == approx((2 + 1 + 3) / 3)  # seed = 2.0
    assert out[4] == approx((2.0 * 2 + 2) / 3)  # 2.0
    assert out[5] == approx((2.0 * 2 + 4) / 3)  # 2.6667


def test_wilder_atr14_on_constant_range() -> None:
    n = 20
    out = wilder_atr([11.0] * n, [9.0] * n, [10.0] * n, 14)
    assert out[13] is None
    assert out[14] == approx(2.0)
    assert out[19] == approx(2.0)


def test_prior_max_excludes_today() -> None:
    assert prior_max([1.0, 3.0, 2.0, 5.0, 4.0], 2) == [None, None, 3.0, 3.0, 5.0]


def test_prior_mean_excludes_today() -> None:
    assert prior_mean([1.0, 2.0, 3.0, 10.0], 2) == [None, None, 1.5, 2.5]


def test_rolling_min_and_median_include_today() -> None:
    assert rolling_min([5.0, 3.0, 4.0, 6.0], 3) == [None, None, 3.0, 3.0]
    assert rolling_median([5.0, 1.0, 3.0, 2.0], 3) == [None, None, 3.0, 2.0]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_indicators.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.strategy.indicators'`.

- [ ] **Step 3: Implement**

`src/signalbench/strategy/indicators.py`:

```python
"""Causal daily indicators on plain float lists.

Every function returns a list the same length as its input. Entry `i` uses only
inputs `0..i`; it is `None` until there is enough history, and `None` means "no signal".
"""

from collections.abc import Sequence
from statistics import median


def sma(values: Sequence[float], period: int) -> list[float | None]:
    """Mean of values[i - period + 1 .. i]; first defined at i = period - 1."""
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def wilder_rsi(closes: Sequence[float], period: int) -> list[float | None]:
    """Wilder RSI.

    change_i = close_i - close_(i-1); gain = max(change, 0); loss = max(-change, 0).
    Seed at i = period: avg_gain / avg_loss = simple mean of changes 1..period.
    After that: avg = (avg_prev * (period - 1) + current) / period.
    RSI = 100 - 100 / (1 + avg_gain / avg_loss); 100 when avg_loss is 0 (50 if both are 0).
    """
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = [0.0] + [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [0.0] + [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    for i in range(period, len(closes)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0.0:
            out[i] = 50.0 if avg_gain == 0.0 else 100.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def _ranges(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> list[float]:
    """True ranges for bars 1..n-1 (bar 0 has no previous close)."""
    return [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, len(closes))
    ]


def true_range(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
) -> list[float | None]:
    """max(high - low, |high - prev close|, |low - prev close|); None at i = 0 (no prev close)."""
    return [None, *_ranges(highs, lows, closes)] if closes else []


def wilder_atr(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int
) -> list[float | None]:
    """Wilder ATR: seed at i = period with the mean of TR_1..TR_period, then
    ATR_i = (ATR_(i-1) * (period - 1) + TR_i) / period."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    ranges = _ranges(highs, lows, closes)  # ranges[k] is TR of bar k + 1
    atr = sum(ranges[:period]) / period
    out[period] = atr
    for i in range(period + 1, len(closes)):
        atr = (atr * (period - 1) + ranges[i - 1]) / period
        out[i] = atr
    return out


def prior_max(values: Sequence[float], lookback: int) -> list[float | None]:
    """max(values[i - lookback .. i - 1]): the prior `lookback` sessions, excluding i."""
    return [
        max(values[i - lookback : i]) if i >= lookback else None for i in range(len(values))
    ]


def prior_mean(values: Sequence[float], lookback: int) -> list[float | None]:
    """mean(values[i - lookback .. i - 1]): the prior `lookback` sessions, excluding i."""
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i in range(len(values)):
        if i >= lookback:
            out[i] = total / lookback
        total += values[i]
        if i >= lookback:
            total -= values[i - lookback]
    return out


def rolling_min(values: Sequence[float], window: int) -> list[float | None]:
    """min(values[i - window + 1 .. i]), including i."""
    return [
        min(values[i - window + 1 : i + 1]) if i >= window - 1 else None
        for i in range(len(values))
    ]


def rolling_median(values: Sequence[float], window: int) -> list[float | None]:
    """median(values[i - window + 1 .. i]), including i."""
    return [
        float(median(values[i - window + 1 : i + 1])) if i >= window - 1 else None
        for i in range(len(values))
    ]
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_indicators.py -v && uv run mypy src && uv run ruff check .`
Expected: 9 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/strategy/indicators.py tests/test_indicators.py
git commit -m "feat: causal SMA, Wilder RSI and ATR, and rolling indicators

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Readings port, portfolio, and decision types

**Files:**
- Create: `src/signalbench/strategy/readings.py`, `src/signalbench/strategy/portfolio.py`, `src/signalbench/strategy/decision.py`, `tests/test_strategy_types.py`

These are the interfaces specs 03–05 consume: spec 03 implements `ReadingsView`, spec 04 builds `PortfolioState` from the ledger, spec 05 turns `Decision` into messages.

- [ ] **Step 1: Write the failing test**

`tests/test_strategy_types.py`:

```python
from datetime import date
from typing import get_args

from signalbench.strategy.decision import Decision, SkipReason
from signalbench.strategy.portfolio import PendingEntry, PortfolioState, Position
from signalbench.strategy.readings import NullReadingsView, ReadingsView


def _position(pid: str, symbol: str, sector: str) -> Position:
    return Position(
        id=pid,
        symbol=symbol,
        setup="pullback",
        sector=sector,
        units=1.0,
        entry_price=100.0,
        entry_date=date(2024, 1, 2),
        stop=95.0,
        target=110.0,
        time_limit=10,
        sessions_held=1,
        highest_close=100.0,
    )


def test_null_readings_never_block_tag_or_trigger() -> None:
    readings: ReadingsView = NullReadingsView()
    day = date(2024, 1, 2)
    assert readings.blocked("AAPL", day) is False
    assert readings.catalyst("AAPL", day) is False
    assert readings.sentiment_trigger("AAPL", day) is False


def test_portfolio_counts_positions_and_pending_entries() -> None:
    state = PortfolioState(
        cash=50.0,
        positions=(_position("P1", "AAPL", "Information Technology"),),
        pending=(PendingEntry("MSFT", "breakout", "Information Technology", 20.0),),
        equity=150.0,
        peak=150.0,
        paused=False,
        paused_since=None,
    )
    assert state.holds("AAPL") and state.holds("MSFT") and not state.holds("NVDA")
    assert state.slots_used() == 2
    assert state.sector_count("Information Technology") == 2
    assert state.sector_count("Health Care") == 0
    assert state.uncommitted_cash() == 30.0


def test_decision_defaults_to_empty_lists() -> None:
    assert Decision() == Decision(entries=[], exits=[], stop_updates=[], skips=[])


def test_skip_reasons_cover_the_spec_list() -> None:
    spec = {
        "gap_up",
        "gap_below_stop",
        "no_slot",
        "sector_cap",
        "blocked",
        "paused",
        "earnings_blackout",
        "regime",
    }
    assert spec <= set(get_args(SkipReason))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_strategy_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.strategy.decision'`.

- [ ] **Step 3: Implement**

`src/signalbench/strategy/readings.py`:

```python
"""The port through which Jev readings (spec 03) reach decide()."""

from datetime import date
from typing import Protocol


class ReadingsView(Protocol):
    def blocked(self, symbol: str, as_of: date) -> bool:
        """A negative document blocks entry (only when the Jev filter is on)."""
        ...

    def catalyst(self, symbol: str, as_of: date) -> bool:
        """A positive document in the last 10 sessions; ranks the candidate first."""
        ...

    def sentiment_trigger(self, symbol: str, as_of: date) -> bool:
        """A positive document in the last 3 sessions; feeds the Sentiment setup."""
        ...


class NullReadingsView:
    """Spec 02: no blocks, no catalysts, no sentiment triggers."""

    def blocked(self, symbol: str, as_of: date) -> bool:
        return False

    def catalyst(self, symbol: str, as_of: date) -> bool:
        return False

    def sentiment_trigger(self, symbol: str, as_of: date) -> bool:
        return False
```

`src/signalbench/strategy/portfolio.py`:

```python
"""What decide() knows about holdings. Built by the simulator here and by the ledger (spec 04)."""

from dataclasses import dataclass
from datetime import date

from signalbench.strategy.config import SetupName


@dataclass(frozen=True)
class Position:
    id: str
    symbol: str
    setup: SetupName
    sector: str
    units: float
    entry_price: float  # the fill, cost included
    entry_date: date
    stop: float
    target: float | None
    time_limit: int
    sessions_held: int  # the entry session counts as 1
    highest_close: float  # highest close since entry, entry session included


@dataclass(frozen=True)
class PendingEntry:
    """An accepted entry that has not filled yet. It holds a slot and its planned cash."""

    symbol: str
    setup: SetupName
    sector: str
    planned_cost: float


@dataclass(frozen=True)
class PortfolioState:
    cash: float
    positions: tuple[Position, ...]
    pending: tuple[PendingEntry, ...]
    equity: float  # cash + sum(units * close) at the as-of close
    peak: float
    paused: bool
    paused_since: date | None

    def holds(self, symbol: str) -> bool:
        return any(p.symbol == symbol for p in self.positions) or any(
            p.symbol == symbol for p in self.pending
        )

    def slots_used(self) -> int:
        return len(self.positions) + len(self.pending)

    def sector_count(self, sector: str) -> int:
        return sum(1 for p in self.positions if p.sector == sector) + sum(
            1 for p in self.pending if p.sector == sector
        )

    def uncommitted_cash(self) -> float:
        return self.cash - sum(p.planned_cost for p in self.pending)
```

`src/signalbench/strategy/decision.py`:

```python
"""The output of decide(): orders for the next open, stop updates, and skips."""

from dataclasses import dataclass, field
from typing import Literal

from signalbench.strategy.config import SetupName

ExitReason = Literal["stop", "earnings", "target", "time"]
SkipReason = Literal[
    "gap_up",
    "gap_below_stop",
    "no_slot",
    "sector_cap",
    "blocked",
    "paused",
    "earnings_blackout",
    "regime",
    "held",
    "no_cash",
    "no_bar",
]


@dataclass(frozen=True)
class EntryOrder:
    symbol: str
    setup: SetupName
    sector: str
    signal_close: float
    stop: float
    target_r: float | None  # None: no target (Breakout)
    time_limit: int
    units: float
    risk_amount: float  # units * (signal_close - stop), after the caps
    catalyst: bool
    rank: int  # 1 = first in the evening's ranking


@dataclass(frozen=True)
class ExitOrder:
    position_id: str
    symbol: str
    reason: ExitReason


@dataclass(frozen=True)
class StopUpdate:
    position_id: str
    old_stop: float
    new_stop: float


@dataclass(frozen=True)
class Skip:
    symbol: str
    setup: SetupName
    reason: SkipReason


@dataclass(frozen=True)
class Decision:
    entries: list[EntryOrder] = field(default_factory=list)
    exits: list[ExitOrder] = field(default_factory=list)
    stop_updates: list[StopUpdate] = field(default_factory=list)
    skips: list[Skip] = field(default_factory=list)
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_strategy_types.py -v && uv run mypy src && uv run ruff check .`
Expected: 4 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/strategy/readings.py src/signalbench/strategy/portfolio.py src/signalbench/strategy/decision.py tests/test_strategy_types.py
git commit -m "feat: readings port, portfolio state, and decision types

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Market view with look-ahead protection

**Files:**
- Modify: `src/signalbench/market/bars.py`, `tests/test_bars.py`
- Create: `src/signalbench/strategy/market_view.py`, `tests/strategy_helpers.py`, `tests/test_market_view.py`

- [ ] **Step 1: Write the shared test helpers**

`tests/strategy_helpers.py` (used by every later strategy and backtest test):

```python
"""Synthetic bars, sessions, and views shared by the strategy and backtest tests."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from signalbench.market.bars import AdjustedBar
from signalbench.strategy.config import SetupName, StrategyConfig, load_strategy_config
from signalbench.strategy.market_view import MarketView, Snapshot, SymbolInput
from signalbench.strategy.portfolio import PortfolioState, Position

FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "strategy_test.yaml"
VOLUME = 1_000_000


def load_test_config() -> StrategyConfig:
    return load_strategy_config(FIXTURE_CONFIG)[0]


def weekdays(start: date, count: int) -> list[date]:
    """`count` Monday-to-Friday dates from `start` (the fixture calendar; no holidays)."""
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def make_bar(
    day: date,
    close: float,
    *,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: int = VOLUME,
) -> AdjustedBar:
    """Defaults: open = close, high = close + 1, low = close - 1."""
    return AdjustedBar(
        date=day,
        open=close if open_ is None else open_,
        high=close + 1.0 if high is None else high,
        low=close - 1.0 if low is None else low,
        close=close,
        volume=volume,
        traded_value=close * volume,
    )


def trend_bars(days: Sequence[date], start: float, step: float) -> list[AdjustedBar]:
    """A straight line: close_i = start + step * i."""
    return [make_bar(day, start + step * i) for i, day in enumerate(days)]


def series(days: Sequence[date], closes: Sequence[float]) -> list[AdjustedBar]:
    return [make_bar(day, close) for day, close in zip(days, closes, strict=True)]


def pullback_closes(count: int, dip: int = 251) -> list[float]:
    """Uptrend 100 + 0.2 i, then -0.8 on dip - 1 and -3.0 on `dip` (RSI(2) about 2.9),
    then flat at the dip close. The pullback setup fires on `dip` and not before."""
    closes = [100.0 + 0.2 * i for i in range(dip - 1)]
    closes.append(closes[-1] - 0.8)
    closes.append(closes[-1] - 3.0)
    closes.extend([closes[-1]] * (count - len(closes)))
    return closes


def with_bar(bars: list[AdjustedBar], index: int, **changes: float) -> list[AdjustedBar]:
    """Copy of `bars` with bar `index` changed (keeps traded_value = close * volume)."""
    out = list(bars)
    bar = replace(out[index], **changes)
    out[index] = replace(bar, traded_value=bar.close * bar.volume)
    return out


def make_market(
    bars: Mapping[str, list[AdjustedBar]],
    benchmark: list[AdjustedBar],
    sessions: Sequence[date],
    config: StrategyConfig,
    *,
    sectors: Mapping[str, str] | None = None,
    earnings: Mapping[str, list[date]] | None = None,
) -> MarketView:
    sectors = sectors or {}
    earnings = earnings or {}
    return MarketView(
        [
            SymbolInput(
                symbol=symbol,
                sector=sectors.get(symbol, "Information Technology"),
                bars=symbol_bars,
                earnings=earnings.get(symbol, []),
            )
            for symbol, symbol_bars in bars.items()
        ],
        benchmark,
        sessions,
        config,
    )


def make_snapshot(**changes: object) -> Snapshot:
    """A snapshot where no setup fires; override fields to build a case."""
    base = Snapshot(
        date=date(2024, 1, 2),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=float(VOLUME),
        prev_high=101.0,
        sma={20: 100.0, 50: 100.0, 200: 100.0},
        rsi=50.0,
        atr=2.0,
        prior_max_close=100.0,
        prior_mean_volume=float(VOLUME),
        min_low=99.0,
        median_traded_value=100_000_000.0,
    )
    return replace(base, **changes)


def make_position(
    position_id: str,
    symbol: str,
    *,
    setup: SetupName = "pullback",
    sector: str = "Information Technology",
    units: float = 0.1,
    entry_price: float = 100.0,
    entry_date: date = date(2023, 1, 2),
    stop: float = 90.0,
    target: float | None = 200.0,
    time_limit: int = 10,
    sessions_held: int = 1,
    highest_close: float = 100.0,
) -> Position:
    return Position(
        id=position_id,
        symbol=symbol,
        setup=setup,
        sector=sector,
        units=units,
        entry_price=entry_price,
        entry_date=entry_date,
        stop=stop,
        target=target,
        time_limit=time_limit,
        sessions_held=sessions_held,
        highest_close=highest_close,
    )


def empty_portfolio(cash: float) -> PortfolioState:
    return PortfolioState(
        cash=cash,
        positions=(),
        pending=(),
        equity=cash,
        peak=cash,
        paused=False,
        paused_since=None,
    )


class WeekdaySessions:
    """A `Sessions` calendar where every Monday-to-Friday is a session."""

    def sessions_between(self, start: date, end: date) -> list[date]:
        days: list[date] = []
        day = start
        while day <= end:
            if day.weekday() < 5:
                days.append(day)
            day += timedelta(days=1)
        return days

    def next_sessions(self, day: date, count: int) -> list[date]:
        return weekdays(day + timedelta(days=1), count)

    def is_session(self, day: date) -> bool:
        return day.weekday() < 5
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_bars.py`:

```python
def test_traded_value_uses_the_raw_close(session: Session) -> None:
    ticker = Ticker(symbol="MSFT", company_name="Microsoft")
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    session.add(
        Price(
            ticker_id=ticker.id,
            date=date(2024, 1, 2),
            open=Decimal("100.0000"),
            high=Decimal("101.0000"),
            low=Decimal("99.0000"),
            close=Decimal("100.0000"),
            adj_close=Decimal("50.0000"),
            volume=3_000,
        )
    )
    session.commit()
    [bar] = adjusted_bars(session, ticker.id)
    assert bar.close == 50.0
    assert bar.traded_value == 300_000.0
```

`tests/test_market_view.py`:

```python
from datetime import date

import pytest
from pytest import approx

from signalbench.strategy.indicators import sma, wilder_atr
from signalbench.strategy.market_view import LookAheadError, MarketView
from strategy_helpers import (
    load_test_config,
    make_bar,
    make_market,
    trend_bars,
    weekdays,
)

DAYS = weekdays(date(2023, 1, 2), 260)
CONFIG = load_test_config()


def _market(**kwargs: object) -> MarketView:
    bars = {"AAA": trend_bars(DAYS, 100.0, 0.2)}
    return make_market(bars, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG, **kwargs)


def test_snapshot_is_the_as_of_bar_with_its_indicators() -> None:
    view = _market().at(DAYS[250])
    snap = view.snapshot("AAA")
    assert snap is not None
    assert snap.date == DAYS[250]
    assert snap.close == approx(150.0)
    closes = [100.0 + 0.2 * i for i in range(260)]
    assert snap.sma[50] == approx(sma(closes, 50)[250])
    assert snap.sma[200] == approx(sma(closes, 200)[250])
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    assert snap.atr == approx(wilder_atr(highs, lows, closes, 14)[250])
    assert snap.prior_max_close == approx(closes[249])
    assert snap.prior_mean_volume == approx(1_000_000.0)
    assert snap.min_low == approx(closes[248] - 1)
    assert snap.prev_high == approx(closes[249] + 1)


def test_future_access_raises() -> None:
    view = _market().at(DAYS[100])
    with pytest.raises(LookAheadError):
        view.snapshot("AAA", DAYS[101])
    with pytest.raises(LookAheadError):
        view.benchmark(DAYS[101])
    assert view.snapshot("AAA", DAYS[99]) is not None


def test_missing_bar_returns_the_latest_earlier_bar() -> None:
    bars = trend_bars(DAYS, 100.0, 0.2)
    del bars[120]
    market = make_market({"AAA": bars}, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG)
    snap = market.at(DAYS[120]).snapshot("AAA")
    assert snap is not None and snap.date == DAYS[119]
    assert market.at(DAYS[120]).is_active("AAA") is False  # no bar today


def test_regime_needs_200_sessions_and_close_above_the_average() -> None:
    market = _market()
    assert market.at(DAYS[198]).regime_on() is False  # SMA200 undefined
    assert market.at(DAYS[199]).regime_on() is True
    falling = trend_bars(DAYS, 300.0, -0.5)
    down = make_market({"AAA": trend_bars(DAYS, 100.0, 0.2)}, falling, DAYS, CONFIG)
    assert down.at(DAYS[250]).regime_on() is False


def test_liquidity_is_active_at_exactly_the_threshold() -> None:
    at_threshold = [make_bar(day, 100.0, volume=500_000) for day in DAYS[:30]]
    below = [make_bar(day, 100.0, volume=499_999) for day in DAYS[:30]]
    market = make_market(
        {"AT": at_threshold, "BELOW": below}, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG
    )
    view = market.at(DAYS[25])
    assert view.is_active("AT") is True  # median 100 * 500,000 = 50,000,000
    assert view.is_active("BELOW") is False
    assert market.at(DAYS[18]).is_active("AT") is False  # fewer than 20 sessions


def test_earnings_within_counts_sessions_after_as_of() -> None:
    # DAYS[10] is a Monday; the next three sessions are Tue, Wed, Thu.
    market = _market(earnings={"AAA": [DAYS[10], DAYS[13]]})
    view = market.at(DAYS[10])
    assert view.earnings_within("AAA", 3) is True  # DAYS[13] is D+3
    assert view.earnings_within("AAA", 2) is False  # an event on as_of itself does not count
    assert market.at(DAYS[13]).earnings_within("AAA", 3) is False


def test_earnings_on_a_non_session_day_counts_inside_the_window() -> None:
    friday = DAYS[4]
    saturday = date(2023, 1, 7)
    view = _market(earnings={"AAA": [saturday]}).at(friday)
    assert view.earnings_within("AAA", 1) is True  # Sat falls before Monday (D+1)


def test_earnings_window_past_the_session_list_is_an_error() -> None:
    view = _market().at(DAYS[-2])
    with pytest.raises(ValueError, match="session list ends"):
        view.earnings_within("AAA", 3)


def test_bars_out_of_order_are_rejected() -> None:
    bars = trend_bars(DAYS[:5], 100.0, 1.0)
    with pytest.raises(ValueError, match="increasing"):
        make_market({"AAA": bars[::-1]}, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG)
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_bars.py tests/test_market_view.py -v`
Expected: FAIL. Collection stops with `ModuleNotFoundError: No module named 'signalbench.strategy.market_view'` (the helpers import it). Run alone, `uv run pytest tests/test_bars.py -v` fails with `AttributeError: 'AdjustedBar' object has no attribute 'traded_value'`.

- [ ] **Step 4: Add `traded_value` to `AdjustedBar`**

In `src/signalbench/market/bars.py`, add a field after `volume: int` in `AdjustedBar`:

```python
    traded_value: float  # raw close x volume, US$ (the liquidity measure in spec 01)
```

and in `adjust()`, add the matching argument after `volume=row.volume,`:

```python
        traded_value=float(row.close) * row.volume,
```

- [ ] **Step 5: Write the market view**

`src/signalbench/strategy/market_view.py`:

```python
"""Bars, indicators, earnings dates and sectors, with look-ahead protection.

`MarketView` computes every indicator once for the full history. `MarketView.at(as_of)`
returns an `AsOfView`, which is the only thing `decide()` reads. It never returns a bar
dated after `as_of` and raises `LookAheadError` when asked for one.
"""

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from signalbench.market.bars import AdjustedBar
from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.indicators import (
    prior_max,
    prior_mean,
    rolling_median,
    rolling_min,
    sma,
    wilder_atr,
    wilder_rsi,
)


class LookAheadError(Exception):
    """Raised when strategy code asks for data dated after its as-of session."""


@dataclass(frozen=True)
class SymbolInput:
    symbol: str
    sector: str
    bars: Sequence[AdjustedBar]
    earnings: Sequence[date]


@dataclass(frozen=True)
class Snapshot:
    """One symbol at one session close: the bar plus every indicator the rules read."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    prev_high: float | None
    sma: Mapping[int, float | None]
    rsi: float | None
    atr: float | None
    prior_max_close: float | None
    prior_mean_volume: float | None
    min_low: float | None
    median_traded_value: float | None


class _Series:
    def __init__(self, bars: Sequence[AdjustedBar], config: StrategyConfig) -> None:
        self.bars = list(bars)
        if any(a.date >= b.date for a, b in zip(self.bars, self.bars[1:], strict=False)):
            raise ValueError("bars must be in strictly increasing date order")
        self.dates = [bar.date for bar in self.bars]
        highs = [bar.high for bar in self.bars]
        lows = [bar.low for bar in self.bars]
        closes = [bar.close for bar in self.bars]
        volumes = [float(bar.volume) for bar in self.bars]
        self.sma = {period: sma(closes, period) for period in sorted(config.sma_periods())}
        self.rsi = wilder_rsi(closes, config.rsi_period)
        self.atr = wilder_atr(highs, lows, closes, config.atr_period)
        self.prior_max_close = prior_max(closes, config.breakout.lookback)
        self.prior_mean_volume = prior_mean(volumes, config.breakout.volume_lookback)
        self.min_low = rolling_min(lows, config.pullback.stop_low_sessions)
        self.median_traded_value = rolling_median(
            [bar.traded_value for bar in self.bars], config.liquidity_sessions
        )

    def index_on_or_before(self, day: date) -> int | None:
        index = bisect_right(self.dates, day) - 1
        return index if index >= 0 else None

    def snapshot(self, i: int) -> Snapshot:
        bar = self.bars[i]
        return Snapshot(
            date=bar.date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=float(bar.volume),
            prev_high=self.bars[i - 1].high if i > 0 else None,
            sma={period: values[i] for period, values in self.sma.items()},
            rsi=self.rsi[i],
            atr=self.atr[i],
            prior_max_close=self.prior_max_close[i],
            prior_mean_volume=self.prior_mean_volume[i],
            min_low=self.min_low[i],
            median_traded_value=self.median_traded_value[i],
        )


class MarketView:
    def __init__(
        self,
        symbols: Sequence[SymbolInput],
        benchmark: Sequence[AdjustedBar],
        sessions: Sequence[date],
        config: StrategyConfig,
    ) -> None:
        self.config = config
        self.sessions: tuple[date, ...] = tuple(sessions)
        if list(self.sessions) != sorted(set(self.sessions)):
            raise ValueError("sessions must be unique and sorted")
        self._series = {item.symbol: _Series(item.bars, config) for item in symbols}
        self._sectors = {item.symbol: item.sector for item in symbols}
        self._earnings = {item.symbol: sorted(set(item.earnings)) for item in symbols}
        self._benchmark = _Series(benchmark, config)
        self.symbols: tuple[str, ...] = tuple(sorted(self._series))

    def at(self, as_of: date) -> "AsOfView":
        return AsOfView(self, as_of)

    def sessions_between(self, start: date, end: date) -> list[date]:
        return [day for day in self.sessions if start <= day <= end]


class AsOfView:
    """Everything `decide()` may know at the close of `as_of`."""

    def __init__(self, market: MarketView, as_of: date) -> None:
        self._market = market
        self.as_of = as_of
        self.symbols = market.symbols

    def _check(self, day: date) -> None:
        if day > self.as_of:
            raise LookAheadError(f"asked for {day} at the close of {self.as_of}")

    def sector(self, symbol: str) -> str:
        return self._market._sectors[symbol]

    def snapshot(self, symbol: str, day: date | None = None) -> Snapshot | None:
        """The latest bar dated on or before `day` (default: as_of), or None."""
        day = self.as_of if day is None else day
        self._check(day)
        series = self._market._series[symbol]
        index = series.index_on_or_before(day)
        return None if index is None else series.snapshot(index)

    def benchmark(self, day: date | None = None) -> Snapshot | None:
        day = self.as_of if day is None else day
        self._check(day)
        index = self._market._benchmark.index_on_or_before(day)
        return None if index is None else self._market._benchmark.snapshot(index)

    def regime_on(self) -> bool:
        """Benchmark close > its regime SMA at as_of. No data means no entries."""
        snap = self.benchmark()
        if snap is None or snap.date != self.as_of:
            return False
        average = snap.sma[self._market.config.regime_sma]
        return average is not None and snap.close > average

    def is_active(self, symbol: str) -> bool:
        """Backtest liquidity: traded a bar today and its 20-session median value clears the bar."""
        snap = self.snapshot(symbol)
        if snap is None or snap.date != self.as_of or snap.median_traded_value is None:
            return False
        return snap.median_traded_value >= self._market.config.liquidity_min_traded_value

    def earnings_within(self, symbol: str, sessions_ahead: int) -> bool:
        """Whether a known earnings date falls after as_of and on or before the Nth next session.

        The session calendar is known in advance, so reading future sessions is not look-ahead.
        In the backtest, realized SEC 2.02 dates stand in for dates announced in advance.
        """
        sessions = self._market.sessions
        first_after = bisect_right(sessions, self.as_of)
        last = first_after + sessions_ahead - 1
        if last >= len(sessions):
            raise ValueError(
                f"session list ends before {sessions_ahead} sessions after {self.as_of}"
            )
        horizon = sessions[last]
        dates = self._market._earnings.get(symbol, [])
        index = bisect_right(dates, self.as_of)  # first date strictly after as_of
        return index < len(dates) and dates[index] <= horizon
```

- [ ] **Step 6: Run the tests and checks**

Run: `uv run pytest tests/test_bars.py tests/test_market_view.py -v && uv run mypy src && uv run ruff check .`
Expected: 2 + 9 passed; mypy and ruff clean.

- [ ] **Step 7: Commit**

```bash
git add src/signalbench/market/bars.py src/signalbench/strategy/market_view.py tests/test_bars.py tests/strategy_helpers.py tests/test_market_view.py
git commit -m "feat: market view with precomputed indicators and look-ahead errors

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Setup rules

**Files:**
- Create: `src/signalbench/strategy/setups.py`, `tests/test_setups.py`

- [ ] **Step 1: Write the failing test**

`tests/test_setups.py`:

```python
from pytest import approx

from signalbench.strategy.setups import breakout, first_signal, pullback, sentiment
from strategy_helpers import load_test_config, make_snapshot

CONFIG = load_test_config()
UPTREND = {20: 98.0, 50: 95.0, 200: 90.0}


def test_pullback_fires_with_stop_below_the_three_session_low() -> None:
    snap = make_snapshot(close=100.0, sma=UPTREND, rsi=5.0, min_low=97.0, atr=2.0)
    signal = pullback(snap, CONFIG)
    assert signal is not None
    assert signal.setup == "pullback"
    assert signal.stop == approx(97.0 - 0.5 * 2.0)
    assert (signal.target_r, signal.time_limit) == (2.0, 10)


def test_pullback_needs_rsi_strictly_below_10() -> None:
    assert pullback(make_snapshot(sma=UPTREND, rsi=10.0), CONFIG) is None
    assert pullback(make_snapshot(sma=UPTREND, rsi=9.99), CONFIG) is not None


def test_pullback_needs_close_above_sma50_above_sma200() -> None:
    assert pullback(make_snapshot(sma={20: 1.0, 50: 95.0, 200: 96.0}, rsi=5.0), CONFIG) is None
    assert pullback(make_snapshot(sma={20: 1.0, 50: 100.0, 200: 90.0}, rsi=5.0), CONFIG) is None
    assert pullback(make_snapshot(sma={20: 1.0, 50: None, 200: 90.0}, rsi=5.0), CONFIG) is None


def test_breakout_fires_on_new_high_with_volume() -> None:
    snap = make_snapshot(
        close=105.0, sma=UPTREND, prior_max_close=104.0, volume=1_500_000.0,
        prior_mean_volume=1_000_000.0, atr=2.5,
    )
    signal = breakout(snap, CONFIG)
    assert signal is not None
    assert signal.stop == approx(105.0 - 2 * 2.5)
    assert (signal.target_r, signal.time_limit) == (None, 30)


def test_breakout_rejects_equal_high_or_thin_volume() -> None:
    base = {"close": 105.0, "sma": UPTREND, "prior_mean_volume": 1_000_000.0}
    assert breakout(make_snapshot(**base, prior_max_close=105.0, volume=2e6), CONFIG) is None
    assert breakout(make_snapshot(**base, prior_max_close=104.0, volume=1_499_999.0), CONFIG) is None


def test_sentiment_needs_a_trigger_trend_and_close_above_prior_high() -> None:
    snap = make_snapshot(close=100.0, sma=UPTREND, prev_high=99.5, atr=2.0)
    assert sentiment(snap, CONFIG, triggered=False) is None
    signal = sentiment(snap, CONFIG, triggered=True)
    assert signal is not None
    assert signal.stop == approx(96.0) and signal.target_r == 2.0 and signal.time_limit == 10
    assert sentiment(make_snapshot(close=100.0, sma=UPTREND, prev_high=100.0), CONFIG, True) is None


def test_first_signal_keeps_setup_priority_and_enabled_setups() -> None:
    both = make_snapshot(
        close=105.0, sma=UPTREND, rsi=5.0, min_low=103.0, prior_max_close=104.0,
        volume=2_000_000.0, prior_mean_volume=1_000_000.0,
    )
    first = first_signal(both, CONFIG, sentiment_triggered=False)
    assert first is not None and first.setup == "pullback"
    only_breakout = CONFIG.with_setups(("breakout",))
    second = first_signal(both, only_breakout, sentiment_triggered=False)
    assert second is not None and second.setup == "breakout"
    assert first_signal(make_snapshot(), CONFIG, sentiment_triggered=False) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_setups.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.strategy.setups'`.

- [ ] **Step 3: Implement**

`src/signalbench/strategy/setups.py`:

```python
"""The three setup rules (spec 02 table), each a pure function of one Snapshot."""

from dataclasses import dataclass

from signalbench.strategy.config import SetupName, StrategyConfig
from signalbench.strategy.market_view import Snapshot


@dataclass(frozen=True)
class Signal:
    setup: SetupName
    close: float
    stop: float
    target_r: float | None
    time_limit: int


def pullback(snap: Snapshot, config: StrategyConfig) -> Signal | None:
    """close > SMA50 > SMA200 and RSI(2) < 10; stop = min(low, 3 sessions) - 0.5 ATR."""
    p = config.pullback
    fast = snap.sma.get(p.trend_sma_fast)
    slow = snap.sma.get(p.trend_sma_slow)
    if fast is None or slow is None or snap.rsi is None or snap.min_low is None:
        return None
    if snap.atr is None or not (snap.close > fast > slow) or snap.rsi >= p.rsi_max:
        return None
    stop = snap.min_low - p.stop_atr_mult * snap.atr
    return Signal("pullback", snap.close, stop, p.target_r, p.time_limit)


def breakout(snap: Snapshot, config: StrategyConfig) -> Signal | None:
    """close > SMA50, close > prior 20-session max close, volume >= 1.5 x prior 50-session mean."""
    b = config.breakout
    trend = snap.sma.get(b.trend_sma)
    if trend is None or snap.prior_max_close is None or snap.prior_mean_volume is None:
        return None
    if snap.atr is None or snap.close <= trend or snap.close <= snap.prior_max_close:
        return None
    if snap.volume < b.volume_mult * snap.prior_mean_volume:
        return None
    return Signal("breakout", snap.close, snap.close - b.stop_atr_mult * snap.atr, None, b.time_limit)


def sentiment(snap: Snapshot, config: StrategyConfig, triggered: bool) -> Signal | None:
    """A positive reading in the last 3 sessions (spec 03), close > SMA20 and > prior high."""
    s = config.sentiment
    trend = snap.sma.get(s.trend_sma)
    if not triggered or trend is None or snap.prev_high is None or snap.atr is None:
        return None
    if snap.close <= trend or snap.close <= snap.prev_high:
        return None
    return Signal(
        "sentiment", snap.close, snap.close - s.stop_atr_mult * snap.atr, s.target_r, s.time_limit
    )


def first_signal(
    snap: Snapshot, config: StrategyConfig, sentiment_triggered: bool
) -> Signal | None:
    """The first enabled setup, in `setup_priority` order, that fires with a stop below close."""
    for name in config.setup_priority:
        if name not in config.enabled_setups:
            continue
        if name == "pullback":
            signal = pullback(snap, config)
        elif name == "breakout":
            signal = breakout(snap, config)
        else:
            signal = sentiment(snap, config, sentiment_triggered)
        if signal is not None and signal.stop < signal.close:
            return signal
    return None
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_setups.py -v && uv run mypy src && uv run ruff check .`
Expected: 7 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/strategy/setups.py tests/test_setups.py
git commit -m "feat: pullback, breakout, and sentiment setup rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Exits and the trailing stop

**Files:**
- Create: `src/signalbench/strategy/exits.py`, `tests/test_exits.py`

- [ ] **Step 1: Write the failing test**

`tests/test_exits.py`:

```python
from dataclasses import replace
from datetime import date

from signalbench.strategy.exits import exit_reason, trailed_stop
from signalbench.strategy.portfolio import Position
from strategy_helpers import load_test_config, make_snapshot

CONFIG = load_test_config()
POSITION = Position(
    id="P00001",
    symbol="AAA",
    setup="pullback",
    sector="Information Technology",
    units=1.0,
    entry_price=100.0,
    entry_date=date(2024, 1, 2),
    stop=95.0,
    target=110.0,
    time_limit=10,
    sessions_held=3,
    highest_close=104.0,
)


def test_no_exit_inside_the_band() -> None:
    assert exit_reason(POSITION, make_snapshot(close=100.0), earnings_soon=False) is None


def test_stop_on_close_at_or_below_stop() -> None:
    assert exit_reason(POSITION, make_snapshot(close=95.0), earnings_soon=False) == "stop"


def test_target_on_close_at_or_above_target() -> None:
    assert exit_reason(POSITION, make_snapshot(close=110.0), earnings_soon=False) == "target"
    no_target = replace(POSITION, target=None)
    assert exit_reason(no_target, make_snapshot(close=500.0), earnings_soon=False) is None


def test_time_when_sessions_held_reaches_the_limit() -> None:
    assert exit_reason(replace(POSITION, sessions_held=9), make_snapshot(), False) is None
    assert exit_reason(replace(POSITION, sessions_held=10), make_snapshot(), False) == "time"


def test_order_is_stop_then_earnings_then_target_then_time() -> None:
    late = replace(POSITION, sessions_held=10)
    assert exit_reason(late, make_snapshot(close=94.0), earnings_soon=True) == "stop"
    assert exit_reason(late, make_snapshot(close=111.0), earnings_soon=True) == "earnings"
    assert exit_reason(late, make_snapshot(close=111.0), earnings_soon=False) == "target"


def test_trailing_stop_ratchets_up_only() -> None:
    held = replace(POSITION, setup="breakout", target=None, stop=95.0, highest_close=110.0)
    assert trailed_stop(held, make_snapshot(atr=2.0), CONFIG) == 104.0  # 110 - 3 * 2
    assert trailed_stop(held, make_snapshot(atr=6.0), CONFIG) is None  # 92 would loosen it
    assert trailed_stop(replace(held, stop=104.0), make_snapshot(atr=2.0), CONFIG) is None


def test_only_breakout_trails() -> None:
    assert trailed_stop(replace(POSITION, highest_close=200.0), make_snapshot(), CONFIG) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_exits.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.strategy.exits'`.

- [ ] **Step 3: Implement**

`src/signalbench/strategy/exits.py`:

```python
"""Exit checks and the Breakout trailing stop, evaluated at each close."""

from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.decision import ExitReason
from signalbench.strategy.market_view import Snapshot
from signalbench.strategy.portfolio import Position


def exit_reason(position: Position, snap: Snapshot, earnings_soon: bool) -> ExitReason | None:
    """First match in spec order: stop, earnings, target, time. It fills at the next open.

    `earnings_soon` is whether an earnings event falls on D+1 or D+2.
    """
    if snap.close <= position.stop:
        return "stop"
    if earnings_soon:
        return "earnings"
    if position.target is not None and snap.close >= position.target:
        return "target"
    if position.sessions_held >= position.time_limit:
        return "time"
    return None


def trailed_stop(position: Position, snap: Snapshot, config: StrategyConfig) -> float | None:
    """Breakout only: max(stop, highest close since entry - 3 x ATR today).

    Returns the new stop when it moves up, otherwise None. The stop never loosens.
    """
    if position.setup != "breakout" or snap.atr is None:
        return None
    candidate = position.highest_close - config.breakout.trail_atr_mult * snap.atr
    return candidate if candidate > position.stop else None
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_exits.py -v && uv run mypy src && uv run ruff check .`
Expected: 7 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/strategy/exits.py tests/test_exits.py
git commit -m "feat: exit order checks and the ratcheting breakout stop

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Gates, ranking, slots, and sizing

**Files:**
- Create: `src/signalbench/strategy/entries.py`, `tests/test_entries.py`

- [ ] **Step 1: Write the failing test**

`tests/test_entries.py`:

```python
from datetime import date

from pytest import approx

from signalbench.strategy.decision import Skip
from signalbench.strategy.entries import Candidate, Gates, allocate, gate_reason, size
from signalbench.strategy.portfolio import PendingEntry, PortfolioState, Position
from signalbench.strategy.setups import Signal
from strategy_helpers import load_test_config

CONFIG = load_test_config()
TECH = "Information Technology"
OPEN = Gates(held=False, regime_on=True, paused=False, earnings_soon=False, blocked=False)


def _state(cash: float = 100.0, equity: float = 100.0, **kwargs: object) -> PortfolioState:
    fields: dict[str, object] = {
        "cash": cash,
        "positions": (),
        "pending": (),
        "equity": equity,
        "peak": equity,
        "paused": False,
        "paused_since": None,
    }
    fields.update(kwargs)
    return PortfolioState(**fields)


def _candidate(symbol: str, sector: str = TECH, value: float = 1e8, catalyst: bool = False) -> Candidate:
    signal = Signal("pullback", close=100.0, stop=90.0, target_r=2.0, time_limit=10)
    return Candidate(symbol, sector, signal, median_traded_value=value, catalyst=catalyst)


def _held(symbol: str, sector: str) -> Position:
    return Position(
        id=f"P-{symbol}", symbol=symbol, setup="pullback", sector=sector, units=0.1,
        entry_price=100.0, entry_date=date(2024, 1, 2), stop=90.0, target=120.0,
        time_limit=10, sessions_held=1, highest_close=100.0,
    )


def test_each_gate_in_order() -> None:
    assert gate_reason(OPEN) is None
    assert gate_reason(Gates(True, False, True, True, True)) == "held"
    assert gate_reason(Gates(False, False, True, True, True)) == "regime"
    assert gate_reason(Gates(False, True, True, True, True)) == "paused"
    assert gate_reason(Gates(False, True, False, True, True)) == "earnings_blackout"
    assert gate_reason(Gates(False, True, False, False, True)) == "blocked"


def test_size_is_two_percent_risk() -> None:
    # 0.02 * 100 / (100 - 90) = 0.2 units, worth 20 (< 100 / 3)
    assert size(100.0, 90.0, equity=100.0, uncommitted_cash=100.0, config=CONFIG) == approx(0.2)


def test_size_caps_at_a_third_of_equity() -> None:
    # risk alone gives 2 units (worth 200); the cap is 100 / 3 / 100 units
    assert size(100.0, 99.0, equity=100.0, uncommitted_cash=100.0, config=CONFIG) == approx(1 / 3)


def test_size_caps_at_uncommitted_cash() -> None:
    assert size(100.0, 90.0, equity=100.0, uncommitted_cash=10.0, config=CONFIG) == approx(0.1)
    assert size(100.0, 90.0, equity=100.0, uncommitted_cash=-5.0, config=CONFIG) == 0.0


def test_ranking_catalyst_then_traded_value_then_symbol() -> None:
    candidates = [
        _candidate("CCC", "Energy", value=5e8),
        _candidate("BBB", "Health Care", value=1e8),
        _candidate("AAA", "Financials", value=1e8),
        _candidate("ZZZ", "Utilities", value=1e7, catalyst=True),
    ]
    entries, skips = allocate(candidates, _state(), CONFIG)
    assert [(e.symbol, e.rank) for e in entries] == [("ZZZ", 1), ("CCC", 2), ("AAA", 3)]
    assert skips == [Skip("BBB", "pullback", "no_slot")]


def test_sector_cap_counts_held_positions() -> None:
    state = _state(positions=(_held("HELD", TECH),))
    entries, skips = allocate([_candidate("AAA"), _candidate("BBB")], state, CONFIG)
    assert [e.symbol for e in entries] == ["AAA"]
    assert skips == [Skip("BBB", "pullback", "sector_cap")]


def test_pending_entries_take_slots_and_cash() -> None:
    pending = (
        PendingEntry("P1", "pullback", "Energy", planned_cost=30.0),
        PendingEntry("P2", "pullback", "Utilities", planned_cost=60.0),
    )
    state = _state(pending=pending)
    entries, skips = allocate([_candidate("AAA"), _candidate("BBB")], state, CONFIG)
    assert [e.symbol for e in entries] == ["AAA"]
    assert entries[0].units == approx(0.1)  # 100 - 90 committed = 10 cash left
    assert entries[0].risk_amount == approx(0.1 * 10.0)
    assert skips == [Skip("BBB", "pullback", "no_slot")]


def test_accepted_entries_reduce_cash_for_the_next() -> None:
    entries, _ = allocate(
        [_candidate("AAA", "Energy"), _candidate("BBB", "Utilities")], _state(cash=25.0), CONFIG
    )
    assert [e.units for e in entries] == [approx(0.2), approx(0.05)]  # 25 - 20 = 5 left


def test_no_cash_is_a_skip() -> None:
    entries, skips = allocate([_candidate("AAA")], _state(cash=0.0), CONFIG)
    assert entries == []
    assert skips == [Skip("AAA", "pullback", "no_cash")]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_entries.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.strategy.entries'`.

- [ ] **Step 3: Implement**

`src/signalbench/strategy/entries.py`:

```python
"""From signals to entry orders: gates, ranking, slots, sector cap, and sizing."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.decision import EntryOrder, Skip, SkipReason
from signalbench.strategy.portfolio import PortfolioState
from signalbench.strategy.setups import Signal


@dataclass(frozen=True)
class Candidate:
    symbol: str
    sector: str
    signal: Signal
    median_traded_value: float
    catalyst: bool


@dataclass(frozen=True)
class Gates:
    """Facts that can stop a signal before ranking, checked in this field order."""

    held: bool
    regime_on: bool
    paused: bool
    earnings_soon: bool  # an earnings event on D+1..D+3
    blocked: bool


def gate_reason(gates: Gates) -> SkipReason | None:
    if gates.held:
        return "held"
    if not gates.regime_on:
        return "regime"
    if gates.paused:
        return "paused"
    if gates.earnings_soon:
        return "earnings_blackout"
    if gates.blocked:
        return "blocked"
    return None


def size(
    signal_close: float,
    stop: float,
    equity: float,
    uncommitted_cash: float,
    config: StrategyConfig,
) -> float:
    """Risk-based units, capped at equity / max_positions and at uncommitted cash (by value)."""
    risk_units = config.risk_pct * equity / (signal_close - stop)
    value_cap = equity / config.max_positions / signal_close
    cash_cap = max(uncommitted_cash, 0.0) / signal_close
    return min(risk_units, value_cap, cash_cap)


def allocate(
    candidates: Sequence[Candidate],
    portfolio: PortfolioState,
    config: StrategyConfig,
) -> tuple[list[EntryOrder], list[Skip]]:
    """Rank (catalyst first, higher median traded value, symbol A-Z) and fill free slots."""
    ranked = sorted(candidates, key=lambda c: (not c.catalyst, -c.median_traded_value, c.symbol))
    free = config.max_positions - portfolio.slots_used()
    sectors: Counter[str] = Counter()
    for position in portfolio.positions:
        sectors[position.sector] += 1
    for pending in portfolio.pending:
        sectors[pending.sector] += 1
    cash = portfolio.uncommitted_cash()
    entries: list[EntryOrder] = []
    skips: list[Skip] = []
    for rank, candidate in enumerate(ranked, start=1):
        signal = candidate.signal
        if free <= 0:
            skips.append(Skip(candidate.symbol, signal.setup, "no_slot"))
            continue
        if sectors[candidate.sector] >= config.max_per_sector:
            skips.append(Skip(candidate.symbol, signal.setup, "sector_cap"))
            continue
        units = size(signal.close, signal.stop, portfolio.equity, cash, config)
        if units <= 0.0:
            skips.append(Skip(candidate.symbol, signal.setup, "no_cash"))
            continue
        entries.append(
            EntryOrder(
                symbol=candidate.symbol,
                setup=signal.setup,
                sector=candidate.sector,
                signal_close=signal.close,
                stop=signal.stop,
                target_r=signal.target_r,
                time_limit=signal.time_limit,
                units=units,
                risk_amount=units * (signal.close - signal.stop),
                catalyst=candidate.catalyst,
                rank=rank,
            )
        )
        free -= 1
        sectors[candidate.sector] += 1
        cash -= units * signal.close
    return entries, skips
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_entries.py -v && uv run mypy src && uv run ruff check .`
Expected: 9 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/strategy/entries.py tests/test_entries.py
git commit -m "feat: entry gates, ranking, slot and sector caps, and risk sizing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `decide()` and the look-ahead test

**Files:**
- Create: `src/signalbench/strategy/decide.py`, `tests/test_decide.py`

The pullback fixture (`pullback_closes()` in the helpers) rises 0.2 a session, falls 0.8 on index 250 and 3.0 on 251, then stays flat. On 251: close 146, RSI(2) ≈ 2.9, SMA50 ≈ 145.2 > SMA200 ≈ 130.3, min low of 3 sessions 145, ATR(14) = (2 × 13 + 4) / 14, so the stop is 145 − 0.5 × ATR ≈ 143.93.

- [ ] **Step 1: Write the failing test**

`tests/test_decide.py`:

```python
import random
from dataclasses import replace
from datetime import date

import pytest
from pytest import approx

from signalbench.market.bars import AdjustedBar
from signalbench.strategy.decide import decide
from signalbench.strategy.decision import Decision, ExitOrder, Skip, StopUpdate
from signalbench.strategy.market_view import LookAheadError, MarketView
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    empty_portfolio,
    load_test_config,
    make_bar,
    make_market,
    make_position,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)

CONFIG = load_test_config()
DAYS = weekdays(date(2023, 1, 2), 270)
SIGNAL = 251  # pullback_closes() dips on index 250 and 251
QQQ_UP = trend_bars(DAYS, 300.0, 0.5)
NULL = NullReadingsView()


class FakeReadings:
    def __init__(self, blocked: bool = False, catalyst: bool = False, trigger: bool = False) -> None:
        self._blocked, self._catalyst, self._trigger = blocked, catalyst, trigger

    def blocked(self, symbol: str, as_of: date) -> bool:
        return self._blocked

    def catalyst(self, symbol: str, as_of: date) -> bool:
        return self._catalyst

    def sentiment_trigger(self, symbol: str, as_of: date) -> bool:
        return self._trigger


def _market(benchmark: list[AdjustedBar] = QQQ_UP, **kwargs: object) -> MarketView:
    bars = {"AAA": series(DAYS, pullback_closes(len(DAYS)))}
    return make_market(bars, benchmark, DAYS, CONFIG, **kwargs)


def test_pullback_entry_with_stop_and_capped_size() -> None:
    decision = decide(DAYS[SIGNAL], _market(), NULL, empty_portfolio(100.0), CONFIG)
    assert decision.exits == [] and decision.skips == []
    [entry] = decision.entries
    assert (entry.symbol, entry.setup, entry.rank, entry.catalyst) == ("AAA", "pullback", 1, False)
    assert entry.signal_close == approx(146.0)
    # min(low of last 3 sessions) = 145; ATR(14) = (2 * 13 + 4) / 14; stop = 145 - 0.5 * ATR
    assert entry.stop == approx(145.0 - 0.5 * (30 / 14))
    assert (entry.target_r, entry.time_limit) == (2.0, 10)
    # risk units 2 / 2.07 = 0.97 units; the equity/3 cap wins: 33.33 / 146
    assert entry.units == approx(100.0 / 3 / 146.0)
    assert entry.risk_amount == approx(entry.units * (146.0 - entry.stop))


def test_no_signal_the_day_before() -> None:
    assert decide(DAYS[SIGNAL - 1], _market(), NULL, empty_portfolio(100.0), CONFIG) == Decision()


def test_regime_off_skips() -> None:
    market = _market(benchmark=trend_bars(DAYS, 300.0, -0.5))
    decision = decide(DAYS[SIGNAL], market, NULL, empty_portfolio(100.0), CONFIG)
    assert decision.entries == []
    assert decision.skips == [Skip("AAA", "pullback", "regime")]


def test_paused_skips() -> None:
    paused = replace(empty_portfolio(100.0), paused=True, paused_since=DAYS[SIGNAL - 3])
    decision = decide(DAYS[SIGNAL], _market(), NULL, paused, CONFIG)
    assert decision.skips == [Skip("AAA", "pullback", "paused")]


def test_earnings_blackout_skips_for_d1_to_d3_only() -> None:
    blackout = _market(earnings={"AAA": [DAYS[SIGNAL + 3]]})
    decision = decide(DAYS[SIGNAL], blackout, NULL, empty_portfolio(100.0), CONFIG)
    assert decision.skips == [Skip("AAA", "pullback", "earnings_blackout")]
    later = _market(earnings={"AAA": [DAYS[SIGNAL + 4]]})
    assert len(decide(DAYS[SIGNAL], later, NULL, empty_portfolio(100.0), CONFIG).entries) == 1


def test_held_symbol_skips() -> None:
    portfolio = replace(empty_portfolio(100.0), positions=(make_position("P00001", "AAA"),))
    decision = decide(DAYS[SIGNAL], _market(), NULL, portfolio, CONFIG)
    assert decision.entries == []
    assert decision.skips == [Skip("AAA", "pullback", "held")]


def test_blocked_skips_and_catalyst_is_carried() -> None:
    market = _market()
    blocked = decide(DAYS[SIGNAL], market, FakeReadings(blocked=True), empty_portfolio(100.0), CONFIG)
    assert blocked.skips == [Skip("AAA", "pullback", "blocked")]
    tagged = decide(DAYS[SIGNAL], market, FakeReadings(catalyst=True), empty_portfolio(100.0), CONFIG)
    assert tagged.entries[0].catalyst is True


def test_illiquid_symbol_is_ignored_without_a_skip() -> None:
    thin = [replace(bar, traded_value=1_000_000.0) for bar in series(DAYS, pullback_closes(len(DAYS)))]
    market = make_market({"AAA": thin}, QQQ_UP, DAYS, CONFIG)
    assert decide(DAYS[SIGNAL], market, NULL, empty_portfolio(100.0), CONFIG) == Decision()


def test_sentiment_fires_only_when_enabled_and_triggered() -> None:
    closes = [100.0 + 0.2 * i for i in range(len(DAYS))]
    bars = series(DAYS, closes)
    bars[260] = make_bar(DAYS[260], closes[260] + 3.0)  # close above the prior high
    market = make_market({"AAA": bars}, QQQ_UP, DAYS, CONFIG)
    sentiment_only = CONFIG.with_setups(("sentiment",))
    fired = decide(DAYS[260], market, FakeReadings(trigger=True), empty_portfolio(100.0), sentiment_only)
    assert [e.setup for e in fired.entries] == ["sentiment"]
    assert decide(DAYS[260], market, NULL, empty_portfolio(100.0), sentiment_only).entries == []
    price_only = CONFIG.with_setups(("pullback", "breakout"))
    assert decide(DAYS[260], market, FakeReadings(trigger=True), empty_portfolio(100.0), price_only).entries == []


def test_exit_and_trailing_stop_updates() -> None:
    market = _market()
    stopped = make_position("P00001", "AAA", stop=146.5)
    trailing = make_position(
        "P00002", "AAA", setup="breakout", stop=130.0, target=None, time_limit=30, highest_close=149.8
    )
    portfolio = replace(empty_portfolio(100.0), positions=(trailing, stopped))
    decision = decide(DAYS[SIGNAL], market, NULL, portfolio, CONFIG)
    assert decision.exits == [ExitOrder("P00001", "AAA", "stop")]
    atr = 30 / 14
    assert decision.stop_updates == [StopUpdate("P00002", 130.0, approx(149.8 - 3 * atr))]


def test_earnings_exit_two_sessions_ahead() -> None:
    market = _market(earnings={"AAA": [DAYS[SIGNAL + 2]]})
    held = replace(empty_portfolio(100.0), positions=(make_position("P00001", "AAA"),))
    assert decide(DAYS[SIGNAL], market, NULL, held, CONFIG).exits == [
        ExitOrder("P00001", "AAA", "earnings")
    ]
    three_ahead = _market(earnings={"AAA": [DAYS[SIGNAL + 3]]})
    assert decide(DAYS[SIGNAL], three_ahead, NULL, held, CONFIG).exits == []


def _random_walk(rng: random.Random, days: list[date]) -> list[AdjustedBar]:
    bars: list[AdjustedBar] = []
    close = 100.0
    for day in days:
        close = max(5.0, close * (1 + rng.gauss(0.0008, 0.02)))
        volume = int(1_000_000 * (3.0 if rng.random() < 0.05 else 1.0))
        spread = close * 0.01
        bars.append(make_bar(day, close, open_=close * (1 + rng.gauss(0, 0.005)),
                             high=close + spread, low=close - spread, volume=volume))
    return bars


def _truncate(bars: list[AdjustedBar], as_of: date) -> list[AdjustedBar]:
    return [bar for bar in bars if bar.date <= as_of]


def test_decide_on_truncated_data_equals_decide_on_full_data() -> None:
    rng = random.Random(20260924)
    days = weekdays(date(2021, 1, 4), 420)
    symbols = {name: _random_walk(rng, days) for name in ("AAA", "BBB", "CCC", "DDD")}
    sectors = {"AAA": "Energy", "BBB": "Energy", "CCC": "Energy", "DDD": "Utilities"}
    earnings = {"AAA": [days[300], days[350]], "CCC": [days[333]]}
    qqq = trend_bars(days, 300.0, 0.3)
    full = make_market(symbols, qqq, days, CONFIG, sectors=sectors, earnings=earnings)
    held = make_position("P00001", "BBB", sector="Energy", setup="breakout", stop=1.0,
                         target=None, time_limit=30, highest_close=150.0, sessions_held=3)
    portfolio = replace(empty_portfolio(100.0), positions=(held,))
    checked = rng.sample(range(260, 415), 25)
    non_empty = 0
    for index in checked:
        as_of = days[index]
        truncated = make_market(
            {name: _truncate(bars, as_of) for name, bars in symbols.items()},
            _truncate(qqq, as_of), days, CONFIG, sectors=sectors, earnings=earnings,
        )
        expected = decide(as_of, full, NULL, portfolio, CONFIG)
        assert decide(as_of, truncated, NULL, portfolio, CONFIG) == expected
        non_empty += expected != Decision(stop_updates=expected.stop_updates)
    assert non_empty > 0  # the sample exercised at least one entry, exit, or skip


def test_decide_is_deterministic() -> None:
    market = _market()
    first = decide(DAYS[SIGNAL], market, NULL, empty_portfolio(100.0), CONFIG)
    assert decide(DAYS[SIGNAL], market, NULL, empty_portfolio(100.0), CONFIG) == first


def test_direct_future_access_raises() -> None:
    with pytest.raises(LookAheadError):
        _market().at(DAYS[SIGNAL]).snapshot("AAA", DAYS[SIGNAL + 1])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_decide.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.strategy.decide'`.

- [ ] **Step 3: Implement**

`src/signalbench/strategy/decide.py`:

```python
"""The one decision function shared by the backtest and the live scan (spec 02).

Pure: no database, no clock, no network. Same inputs, same Decision.
"""

from datetime import date

from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.decision import Decision, ExitOrder, Skip, StopUpdate
from signalbench.strategy.entries import Candidate, Gates, allocate, gate_reason
from signalbench.strategy.exits import exit_reason, trailed_stop
from signalbench.strategy.market_view import AsOfView, MarketView
from signalbench.strategy.portfolio import PortfolioState
from signalbench.strategy.readings import ReadingsView
from signalbench.strategy.setups import first_signal


def _exits(
    view: AsOfView, portfolio: PortfolioState, config: StrategyConfig
) -> tuple[list[ExitOrder], list[StopUpdate]]:
    exits: list[ExitOrder] = []
    updates: list[StopUpdate] = []
    for position in sorted(portfolio.positions, key=lambda p: p.id):
        snap = view.snapshot(position.symbol)
        if snap is None:
            continue
        soon = view.earnings_within(position.symbol, config.earnings_exit_sessions)
        reason = exit_reason(position, snap, soon)
        if reason is not None:
            exits.append(ExitOrder(position.id, position.symbol, reason))
            continue
        new_stop = trailed_stop(position, snap, config)
        if new_stop is not None:
            updates.append(StopUpdate(position.id, position.stop, new_stop))
    return exits, updates


def _candidates(
    view: AsOfView,
    readings: ReadingsView,
    portfolio: PortfolioState,
    config: StrategyConfig,
) -> tuple[list[Candidate], list[Skip]]:
    as_of = view.as_of
    regime_on = view.regime_on()
    candidates: list[Candidate] = []
    skips: list[Skip] = []
    for symbol in view.symbols:
        snap = view.snapshot(symbol)
        if snap is None or snap.date != as_of or snap.median_traded_value is None:
            continue
        if not view.is_active(symbol):
            continue
        triggered = "sentiment" in config.enabled_setups and readings.sentiment_trigger(
            symbol, as_of
        )
        signal = first_signal(snap, config, triggered)
        if signal is None:
            continue
        reason = gate_reason(
            Gates(
                held=portfolio.holds(symbol),
                regime_on=regime_on,
                paused=portfolio.paused,
                earnings_soon=view.earnings_within(symbol, config.earnings_blackout_sessions),
                blocked=readings.blocked(symbol, as_of),
            )
        )
        if reason is not None:
            skips.append(Skip(symbol, signal.setup, reason))
            continue
        candidates.append(
            Candidate(
                symbol=symbol,
                sector=view.sector(symbol),
                signal=signal,
                median_traded_value=snap.median_traded_value,
                catalyst=readings.catalyst(symbol, as_of),
            )
        )
    return candidates, skips


def decide(
    as_of: date,
    market: MarketView,
    readings: ReadingsView,
    portfolio: PortfolioState,
    config: StrategyConfig,
) -> Decision:
    view = market.at(as_of)
    exits, stop_updates = _exits(view, portfolio, config)
    candidates, gate_skips = _candidates(view, readings, portfolio, config)
    entries, slot_skips = allocate(candidates, portfolio, config)
    return Decision(
        entries=entries,
        exits=exits,
        stop_updates=stop_updates,
        skips=gate_skips + slot_skips,
    )
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_decide.py -v && uv run mypy src && uv run ruff check .`
Expected: 14 passed; mypy and ruff clean.

- [ ] **Step 5: Confirm the strategy package stays pure**

Run: `grep -rn -E "^(from|import) (sqlmodel|sqlalchemy|pandas|numpy|exchange_calendars|httpx)|datetime.now|date.today" src/signalbench/strategy`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/strategy/decide.py tests/test_decide.py
git commit -m "feat: pure decide() shared by the backtest and the live scan

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Simulator

**Files:**
- Create: `src/signalbench/backtest/simulator.py`, `tests/test_simulator.py`

- [ ] **Step 1: Write the failing test**

`tests/test_simulator.py`:

```python
from datetime import date

from pytest import approx

from signalbench.backtest.simulator import (
    RiskState,
    SimulationResult,
    simulate,
    step_risk,
)
from signalbench.market.bars import AdjustedBar
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

CONFIG = load_test_config().with_setups(("pullback",))
DAYS = weekdays(date(2023, 1, 2), 280)
SIGNAL, ENTRY = 251, 252
STOP = 145.0 - 0.5 * (30 / 14)  # from the pullback fixture (see test_decide.py)
COST = 0.002


def _run(bars: list[AdjustedBar], start: int = 240, end: int = 270) -> SimulationResult:
    market = make_market({"AAA": bars}, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG)
    return simulate(market, NullReadingsView(), CONFIG, DAYS[start], DAYS[end])


def _events(result: SimulationResult, kind: str) -> list[dict[str, object]]:
    return [event for event in result.events if event["event"] == kind]


def test_entry_fills_at_next_open_with_cost_and_target_from_the_fill() -> None:
    result = _run(series(DAYS, pullback_closes(len(DAYS))), end=260)  # before the time exit
    [entry] = _events(result, "entry")
    assert entry["date"] == DAYS[ENTRY].isoformat()
    assert entry["price"] == approx(146.0 * (1 + COST))
    assert entry["units"] == approx(100.0 / 3 / 146.0)
    [position] = result.open_positions
    fill = 146.0 * (1 + COST)
    assert position.entry_price == approx(fill)
    assert position.stop == approx(STOP)
    assert position.target == approx(fill + 2 * (fill - STOP))
    assert position.sessions_held == 260 - ENTRY + 1  # the entry session counts as 1
    assert result.equity_curve[-1].cash == approx(100.0 - position.units * fill)


def test_gap_up_above_one_percent_skips() -> None:
    bars = with_bar(series(DAYS, pullback_closes(len(DAYS))), ENTRY, open=146.0 * 1.01 + 0.01)
    result = _run(bars)
    assert {"date": DAYS[ENTRY].isoformat(), "event": "skip", "symbol": "AAA",
            "setup": "pullback", "reason": "gap_up"} in result.events
    assert all(e["date"] != DAYS[ENTRY].isoformat() for e in _events(result, "entry"))


def test_open_exactly_one_percent_up_still_fills() -> None:
    bars = with_bar(series(DAYS, pullback_closes(len(DAYS))), ENTRY, open=146.0 * (1.0 + 0.01))
    assert _events(_run(bars), "entry")[0]["date"] == DAYS[ENTRY].isoformat()


def test_open_at_or_below_the_stop_skips() -> None:
    bars = with_bar(series(DAYS, pullback_closes(len(DAYS))), ENTRY, open=STOP)
    result = _run(bars)
    skips = [e for e in _events(result, "skip") if e["date"] == DAYS[ENTRY].isoformat()]
    assert [e["reason"] for e in skips] == ["gap_below_stop"]


def test_crash_pauses_then_auto_resumes_with_peak_reset() -> None:
    closes = pullback_closes(len(DAYS))
    closes[ENTRY + 1 :] = [60.0] * (len(DAYS) - ENTRY - 1)
    result = _run(series(DAYS, closes))
    [trade] = result.trades
    assert (trade.reason, trade.signal_date, trade.entry_date) == ("stop", DAYS[SIGNAL], DAYS[ENTRY])
    assert (trade.exit_signal_date, trade.exit_date) == (DAYS[ENTRY + 1], DAYS[ENTRY + 2])
    assert trade.exit_price == approx(60.0 * (1 - COST))
    assert trade.r == approx((60.0 * (1 - COST) - 146.0 * (1 + COST)) / (146.0 * (1 + COST) - STOP))
    assert trade.sessions_held == 2
    [pause] = _events(result, "pause")
    [resume] = _events(result, "resume")
    assert pause["date"] == DAYS[ENTRY + 1].isoformat()
    assert pause["peak"] == approx(100.0)
    assert resume["date"] == DAYS[ENTRY + 1 + 10].isoformat()
    assert resume["peak"] == approx(resume["equity"])  # peak := equity at resume
    assert resume["equity"] == approx(100.0 + trade.pnl)


def test_step_risk_pauses_below_85_percent_of_peak() -> None:
    state = RiskState(peak=100.0, paused=False, paused_since=None, paused_at=None)
    same, change = step_risk(state, 85.0, 5, DAYS[5], CONFIG)
    assert (same.paused, change) == (False, None)  # exactly -15% is not below
    paused, change = step_risk(state, 84.99, 5, DAYS[5], CONFIG)
    assert (paused.paused, paused.paused_since, paused.paused_at, change) == (
        True, DAYS[5], 5, "pause",
    )
    higher, _ = step_risk(state, 120.0, 5, DAYS[5], CONFIG)
    assert higher.peak == 120.0


def test_step_risk_resumes_after_ten_sessions_and_resets_the_peak() -> None:
    paused = RiskState(peak=100.0, paused=True, paused_since=DAYS[5], paused_at=5)
    still, change = step_risk(paused, 80.0, 14, DAYS[14], CONFIG)
    assert (still.paused, change) == (True, None)
    resumed, change = step_risk(paused, 80.0, 15, DAYS[15], CONFIG)
    assert (resumed.paused, resumed.peak, change) == (False, 80.0, "resume")


def test_last_entry_is_trimmed_to_the_cash_left() -> None:
    # Three identical signals fill the three slots; each is sized at equity / 3 on the close.
    # All open 1% higher, so the third can only buy what the first two left.
    bars = with_bar(series(DAYS, pullback_closes(len(DAYS))), ENTRY, open=146.0 * 1.01)
    sectors = {"AAA": "Energy", "BBB": "Utilities", "CCC": "Financials"}
    market = make_market(
        {symbol: bars for symbol in sectors}, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG,
        sectors=sectors,
    )
    result = simulate(market, NullReadingsView(), CONFIG, DAYS[240], DAYS[260])
    entries = _events(result, "entry")
    assert [(e["symbol"], e["trimmed"]) for e in entries] == [
        ("AAA", False), ("BBB", False), ("CCC", True),
    ]
    assert result.equity_curve[ENTRY - 240].cash == approx(0.0, abs=1e-9)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_simulator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.backtest.simulator'`.

- [ ] **Step 3: Implement**

`src/signalbench/backtest/simulator.py`:

```python
"""Day-by-day simulation of decide() over NYSE sessions (spec 02, Simulator).

For each session t: at the open, fill the exits and then the entries decided at t-1;
at the close, mark to market, update the peak and pause state, and call decide(t).
"""

from dataclasses import dataclass, replace
from datetime import date

from signalbench.strategy.config import SetupName, StrategyConfig
from signalbench.strategy.decide import decide
from signalbench.strategy.decision import EntryOrder, ExitOrder, ExitReason, SkipReason
from signalbench.strategy.market_view import MarketView
from signalbench.strategy.portfolio import PortfolioState, Position
from signalbench.strategy.readings import ReadingsView

Event = dict[str, object]


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
    entry_price: float
    exit_price: float
    initial_stop: float
    units: float
    r: float  # (exit fill - entry fill) / (entry fill - initial stop), costs included
    pnl: float
    reason: ExitReason
    sessions_held: int


@dataclass(frozen=True)
class EquityPoint:
    date: date
    equity: float
    cash: float
    open_positions: int


@dataclass(frozen=True)
class SimulationResult:
    start: date
    end: date
    equity_curve: list[EquityPoint]
    trades: list[TradeRecord]
    events: list[Event]
    open_positions: list[Position]


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
    initial_stop: float


@dataclass(frozen=True)
class _Orders:
    """What decide() returned at a close, to fill at the next open."""

    decided_on: date
    exits: list[ExitOrder]
    entries: list[EntryOrder]


def entry_skip(entry: EntryOrder, open_price: float, cash: float, config: StrategyConfig) -> SkipReason | None:
    """Open-time skip rules: gap_up, gap_below_stop, and no cash left."""
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

        # Open: exits first, then entries, both decided at the previous close.
        if orders is not None:
            for order in orders.exits:
                position = positions[order.position_id]
                snap = view.snapshot(position.symbol)
                if snap is None or snap.date != day:
                    events.append(_event(day, "exit_deferred", position_id=position.id))
                    continue
                fill = snap.open * (1.0 - config.cost_per_side)
                cash += position.units * fill
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
                    entry_price=position.entry_price,
                    exit_price=fill,
                    initial_stop=meta.initial_stop,
                    units=position.units,
                    r=(fill - position.entry_price) / (position.entry_price - meta.initial_stop),
                    pnl=position.units * (fill - position.entry_price),
                    reason=order.reason,
                    sessions_held=position.sessions_held,
                )
                trades.append(trade)
                events.append(
                    _event(day, "exit", position_id=position.id, symbol=position.symbol,
                           reason=order.reason, price=fill, r=trade.r)
                )
            for entry in orders.entries:
                snap = view.snapshot(entry.symbol)
                skip = "no_bar" if snap is None or snap.date != day else None
                if snap is not None and skip is None:
                    skip = entry_skip(entry, snap.open, cash, config)
                if snap is None or skip is not None:
                    events.append(
                        _event(day, "skip", symbol=entry.symbol, setup=entry.setup, reason=skip)
                    )
                    continue
                fill = snap.open * (1.0 + config.cost_per_side)
                units = min(entry.units, cash / fill)  # never spend more cash than there is
                cash -= units * fill
                position_id = f"P{next_id:05d}"
                next_id += 1
                target = None
                if entry.target_r is not None:
                    target = fill + entry.target_r * (fill - entry.stop)
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
                opened[position_id] = _Opened(orders.decided_on, entry.stop)
                events.append(
                    _event(day, "entry", position_id=position_id, symbol=entry.symbol,
                           setup=entry.setup, units=units, price=fill, trimmed=units < entry.units)
                )

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
        equity = cash + value
        risk, change = step_risk(risk, equity, index, day, config)
        if change is not None:
            events.append(_event(day, change, equity=equity, peak=risk.peak))
        curve.append(EquityPoint(day, equity, cash, len(positions)))

        state = PortfolioState(
            cash=cash,
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
        orders = _Orders(day, decision.exits, decision.entries)

    return SimulationResult(
        start=sessions[0],
        end=sessions[-1],
        equity_curve=curve,
        trades=trades,
        events=events,
        open_positions=list(positions.values()),
    )


def _event(day: date, kind: str, **fields: object) -> Event:
    return {"date": day.isoformat(), "event": kind, **fields}
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_simulator.py -v && uv run mypy src && uv run ruff check .`
Expected: 8 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/simulator.py tests/test_simulator.py
git commit -m "feat: day-by-day simulator with open fills, costs, and pause/resume

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Known-date scenarios for every exit reason

**Files:**
- Create: `tests/test_scenarios.py`

This task only adds tests. They pin Tasks 6–10 end to end: each setup fires on a known date, with a known stop and target, and exits for a known reason on a known date. They should pass on the first run. If one fails, the bug is in a module from Tasks 6–10; fix the module, not the expected dates or prices (they were verified when this plan was written).

- [ ] **Step 1: Write the tests**

`tests/test_scenarios.py`:

```python
"""Each setup fires on a known date and exits for a known reason on a known date."""

from datetime import date

from pytest import approx

from signalbench.backtest.simulator import SimulationResult, TradeRecord, simulate
from signalbench.market.bars import AdjustedBar
from signalbench.strategy.config import StrategyConfig
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

DAYS = weekdays(date(2023, 1, 2), 300)
PULLBACK = load_test_config().with_setups(("pullback",))
BREAKOUT = load_test_config().with_setups(("breakout",))
COST = 0.002
# Pullback fixture: signal on 251 (close 146), entry on 252 at the open.
PB_STOP = 145.0 - 0.5 * (30 / 14)
PB_FILL = 147.0 * (1 + COST)  # every pullback scenario opens 252 at 147
PB_TARGET = PB_FILL + 2 * (PB_FILL - PB_STOP)  # about 154.03


def _run(
    bars: list[AdjustedBar], config: StrategyConfig, earnings: list[date] | None = None
) -> SimulationResult:
    market = make_market(
        {"AAA": bars}, trend_bars(DAYS, 300.0, 0.5), DAYS, config,
        earnings={"AAA": earnings or []},
    )
    return simulate(market, NullReadingsView(), config, DAYS[240], DAYS[290])


def _pullback(after: dict[int, float]) -> list[AdjustedBar]:
    """The pullback fixture; from each index in `after`, closes hold that value."""
    closes = pullback_closes(len(DAYS))
    for start in sorted(after):
        closes[start:] = [after[start]] * (len(DAYS) - start)
    return series(DAYS, closes)


def _first(result: SimulationResult) -> TradeRecord:
    return result.trades[0]


def _dates(trade: TradeRecord) -> tuple[int, int, int, int]:
    return (
        DAYS.index(trade.signal_date),
        DAYS.index(trade.entry_date),
        DAYS.index(trade.exit_signal_date),
        DAYS.index(trade.exit_date),
    )


def test_pullback_stop() -> None:
    trade = _first(_run(_pullback({252: 147.0, 253: 143.5}), PULLBACK))
    assert trade.reason == "stop"
    assert _dates(trade) == (251, 252, 253, 254)
    assert (trade.entry_price, trade.initial_stop) == (approx(PB_FILL), approx(PB_STOP))
    assert trade.exit_price == approx(143.5 * (1 - COST))
    assert trade.r == approx((143.5 * (1 - COST) - PB_FILL) / (PB_FILL - PB_STOP))  # about -1.21


def test_pullback_target() -> None:
    trade = _first(_run(_pullback({252: 147.0, 255: 155.0}), PULLBACK))
    assert trade.reason == "target"
    assert _dates(trade) == (251, 252, 255, 256)
    assert 155.0 >= PB_TARGET
    assert trade.r == approx((155.0 * (1 - COST) - PB_FILL) / (PB_FILL - PB_STOP))  # about 2.2
    assert trade.sessions_held == 4


def test_pullback_time_after_ten_sessions() -> None:
    trade = _first(_run(_pullback({252: 147.0}), PULLBACK))
    assert trade.reason == "time"
    assert _dates(trade) == (251, 252, 261, 262)  # 252 is session 1, 261 is session 10
    assert trade.sessions_held == 10


def test_pullback_earnings_exit_two_sessions_ahead() -> None:
    trade = _first(_run(_pullback({252: 147.0}), PULLBACK, earnings=[DAYS[256]]))
    assert trade.reason == "earnings"
    assert _dates(trade) == (251, 252, 254, 255)  # at 254 the event is D+2


def _breakout_bars() -> list[AdjustedBar]:
    """Uptrend 0.2/day, volume spike on 250 (breakout), +1/day to 160 on 260, then -3/day."""
    closes = [100.0 + 0.2 * i for i in range(251)]
    closes += [151.0 + k for k in range(10)]
    closes += [157.0 - 3.0 * j for j in range(len(DAYS) - 261)]
    return with_bar(series(DAYS, [max(c, 5.0) for c in closes]), 250, volume=2_000_000)


def test_breakout_trailing_stop_ratchets_then_stops_out() -> None:
    result = _run(_breakout_bars(), BREAKOUT)
    trade = _first(result)
    assert trade.setup == "breakout"
    assert trade.reason == "stop"
    assert trade.initial_stop == approx(150.0 - 2 * 2.0)  # close - 2 x ATR(14) of 2.0
    assert _dates(trade) == (250, 251, 262, 263)
    updates = [e for e in result.events if e["event"] == "stop_update"]
    stops = [e["new_stop"] for e in updates]
    # highest close - 3 x ATR: 153 - 6 = 147 on 253, then +1 a day up to 160 - 6 = 154 on 260
    assert stops == [approx(147.0 + k) for k in range(8)]
    assert [DAYS.index(date.fromisoformat(str(e["date"]))) for e in updates] == list(range(253, 261))
    assert all(e["new_stop"] > e["old_stop"] for e in updates)  # it never loosens
    assert trade.exit_price == approx(151.0 * (1 - COST))  # close 154 <= stop 154 on 262
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_scenarios.py -v`
Expected: 5 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_scenarios.py
git commit -m "test: known-date scenarios for stop, target, time, earnings, and trailing exits

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Run metrics

**Files:**
- Modify: `src/signalbench/backtest/metrics.py` (imports + append; the existing `Trade`, `Metrics`, and `compute_metrics` stay unchanged)
- Create: `tests/test_run_metrics.py`

- [ ] **Step 1: Write the failing test**

`tests/test_run_metrics.py`:

```python
import math
from datetime import date

from pytest import approx

from signalbench.backtest.metrics import (
    TradeStats,
    cagr,
    max_drawdown,
    run_metrics,
    sharpe,
    trade_stats,
)
from signalbench.backtest.simulator import EquityPoint, SimulationResult, TradeRecord
from strategy_helpers import load_test_config

PARAMS = load_test_config().backtest


def _trade(entry: date, r: float, held: int = 5) -> TradeRecord:
    return TradeRecord(
        position_id=f"P-{entry}-{r}", symbol="AAA", setup="pullback", sector="Energy",
        signal_date=entry, entry_date=entry, exit_signal_date=entry, exit_date=entry,
        entry_price=100.0, exit_price=100.0 + r, initial_stop=99.0, units=1.0, r=r,
        pnl=r, reason="time", sessions_held=held,
    )


def test_trade_stats_by_hand() -> None:
    stats = trade_stats([_trade(date(2015, 1, 5), r, held) for r, held in ((2.0, 4), (-1.0, 10), (0.5, 7))])
    assert stats == TradeStats(trades=3, win_rate=approx(2 / 3), mean_r=approx(0.5),
                               median_r=approx(0.5), average_hold=approx(7.0))
    assert trade_stats([]) == TradeStats(0, 0.0, 0.0, 0.0, 0.0)


def test_sharpe_by_hand() -> None:
    # returns +10%, -10%, +10%: mean 1/30, sample stdev 0.2 / sqrt(3)
    assert sharpe([100.0, 110.0, 99.0, 108.9]) == approx(math.sqrt(3) / 6 * math.sqrt(252))
    assert sharpe([100.0, 100.0, 100.0]) == 0.0
    assert sharpe([100.0, 101.0]) == 0.0  # one return is not enough


def test_max_drawdown_and_cagr_by_hand() -> None:
    assert max_drawdown([100.0, 120.0, 90.0, 130.0, 117.0]) == approx(0.25)
    assert max_drawdown([100.0, 101.0]) == 0.0
    # 2020-01-01 to 2024-01-01 is 1461 days = 4.0 years of 365.25
    assert cagr(100.0, 146.41, date(2020, 1, 1), date(2024, 1, 1)) == approx(0.10)
    assert cagr(100.0, 50.0, date(2020, 1, 1), date(2020, 1, 1)) == 0.0


def test_run_metrics_splits_halves_and_recent_by_entry_date() -> None:
    trades = [
        _trade(date(2015, 3, 2), 1.0),
        _trade(date(2018, 12, 31), -0.5),  # last day of H1
        _trade(date(2019, 1, 2), 0.3),
        _trade(date(2026, 9, 15), 2.0),  # first recent day
    ]
    days = [date(2012, 1, 3), date(2012, 1, 4), date(2012, 1, 5), date(2012, 1, 6)]
    curve = [
        EquityPoint(days[0], 100.0, 100.0, 0),
        EquityPoint(days[1], 110.0, 50.0, 1),
        EquityPoint(days[2], 99.0, 50.0, 1),
        EquityPoint(days[3], 108.9, 108.9, 0),
    ]
    events: list[dict[str, object]] = [
        {"date": "2012-01-04", "event": "skip", "reason": "regime"},
        {"date": "2012-01-04", "event": "skip", "reason": "regime"},
        {"date": "2012-01-05", "event": "skip", "reason": "gap_up"},
        {"date": "2012-01-05", "event": "pause", "equity": 99.0, "peak": 110.0},
    ]
    result = SimulationResult(days[0], days[-1], curve, trades, events, open_positions=[])
    metrics = run_metrics(result, PARAMS)
    assert metrics.trades == 4
    assert metrics.mean_r == approx((1.0 - 0.5 + 0.3 + 2.0) / 4)
    assert (metrics.trades_h1, metrics.mean_r_h1) == (2, approx(0.25))
    assert (metrics.trades_h2, metrics.mean_r_h2) == (2, approx(1.15))
    assert (metrics.recent.trades, metrics.recent.mean_r) == (1, approx(2.0))
    assert metrics.total_return == approx(0.089)
    assert metrics.sharpe == approx(math.sqrt(3) / 6 * math.sqrt(252))
    assert metrics.max_drawdown == approx(0.1)
    assert metrics.exposure_pct == approx(0.5)
    assert metrics.pauses == 1
    assert metrics.skips_by_reason == {"gap_up": 1, "regime": 2}
    assert metrics.open_positions_at_end == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_run_metrics.py -v`
Expected: FAIL with `ImportError: cannot import name 'TradeStats' from 'signalbench.backtest.metrics'`.

- [ ] **Step 3: Implement**

In `src/signalbench/backtest/metrics.py`, replace the import block (everything from `import math` to `import numpy as np`) with:

```python
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import fmean, median, stdev

import numpy as np

from signalbench.backtest.simulator import SimulationResult, TradeRecord
from signalbench.strategy.config import BacktestParams
```

Keep `from __future__ import annotations` as the first line. Then append to the end of the file:

```python
# --- Spec 02 run metrics (plain floats; the functions above serve older callers) ---


@dataclass(frozen=True)
class TradeStats:
    trades: int
    win_rate: float
    mean_r: float
    median_r: float
    average_hold: float  # sessions held, entry session included


@dataclass(frozen=True)
class RunMetrics:
    trades: int
    win_rate: float
    mean_r: float
    median_r: float
    average_hold: float
    trades_h1: int
    mean_r_h1: float
    trades_h2: int
    mean_r_h2: float
    total_return: float
    cagr: float
    sharpe: float
    max_drawdown: float
    exposure_pct: float
    pauses: int
    skips_by_reason: dict[str, int]
    open_positions_at_end: int
    recent: TradeStats


def trade_stats(trades: Sequence[TradeRecord]) -> TradeStats:
    if not trades:
        return TradeStats(trades=0, win_rate=0.0, mean_r=0.0, median_r=0.0, average_hold=0.0)
    rs = [trade.r for trade in trades]
    return TradeStats(
        trades=len(trades),
        win_rate=sum(1 for r in rs if r > 0) / len(rs),
        mean_r=fmean(rs),
        median_r=float(median(rs)),
        average_hold=fmean(trade.sessions_held for trade in trades),
    )


def daily_returns(values: Sequence[float]) -> list[float]:
    return [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]


def sharpe(values: Sequence[float]) -> float:
    """Annualised Sharpe of daily returns: mean / sample stdev x sqrt(252), rf = 0."""
    returns = daily_returns(values)
    if len(returns) < 2:
        return 0.0
    deviation = stdev(returns)
    if deviation == 0.0:
        return 0.0
    return fmean(returns) / deviation * math.sqrt(252.0)


def max_drawdown(values: Sequence[float]) -> float:
    """Largest fall from a running peak, as a fraction of that peak."""
    peak = 0.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0.0:
            worst = max(worst, (peak - value) / peak)
    return worst


def cagr(first: float, last: float, start: date, end: date) -> float:
    years = (end - start).days / 365.25
    if years <= 0.0 or first <= 0.0 or last <= 0.0:
        return 0.0
    return float((last / first) ** (1.0 / years) - 1.0)


def run_metrics(result: SimulationResult, params: BacktestParams) -> RunMetrics:
    """Spec 02 "Computed per run". Halves and the recent sample go by entry date."""
    values = [point.equity for point in result.equity_curve]
    everything = trade_stats(result.trades)
    first_half = trade_stats([t for t in result.trades if t.entry_date <= params.h1_end])
    second_half = trade_stats([t for t in result.trades if t.entry_date >= params.h2_start])
    recent = trade_stats([t for t in result.trades if t.entry_date >= params.recent_since])
    skips = Counter(str(e["reason"]) for e in result.events if e["event"] == "skip")
    exposed = sum(1 for point in result.equity_curve if point.open_positions > 0)
    return RunMetrics(
        trades=everything.trades,
        win_rate=everything.win_rate,
        mean_r=everything.mean_r,
        median_r=everything.median_r,
        average_hold=everything.average_hold,
        trades_h1=first_half.trades,
        mean_r_h1=first_half.mean_r,
        trades_h2=second_half.trades,
        mean_r_h2=second_half.mean_r,
        total_return=values[-1] / values[0] - 1.0 if values else 0.0,
        cagr=cagr(values[0], values[-1], result.start, result.end) if values else 0.0,
        sharpe=sharpe(values),
        max_drawdown=max_drawdown(values),
        exposure_pct=exposed / len(values) if values else 0.0,
        pauses=sum(1 for e in result.events if e["event"] == "pause"),
        skips_by_reason=dict(sorted(skips.items())),
        open_positions_at_end=len(result.open_positions),
        recent=recent,
    )
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_run_metrics.py tests/test_metrics.py tests/test_determinism.py -v && uv run mypy src && uv run ruff check .`
Expected: 4 new tests pass and the older metrics tests still pass; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/metrics.py tests/test_run_metrics.py
git commit -m "feat: per-run metrics with halves, recent sample, skips, and pauses

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Benchmarks and the pass bar

**Files:**
- Create: `src/signalbench/backtest/benchmarks.py`, `src/signalbench/backtest/passbar.py`, `tests/test_benchmarks_passbar.py`

- [ ] **Step 1: Write the failing test**

`tests/test_benchmarks_passbar.py`:

```python
from dataclasses import replace
from datetime import date

import pytest
from pytest import approx

from signalbench.backtest.benchmarks import benchmark_stats, buy_and_hold, equal_weight
from signalbench.backtest.metrics import RunMetrics, TradeStats
from signalbench.backtest.passbar import Criterion, evaluate_pass_bar, passes
from strategy_helpers import load_test_config, make_bar, weekdays

PARAMS = load_test_config().backtest
DAYS = weekdays(date(2012, 1, 2), 4)


def test_buy_and_hold_normalises_to_the_first_close() -> None:
    bars = [make_bar(DAYS[0], 50.0), make_bar(DAYS[1], 55.0), make_bar(DAYS[3], 45.0)]
    assert buy_and_hold(bars, DAYS) == [1.0, 1.1, 1.1, approx(0.9)]  # DAYS[2] carries 55
    with pytest.raises(ValueError, match="no bar"):
        buy_and_hold(bars[1:], DAYS)


def test_survivor_benchmark_holds_names_with_a_first_day_bar() -> None:
    a = [make_bar(day, close) for day, close in zip(DAYS, [10.0, 12.0, 11.0, 13.0], strict=True)]
    b = [make_bar(day, close) for day, close in zip(DAYS, [20.0, 20.0, 30.0, 10.0], strict=True)]
    late = [make_bar(DAYS[1], 5.0), make_bar(DAYS[2], 50.0)]  # listed after the start: excluded
    curve = equal_weight([a, b, late], DAYS)
    assert curve == [1.0, approx((1.2 + 1.0) / 2), approx((1.1 + 1.5) / 2), approx((1.3 + 0.5) / 2)]


def test_benchmark_stats() -> None:
    stats = benchmark_stats("QQQ buy-and-hold", [1.0, 1.2, 0.9, 1.1], DAYS)
    assert stats.name == "QQQ buy-and-hold"
    assert stats.total_return == approx(0.1)
    assert stats.max_drawdown == approx(0.25)


def _metrics(**changes: object) -> RunMetrics:
    base = RunMetrics(
        trades=30, win_rate=0.5, mean_r=0.2, median_r=0.1, average_hold=6.0,
        trades_h1=15, mean_r_h1=0.1, trades_h2=15, mean_r_h2=0.3,
        total_return=0.5, cagr=0.03, sharpe=0.8, max_drawdown=0.2, exposure_pct=0.4,
        pauses=0, skips_by_reason={}, open_positions_at_end=0,
        recent=TradeStats(0, 0.0, 0.0, 0.0, 0.0),
    )
    return replace(base, **changes)


def test_every_criterion_at_its_exact_threshold() -> None:
    at = evaluate_pass_bar(_metrics(trades=30, mean_r=0.10, mean_r_h1=0.0, sharpe=0.8), 0.8, PARAMS)
    assert at["trades"] == Criterion(30.0, 30.0, True)  # >= 30
    assert at["mean_r"] == Criterion(0.10, 0.10, False)  # > +0.10 is strict
    assert at["mean_r_halves"] == Criterion(0.0, 0.0, False)  # > 0 in both halves
    assert at["sharpe"] == Criterion(0.8, 0.8, True)  # >= QQQ's Sharpe
    assert passes(at) is False


def test_just_inside_and_outside_each_threshold() -> None:
    assert evaluate_pass_bar(_metrics(trades=29), 0.0, PARAMS)["trades"].passed is False
    assert evaluate_pass_bar(_metrics(mean_r=0.1000001), 0.0, PARAMS)["mean_r"].passed is True
    halves = evaluate_pass_bar(_metrics(mean_r_h1=1e-9, mean_r_h2=-1e-9), 0.0, PARAMS)
    assert halves["mean_r_halves"] == Criterion(-1e-9, 0.0, False)
    assert evaluate_pass_bar(_metrics(sharpe=0.79), 0.8, PARAMS)["sharpe"].passed is False


def test_all_four_pass() -> None:
    assert passes(evaluate_pass_bar(_metrics(), 0.5, PARAMS)) is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_benchmarks_passbar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.backtest.benchmarks'`.

- [ ] **Step 3: Implement**

`src/signalbench/backtest/benchmarks.py`:

```python
"""Buy-and-hold benchmarks over the same sessions as a run (spec 02)."""

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from statistics import fmean

from signalbench.backtest.metrics import cagr, max_drawdown, sharpe
from signalbench.market.bars import AdjustedBar


@dataclass(frozen=True)
class BenchmarkStats:
    name: str
    total_return: float
    cagr: float
    sharpe: float
    max_drawdown: float


def closes_on(bars: Sequence[AdjustedBar], sessions: Sequence[date]) -> list[float | None]:
    """The latest adjusted close on or before each session; None before the first bar."""
    dates = [bar.date for bar in bars]
    out: list[float | None] = []
    for day in sessions:
        index = bisect_right(dates, day) - 1
        out.append(bars[index].close if index >= 0 else None)
    return out


def buy_and_hold(bars: Sequence[AdjustedBar], sessions: Sequence[date]) -> list[float]:
    """Value of 1.0 bought at the first session's close, held to the end."""
    closes = closes_on(bars, sessions)
    first = closes[0]
    if first is None:
        raise ValueError(f"benchmark has no bar on or before {sessions[0]}")
    return [(first if close is None else close) / first for close in closes]


def equal_weight(series: Sequence[Sequence[AdjustedBar]], sessions: Sequence[date]) -> list[float]:
    """Survivor benchmark: equal money in every name with a bar on the first session,
    bought at that close and never rebalanced."""
    holdings: list[list[float]] = []
    for bars in series:
        if not any(bar.date == sessions[0] for bar in bars):
            continue
        closes = closes_on(bars, sessions)
        first = closes[0]
        if first is None:
            continue
        holdings.append([1.0 if close is None else close / first for close in closes])
    if not holdings:
        raise ValueError(f"no universe name has a bar on {sessions[0]}")
    return [fmean(values) for values in zip(*holdings, strict=True)]


def benchmark_stats(name: str, curve: Sequence[float], sessions: Sequence[date]) -> BenchmarkStats:
    return BenchmarkStats(
        name=name,
        total_return=curve[-1] / curve[0] - 1.0,
        cagr=cagr(curve[0], curve[-1], sessions[0], sessions[-1]),
        sharpe=sharpe(curve),
        max_drawdown=max_drawdown(curve),
    )
```

`src/signalbench/backtest/passbar.py`:

```python
"""The v1 pass bar (spec 02). A setup run alone must meet every criterion to go live."""

from dataclasses import dataclass

from signalbench.backtest.metrics import RunMetrics
from signalbench.strategy.config import BacktestParams


@dataclass(frozen=True)
class Criterion:
    value: float
    threshold: float
    passed: bool


def evaluate_pass_bar(
    metrics: RunMetrics, qqq_sharpe: float, params: BacktestParams
) -> dict[str, Criterion]:
    """1 trades >= 30; 2 mean R > +0.10; 3 mean R > 0 in both halves; 4 Sharpe >= QQQ's."""
    worst_half = min(metrics.mean_r_h1, metrics.mean_r_h2)
    return {
        "trades": Criterion(
            float(metrics.trades), float(params.min_trades), metrics.trades >= params.min_trades
        ),
        "mean_r": Criterion(metrics.mean_r, params.min_mean_r, metrics.mean_r > params.min_mean_r),
        "mean_r_halves": Criterion(
            worst_half, 0.0, metrics.mean_r_h1 > 0.0 and metrics.mean_r_h2 > 0.0
        ),
        "sharpe": Criterion(metrics.sharpe, qqq_sharpe, metrics.sharpe >= qqq_sharpe),
    }


def passes(bar: dict[str, Criterion]) -> bool:
    return all(criterion.passed for criterion in bar.values())
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_benchmarks_passbar.py -v && uv run mypy src && uv run ruff check .`
Expected: 6 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/benchmarks.py src/signalbench/backtest/passbar.py tests/test_benchmarks_passbar.py
git commit -m "feat: QQQ and survivor benchmarks and the v1 pass bar

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Data fingerprint and determinism

**Files:**
- Modify: `src/signalbench/backtest/fingerprint.py`, `tests/test_determinism.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_determinism.py`, replace the two `signalbench` import lines with:

```python
from signalbench.backtest.fingerprint import (
    data_fingerprint,
    series_summary,
    signal_set_fingerprint,
)
from signalbench.backtest.metrics import RunMetrics, Trade, compute_metrics, run_metrics
from signalbench.backtest.simulator import simulate
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
```

Append:

```python
def test_data_fingerprint_is_order_free_and_sensitive_to_every_field() -> None:
    days = [date(2024, 1, 2), date(2024, 1, 3)]
    aaa = [make_bar(days[0], 10.0), make_bar(days[1], 11.0)]
    qqq = [make_bar(days[0], 300.0), make_bar(days[1], 301.0)]
    assert series_summary("AAA", aaa) == "AAA|2024-01-02|2024-01-03|2|21.0000"
    base = data_fingerprint([("AAA", aaa), ("QQQ", qqq)])
    assert base == data_fingerprint([("QQQ", qqq), ("AAA", aaa)])
    assert len(base) == 64
    assert base != data_fingerprint([("AAA", aaa[:1]), ("QQQ", qqq)])  # row count, last date
    assert base != data_fingerprint([("AAA", [aaa[0], make_bar(days[1], 11.0001)]), ("QQQ", qqq)])
    assert base != data_fingerprint([("AAB", aaa), ("QQQ", qqq)])


def test_two_simulations_give_identical_metrics_and_fingerprints() -> None:
    config = load_test_config().with_setups(("pullback", "breakout"))
    days = weekdays(date(2023, 1, 2), 300)
    bars = {"AAA": series(days, pullback_closes(len(days))), "BBB": trend_bars(days, 50.0, 0.1)}
    qqq = trend_bars(days, 300.0, 0.5)

    def run() -> tuple[RunMetrics, str]:
        market = make_market(bars, qqq, days, config)
        result = simulate(market, NullReadingsView(), config, days[220], days[290])
        fingerprint = data_fingerprint([*bars.items(), ("QQQ", qqq)])
        return run_metrics(result, config.backtest), fingerprint

    first, second = run(), run()
    assert first[0].trades >= 1  # the fixture trades, so the comparison means something
    assert first == second
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_determinism.py -v`
Expected: FAIL with `ImportError: cannot import name 'data_fingerprint' from 'signalbench.backtest.fingerprint'`.

- [ ] **Step 3: Implement**

Replace `src/signalbench/backtest/fingerprint.py` with (the existing `signal_set_fingerprint` is kept and reused):

```python
import hashlib
import math
from collections.abc import Iterable, Sequence

from signalbench.market.bars import AdjustedBar


def signal_set_fingerprint(signal_ids: list[str]) -> str:
    joined = ",".join(sorted(signal_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def series_summary(symbol: str, bars: Sequence[AdjustedBar]) -> str:
    """symbol|first_date|last_date|row_count|round(sum(adj_close), 4)."""
    if not bars:
        return f"{symbol}|||0|0.0000"
    total = round(math.fsum(bar.close for bar in bars), 4)
    return f"{symbol}|{bars[0].date.isoformat()}|{bars[-1].date.isoformat()}|{len(bars)}|{total:.4f}"


def data_fingerprint(series: Iterable[tuple[str, Sequence[AdjustedBar]]]) -> str:
    """SHA-256 over the sorted per-series summaries of every input price series (spec 02)."""
    return signal_set_fingerprint([series_summary(symbol, bars) for symbol, bars in series])
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_determinism.py -v && uv run mypy src && uv run ruff check .`
Expected: 4 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/fingerprint.py tests/test_determinism.py
git commit -m "feat: data fingerprint over input price series; determinism test

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: `backtest_runs` table (migration 0010)

**Files:**
- Modify: `src/signalbench/db/models.py`, `tests/test_schema.py`, `tests/test_migrations.py`
- Create: `alembic/versions/0010_backtest_runs.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_schema.py`, add `BacktestRun,` as the first name in the `from signalbench.db.models import (…)` block, then append:

```python
def test_backtest_run_round_trips_json(session: Session) -> None:
    run = BacktestRun(
        strategy_version="v1",
        config_sha256="a" * 64,
        git_sha="b" * 40,
        setup="pullback",
        jev_mode="off",
        start_date=date(2012, 1, 3),
        end_date=date(2026, 9, 23),
        data_fingerprint="c" * 64,
        metrics={"trades": 31, "skips_by_reason": {"regime": 4}},
        pass_bar={"trades": {"value": 31.0, "threshold": 30.0, "passed": True}},
        passed=True,
        trade_log={"trades": [], "events": [{"date": "2012-01-04", "event": "pause"}]},
    )
    session.add(run)
    session.commit()
    stored = session.exec(select(BacktestRun)).one()
    assert stored.metrics["skips_by_reason"] == {"regime": 4}
    assert stored.pass_bar["trades"]["passed"] is True
    assert stored.trade_log["events"][0]["event"] == "pause"
    assert stored.run_at.tzinfo is not None
```

In `tests/test_migrations.py`, change the head assertion to `assert _script().get_heads() == ["0010_backtest_runs"]` and append:

```python
def test_0010_creates_backtest_runs_with_json_payloads() -> None:
    text = (VERSIONS / "0010_backtest_runs.py").read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0009_document_form"' in text
    assert 'op.create_table(\n        "backtest_runs"' in text
    for column in ("metrics", "pass_bar", "trade_log"):
        assert f'sa.Column("{column}", sa.JSON(), nullable=False)' in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_schema.py tests/test_migrations.py -v`
Expected: FAIL with `ImportError: cannot import name 'BacktestRun' from 'signalbench.db.models'`.

- [ ] **Step 3: Add the model**

In `src/signalbench/db/models.py`, add `from typing import Any` after `from enum import Enum`, and add `JSON,` as the first name in the `from sqlalchemy import (…)` block. Append:

```python
class BacktestRun(SQLModel, table=True):
    """One stored spec 02 backtest run. JSON payloads are written by backtest/runner.py."""

    __tablename__ = "backtest_runs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    strategy_version: str
    config_sha256: str
    git_sha: str
    setup: str  # pullback | breakout | sentiment | combined
    jev_mode: str  # off | filter
    start_date: date
    end_date: date
    data_fingerprint: str
    metrics: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    pass_bar: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    passed: bool
    trade_log: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    run_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(UTCDateTime(), nullable=False),
    )
```

`setup` and `jev_mode` are plain strings (no Postgres enum), validated by the runner.

- [ ] **Step 4: Write migration 0010**

`alembic/versions/0010_backtest_runs.py`:

```python
"""Spec 02 backtest runs

Revision ID: 0010_backtest_runs
Revises: 0009_document_form
Create Date: 2026-09-24 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_backtest_runs"
down_revision: str | None = "0009_document_form"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version", sa.String(), nullable=False),
        sa.Column("config_sha256", sa.String(), nullable=False),
        sa.Column("git_sha", sa.String(), nullable=False),
        sa.Column("setup", sa.String(), nullable=False),
        sa.Column("jev_mode", sa.String(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("data_fingerprint", sa.String(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("pass_bar", sa.JSON(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("trade_log", sa.JSON(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("backtest_runs")
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_schema.py tests/test_migrations.py -v && uv run mypy src && uv run ruff check .`
Expected: all passed (11 in `test_schema.py`, 4 in `test_migrations.py`); mypy and ruff clean.

- [ ] **Step 6: Verify on local Postgres (if Docker is running)**

Run: `uv run alembic upgrade head && uv run alembic downgrade 0009_document_form && uv run alembic upgrade head`
Expected: all three succeed. If Docker isn't running, say so in the task report and move on; CI covers SQLite `create_all`.

- [ ] **Step 7: Commit**

```bash
git add src/signalbench/db/models.py alembic/versions/0010_backtest_runs.py tests/test_schema.py tests/test_migrations.py
git commit -m "feat: backtest_runs table with JSON metrics, pass bar, and trade log (0010)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Report

**Files:**
- Create: `src/signalbench/backtest/report.py`, `tests/test_report.py`

`report.py` owns the stored JSON shape (`metrics`, `pass_bar`, `trade_log`) and renders it, so `backtest show` can reprint any stored run.

- [ ] **Step 1: Write the failing test**

`tests/test_report.py`:

```python
import json
from datetime import date
from pathlib import Path

from signalbench.backtest.benchmarks import BenchmarkStats
from signalbench.backtest.metrics import run_metrics
from signalbench.backtest.passbar import evaluate_pass_bar, passes
from signalbench.backtest.report import (
    CAVEATS,
    metrics_payload,
    pass_bar_payload,
    render_report,
    report_path,
    trade_log_payload,
)
from signalbench.backtest.simulator import simulate
from signalbench.db.models import BacktestRun
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    load_test_config,
    make_market,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)

CONFIG = load_test_config().with_setups(("pullback",))
DAYS = weekdays(date(2023, 1, 2), 300)


def _run(setup: str = "pullback", version: str = "v1") -> BacktestRun:
    closes = pullback_closes(len(DAYS))
    closes[252:] = [147.0] * (len(DAYS) - 252)
    market = make_market({"AAA": series(DAYS, closes)}, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG)
    result = simulate(market, NullReadingsView(), CONFIG, DAYS[240], DAYS[290])
    metrics = run_metrics(result, CONFIG.backtest)
    benchmarks = [
        BenchmarkStats("QQQ buy-and-hold", 0.2, 0.1, 1.5, 0.05),
        BenchmarkStats("Survivor benchmark (equal weight)", 0.3, 0.12, 1.1, 0.08),
    ]
    bar = evaluate_pass_bar(metrics, 1.5, CONFIG.backtest)
    return BacktestRun(
        strategy_version=version,
        config_sha256="c" * 64,
        git_sha="abc123",
        setup=setup,
        jev_mode="off",
        start_date=result.start,
        end_date=result.end,
        data_fingerprint="d" * 64,
        metrics=metrics_payload(metrics, benchmarks, CONFIG.backtest),
        pass_bar=pass_bar_payload(bar),
        passed=passes(bar),
        trade_log=trade_log_payload(result),
    )


def test_payloads_are_plain_json() -> None:
    run = _run()
    for payload in (run.metrics, run.pass_bar, run.trade_log):
        assert json.loads(json.dumps(payload)) == payload
    assert run.trade_log["trades"][0]["signal_date"] == DAYS[251].isoformat()
    assert run.metrics["recent_since"] == "2026-09-15"
    assert run.pass_bar["trades"] == {"value": 1.0, "threshold": 30.0, "passed": False}


def test_report_has_provenance_pass_bar_benchmarks_caveats_and_trades() -> None:
    text = render_report(_run())
    assert text.startswith("# Backtest: pullback (Jev off)\n")
    assert "**Strategy:** v1 · **Result:** FAIL" in text
    for field in ("config_sha256 | `" + "c" * 64, "git_sha | `abc123`", "data_fingerprint | `"):
        assert field in text
    assert "| 1 | Trades | 1.000 | >= 30.000 | no |" in text
    assert "| 4 | Sharpe vs QQQ buy-and-hold |" in text
    assert "| QQQ buy-and-hold | 20.0% | 10.0% | 1.50 | 5.0% |" in text
    assert "| Survivor benchmark (equal weight) |" in text
    for caveat in CAVEATS:
        assert f"- {caveat}" in text
    assert "| 1 | AAA | pullback | " + DAYS[251].isoformat() in text
    assert "POST-HOC" not in text and "Information only" not in text


def test_combined_and_post_hoc_labels() -> None:
    assert "**Information only:**" in render_report(_run(setup="combined"))
    assert "**POST-HOC** (v2)" in render_report(_run(version="v2"))


def test_report_path() -> None:
    path = report_path(Path("reports/backtests"), date(2026, 9, 24), "breakout", "off")
    assert path == Path("reports/backtests/2026-09-24-breakout-off.md")


def test_passed_run_says_pass() -> None:
    run = _run()
    run.passed = True
    assert "**Result:** PASS" in render_report(run)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.backtest.report'`.

- [ ] **Step 3: Implement**

`src/signalbench/backtest/report.py`:

```python
"""The stored JSON shape of a run and its markdown report (spec 02, Persistence and output)."""

from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from signalbench.backtest.benchmarks import BenchmarkStats
from signalbench.backtest.metrics import RunMetrics
from signalbench.backtest.passbar import Criterion
from signalbench.backtest.simulator import SimulationResult
from signalbench.db.models import BacktestRun
from signalbench.strategy.config import BacktestParams

CAVEATS = (
    (
        "Survivorship: the universe is today's CDR list. Compare with the survivor "
        "benchmark, which has the same bias."
    ),
    "US prices stand in for CDR prices. CDR spreads enter only through the cost per side.",
    "Realized SEC Item 2.02 dates stand in for earnings dates known in advance.",
    "Liquidity is checked on US traded value only, because CDR history is short.",
)
PASS_BAR_ROWS = (
    ("trades", "Trades", ">="),
    ("mean_r", "Mean R after costs", ">"),
    ("mean_r_halves", "Mean R in both halves (worse half shown)", ">"),
    ("sharpe", "Sharpe vs QQQ buy-and-hold", ">="),
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return value


def metrics_payload(
    metrics: RunMetrics, benchmarks: list[BenchmarkStats], params: BacktestParams
) -> dict[str, Any]:
    payload: dict[str, Any] = _jsonable(asdict(metrics))
    payload["benchmarks"] = [asdict(stats) for stats in benchmarks]
    payload["h1_end"] = params.h1_end.isoformat()
    payload["h2_start"] = params.h2_start.isoformat()
    payload["recent_since"] = params.recent_since.isoformat()
    return payload


def pass_bar_payload(bar: dict[str, Criterion]) -> dict[str, Any]:
    return {name: asdict(criterion) for name, criterion in bar.items()}


def trade_log_payload(result: SimulationResult) -> dict[str, Any]:
    return {
        "trades": [_jsonable(asdict(trade)) for trade in result.trades],
        "events": _jsonable(result.events),
    }


def report_path(directory: Path, run_date: date, setup: str, jev_mode: str) -> Path:
    return directory / f"{run_date.isoformat()}-{setup}-{jev_mode}.md"


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _header(run: BacktestRun) -> list[str]:
    lines = [
        f"# Backtest: {run.setup} (Jev {run.jev_mode})",
        "",
        f"**Strategy:** {run.strategy_version} · **Result:** {'PASS' if run.passed else 'FAIL'}",
        "",
    ]
    if run.setup == "combined":
        lines += ["**Information only:** a combined run does not change pass or fail.", ""]
    if run.strategy_version != "v1":
        lines += [
            f"**POST-HOC** ({run.strategy_version}): cannot overturn a v1 result on its own.",
            "",
        ]
    return [
        *lines,
        "| Field | Value |",
        "| --- | --- |",
        f"| Run id | `{run.id}` |",
        f"| Sessions | {run.start_date.isoformat()} to {run.end_date.isoformat()} |",
        f"| Run at (UTC) | {run.run_at.strftime('%Y-%m-%d %H:%M')} |",
        f"| config_sha256 | `{run.config_sha256}` |",
        f"| git_sha | `{run.git_sha}` |",
        f"| data_fingerprint | `{run.data_fingerprint}` |",
    ]


def _pass_bar(run: BacktestRun) -> list[str]:
    lines = [
        "## Pass bar",
        "",
        "| # | Criterion | Value | Threshold | Passed |",
        "| --- | --- | --- | --- | --- |",
    ]
    for number, (key, label, comparison) in enumerate(PASS_BAR_ROWS, start=1):
        row = run.pass_bar[key]
        passed = "yes" if row["passed"] else "no"
        lines.append(
            f"| {number} | {label} | {row['value']:.3f} "
            f"| {comparison} {row['threshold']:.3f} | {passed} |"
        )
    return lines


def _metrics(m: dict[str, Any]) -> list[str]:
    recent = m["recent"]
    return [
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Trades | {m['trades']} |",
        f"| Win rate | {_pct(m['win_rate'])} |",
        f"| Mean R | {m['mean_r']:.3f} |",
        f"| Median R | {m['median_r']:.3f} |",
        f"| Mean R, H1 (entries to {m['h1_end']}) | {m['mean_r_h1']:.3f} ({m['trades_h1']}) |",
        f"| Mean R, H2 (entries from {m['h2_start']}) | {m['mean_r_h2']:.3f} ({m['trades_h2']}) |",
        f"| Total return | {_pct(m['total_return'])} |",
        f"| CAGR | {_pct(m['cagr'])} |",
        f"| Sharpe (daily, sqrt 252, rf 0) | {m['sharpe']:.2f} |",
        f"| Max drawdown | {_pct(m['max_drawdown'])} |",
        f"| Exposure | {_pct(m['exposure_pct'])} |",
        f"| Average hold (sessions) | {m['average_hold']:.1f} |",
        f"| Pauses | {m['pauses']} |",
        f"| Open positions at the end | {m['open_positions_at_end']} |",
        "",
        f"## Trades entered on or after {m['recent_since']}",
        "",
        (
            f"Trades {recent['trades']} · win rate {_pct(recent['win_rate'])} · "
            f"mean R {recent['mean_r']:.3f} · median R {recent['median_r']:.3f}"
        ),
    ]


def _benchmarks(m: dict[str, Any]) -> list[str]:
    rows = [
        {
            "name": "This run",
            "total_return": m["total_return"],
            "cagr": m["cagr"],
            "sharpe": m["sharpe"],
            "max_drawdown": m["max_drawdown"],
        },
        *m["benchmarks"],
    ]
    return [
        "## Benchmarks (same sessions)",
        "",
        "| Series | Total return | CAGR | Sharpe | Max drawdown |",
        "| --- | --- | --- | --- | --- |",
        *[
            f"| {row['name']} | {_pct(row['total_return'])} | {_pct(row['cagr'])} "
            f"| {row['sharpe']:.2f} | {_pct(row['max_drawdown'])} |"
            for row in rows
        ],
    ]


def _skips_and_caveats(m: dict[str, Any]) -> list[str]:
    skips: dict[str, int] = m["skips_by_reason"]
    rows = [f"| {reason} | {count} |" for reason, count in skips.items()] or ["| none | 0 |"]
    return [
        "## Skips by reason",
        "",
        "| Reason | Count |",
        "| --- | --- |",
        *rows,
        "",
        "## Caveats",
        "",
        *[f"- {caveat}" for caveat in CAVEATS],
    ]


def _trades(run: BacktestRun) -> list[str]:
    lines = [
        "## Trades",
        "",
        "| # | Symbol | Setup | Signal | Entry | Exit | Reason | Entry price | Exit price | R |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for number, trade in enumerate(run.trade_log["trades"], start=1):
        lines.append(
            f"| {number} | {trade['symbol']} | {trade['setup']} | {trade['signal_date']} "
            f"| {trade['entry_date']} | {trade['exit_date']} | {trade['reason']} "
            f"| {trade['entry_price']:.2f} | {trade['exit_price']:.2f} | {trade['r']:.2f} |"
        )
    return lines


def render_report(run: BacktestRun) -> str:
    sections = [
        _header(run),
        _pass_bar(run),
        _metrics(run.metrics),
        _benchmarks(run.metrics),
        _skips_and_caveats(run.metrics),
        _trades(run),
    ]
    return "\n\n".join("\n".join(section) for section in sections) + "\n"
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_report.py -v && uv run mypy src && uv run ruff check .`
Expected: 5 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/report.py tests/test_report.py
git commit -m "feat: stored run payloads and the markdown backtest report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: Git provenance

**Files:**
- Create: `src/signalbench/backtest/provenance.py`, `tests/test_provenance.py`

The tests create a throwaway git repo in `tmp_path` (git is installed on CI runners; no network).

- [ ] **Step 1: Write the failing test**

`tests/test_provenance.py`:

```python
import subprocess
from pathlib import Path

import pytest

from signalbench.backtest.provenance import committed_unchanged, git_sha


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "strategy_v1.yaml").write_text("version: v1\n", encoding="utf-8")
    _git(tmp_path, "add", "data/strategy_v1.yaml")
    _git(tmp_path, "commit", "-q", "-m", "config")
    return tmp_path


def test_git_sha_is_head_and_marks_dirty_code(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    assert git_sha(repo) == head
    (repo / "notes.txt").write_text("untracked files do not count\n", encoding="utf-8")
    assert git_sha(repo) == head
    (repo / "data" / "strategy_v1.yaml").write_text("version: v2\n", encoding="utf-8")
    assert git_sha(repo) == f"{head}-dirty"


def test_committed_unchanged(repo: Path) -> None:
    config = repo / "data" / "strategy_v1.yaml"
    assert committed_unchanged(repo, config) is True
    config.write_text("version: v1\ncost_per_side: 0.003\n", encoding="utf-8")
    assert committed_unchanged(repo, config) is False
    untracked = repo / "data" / "strategy_v2.yaml"
    untracked.write_text("version: v2\n", encoding="utf-8")
    assert committed_unchanged(repo, untracked) is False


def test_git_sha_outside_a_repo_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="rev-parse"):
        git_sha(tmp_path)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_provenance.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.backtest.provenance'`.

- [ ] **Step 3: Implement**

`src/signalbench/backtest/provenance.py`:

```python
"""Git facts recorded with every run: the code version and whether the config is committed."""

import subprocess  # runs the local git binary with fixed arguments
from pathlib import Path

CODE_PATHS = ("src", "data", "alembic", "pyproject.toml", "uv.lock")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def git_sha(repo: Path) -> str:
    """HEAD's SHA, with "-dirty" when tracked code or data files have uncommitted changes."""
    head = _git(repo, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise RuntimeError(f"git rev-parse HEAD failed: {head.stderr.strip()}")
    status = _git(repo, "status", "--porcelain", "--untracked-files=no", "--", *CODE_PATHS)
    return head.stdout.strip() + ("-dirty" if status.stdout.strip() else "")


def committed_unchanged(repo: Path, path: Path) -> bool:
    """True when `path` is tracked and identical to HEAD (pre-registration check)."""
    relative = str(path.resolve().relative_to(repo.resolve()))
    tracked = _git(repo, "ls-files", "--error-unmatch", "--", relative)
    if tracked.returncode != 0:
        return False
    return _git(repo, "diff", "--quiet", "HEAD", "--", relative).returncode == 0
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_provenance.py -v && uv run mypy src && uv run ruff check .`
Expected: 3 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/provenance.py tests/test_provenance.py
git commit -m "feat: git sha and committed-config checks for backtest provenance

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Runner

**Files:**
- Create: `src/signalbench/backtest/runner.py`, `tests/test_runner.py`

The backtest universe is the US ticker of every entry in `data/cdr_universe.yaml`, with the sector from that file; the benchmark is `config.regime_symbol` (QQQ). Bars load from the start of stored history (2010) so the 200-session SMA is warm by 2012.

- [ ] **Step 1: Write the failing test**

`tests/test_runner.py`:

```python
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, select

from signalbench.backtest.fingerprint import data_fingerprint
from signalbench.backtest.runner import (
    RequiresSpec03Error,
    run_backtest,
    setups_for_run,
)
from signalbench.db.models import BacktestRun, Price, Ticker, TickerKind
from signalbench.ingest.cdr import CdrEntry
from signalbench.market.bars import AdjustedBar
from strategy_helpers import (
    WeekdaySessions,
    load_test_config,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)

DAYS = weekdays(date(2011, 1, 3), 300)  # DAYS[260] is 2012-01-02
DIP = 265
UNIVERSE = [
    CdrEntry("AAA", "ZAAA", "ZAAA.NE", "Aaa", "Information Technology"),
    CdrEntry("BBB", "ZBBB", "ZBBB.NE", "Bbb", "Energy"),
]


def _store(session: Session, symbol: str, kind: TickerKind, bars: list[AdjustedBar]) -> None:
    ticker = Ticker(symbol=symbol, company_name=symbol, kind=kind)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    for bar in bars:
        session.add(
            Price(
                ticker_id=ticker.id,
                date=bar.date,
                open=Decimal(str(round(bar.open, 4))),
                high=Decimal(str(round(bar.high, 4))),
                low=Decimal(str(round(bar.low, 4))),
                close=Decimal(str(round(bar.close, 4))),
                adj_close=Decimal(str(round(bar.close, 4))),
                volume=bar.volume,
            )
        )
    session.commit()


@pytest.fixture
def seeded(session: Session) -> Session:
    _store(session, "AAA", TickerKind.us_stock, series(DAYS, pullback_closes(len(DAYS), dip=DIP)))
    _store(session, "BBB", TickerKind.us_stock, trend_bars(DAYS, 50.0, 0.1))
    _store(session, "QQQ", TickerKind.benchmark, trend_bars(DAYS, 300.0, 0.5))
    return session


def _run(session: Session, tmp_path: Path, setup: str = "pullback") -> tuple[BacktestRun, Path]:
    return run_backtest(
        session,
        setup=setup,  # type: ignore[arg-type]
        jev_mode="off",
        config=load_test_config(),
        config_sha256="f" * 64,
        universe=UNIVERSE,
        calendar=WeekdaySessions(),
        git_sha="abc123",
        run_date=date(2026, 9, 24),
        reports_dir=tmp_path,
    )


def test_run_is_stored_with_provenance_and_report(seeded: Session, tmp_path: Path) -> None:
    run, path = _run(seeded, tmp_path)
    stored = seeded.exec(select(BacktestRun)).one()
    assert stored.id == run.id
    assert (run.setup, run.jev_mode, run.strategy_version) == ("pullback", "off", "test")
    assert (run.config_sha256, run.git_sha) == ("f" * 64, "abc123")
    assert run.start_date == date(2012, 1, 2)  # first session on or after backtest.start
    assert run.end_date == DAYS[-1]  # the last QQQ bar
    assert run.metrics["trades"] >= 1
    assert run.trade_log["trades"][0]["signal_date"] == DAYS[DIP].isoformat()
    assert [b["name"] for b in run.metrics["benchmarks"]] == [
        "QQQ buy-and-hold",
        "Survivor benchmark (equal weight, not rebalanced)",
    ]
    assert set(run.pass_bar) == {"trades", "mean_r", "mean_r_halves", "sharpe"}
    assert run.passed is False  # one trade is far below 30
    assert path == tmp_path / "2026-09-24-pullback-off.md"
    assert path.read_text(encoding="utf-8").startswith("# Backtest: pullback (Jev off)")


def test_data_fingerprint_covers_every_input_series(seeded: Session, tmp_path: Path) -> None:
    run, _ = _run(seeded, tmp_path)
    stored = {
        "AAA": series(DAYS, pullback_closes(len(DAYS), dip=DIP)),
        "BBB": trend_bars(DAYS, 50.0, 0.1),
        "QQQ": trend_bars(DAYS, 300.0, 0.5),
    }
    assert run.data_fingerprint == data_fingerprint(stored.items())


def test_second_report_on_the_same_day_gets_a_suffix(seeded: Session, tmp_path: Path) -> None:
    _, first = _run(seeded, tmp_path)
    second_run, second = _run(seeded, tmp_path)
    assert first != second
    assert second.name == f"2026-09-24-pullback-off-{str(second_run.id)[:8]}.md"


def test_sentiment_and_jev_filter_need_spec_03() -> None:
    with pytest.raises(RequiresSpec03Error, match="spec 03"):
        setups_for_run("sentiment", "off")
    with pytest.raises(RequiresSpec03Error, match="spec 03"):
        setups_for_run("pullback", "filter")
    assert setups_for_run("combined", "off") == ("pullback", "breakout")


def test_unseeded_universe_is_an_error(session: Session, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Not seeded: AAA, BBB, QQQ"):
        _run(session, tmp_path)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.backtest.runner'`.

- [ ] **Step 3: Implement**

`src/signalbench/backtest/runner.py`:

```python
"""Load real data, run one backtest, store it, and write its report (spec 02)."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from sqlmodel import Session, col, select

from signalbench.backtest.benchmarks import benchmark_stats, buy_and_hold, equal_weight
from signalbench.backtest.fingerprint import data_fingerprint
from signalbench.backtest.metrics import run_metrics
from signalbench.backtest.passbar import evaluate_pass_bar, passes
from signalbench.backtest.report import (
    metrics_payload,
    pass_bar_payload,
    render_report,
    report_path,
    trade_log_payload,
)
from signalbench.backtest.simulator import simulate
from signalbench.db.models import BacktestRun, Ticker
from signalbench.ingest.cdr import CdrEntry
from signalbench.ingest.earnings import earnings_dates
from signalbench.market.bars import AdjustedBar, adjusted_bars
from signalbench.market.calendar import Sessions
from signalbench.strategy.config import SetupName, StrategyConfig
from signalbench.strategy.market_view import MarketView, SymbolInput
from signalbench.strategy.readings import NullReadingsView

RunSetup = Literal["pullback", "breakout", "sentiment", "combined"]
JevMode = Literal["off", "filter"]
SETUPS_BY_RUN: dict[str, tuple[SetupName, ...]] = {
    "pullback": ("pullback",),
    "breakout": ("breakout",),
    "combined": ("pullback", "breakout"),
}
QQQ_BENCHMARK = "QQQ buy-and-hold"
SURVIVOR_BENCHMARK = "Survivor benchmark (equal weight, not rebalanced)"


class RequiresSpec03Error(ValueError):
    """The Sentiment setup and the Jev filter need Jev readings, which arrive in spec 03."""


def setups_for_run(setup: RunSetup, jev_mode: JevMode) -> tuple[SetupName, ...]:
    if setup == "sentiment":
        raise RequiresSpec03Error("--setup sentiment requires Jev readings (spec 03).")
    if jev_mode == "filter":
        raise RequiresSpec03Error("--jev filter requires Jev readings (spec 03).")
    return SETUPS_BY_RUN[setup]


@dataclass(frozen=True)
class MarketInputs:
    symbols: list[SymbolInput]
    benchmark: list[AdjustedBar]


def load_market_inputs(
    session: Session, universe: list[CdrEntry], benchmark_symbol: str, end: date | None
) -> MarketInputs:
    """Adjusted US bars, clustered earnings dates, and sectors for every universe name."""
    wanted = [entry.us_symbol for entry in universe] + [benchmark_symbol]
    rows = session.exec(select(Ticker).where(col(Ticker.symbol).in_(wanted))).all()
    tickers = {ticker.symbol: ticker for ticker in rows}
    missing = sorted(set(wanted) - set(tickers))
    if missing:
        raise ValueError(f"Not seeded: {', '.join(missing)}. Run `signalbench seed` first.")
    symbols = [
        SymbolInput(
            symbol=entry.us_symbol,
            sector=entry.sector,
            bars=adjusted_bars(session, tickers[entry.us_symbol].id, end=end),
            earnings=earnings_dates(session, tickers[entry.us_symbol].id),
        )
        for entry in sorted(universe, key=lambda e: e.us_symbol)
    ]
    benchmark = adjusted_bars(session, tickers[benchmark_symbol].id, end=end)
    if not benchmark:
        raise ValueError(f"No {benchmark_symbol} prices. Run `signalbench ingest prices` first.")
    return MarketInputs(symbols=symbols, benchmark=benchmark)


def run_backtest(
    session: Session,
    *,
    setup: RunSetup,
    jev_mode: JevMode,
    config: StrategyConfig,
    config_sha256: str,
    universe: list[CdrEntry],
    calendar: Sessions,
    git_sha: str,
    run_date: date,
    reports_dir: Path,
    end: date | None = None,
) -> tuple[BacktestRun, Path]:
    """Run one setup (or the combined set) with Jev off, store it, and write the report."""
    config = config.with_setups(setups_for_run(setup, jev_mode))
    inputs = load_market_inputs(session, universe, config.regime_symbol, end)
    last = inputs.benchmark[-1].date if end is None else end
    start = config.backtest.start
    lookahead = max(config.earnings_blackout_sessions, config.earnings_exit_sessions)
    sessions = calendar.sessions_between(start, last) + calendar.next_sessions(last, lookahead)
    market = MarketView(inputs.symbols, inputs.benchmark, sessions, config)
    result = simulate(market, NullReadingsView(), config, start, last)
    metrics = run_metrics(result, config.backtest)
    run_sessions = market.sessions_between(result.start, result.end)
    qqq = benchmark_stats(QQQ_BENCHMARK, buy_and_hold(inputs.benchmark, run_sessions), run_sessions)
    survivor = benchmark_stats(
        SURVIVOR_BENCHMARK,
        equal_weight([item.bars for item in inputs.symbols], run_sessions),
        run_sessions,
    )
    bar = evaluate_pass_bar(metrics, qqq.sharpe, config.backtest)
    run = BacktestRun(
        strategy_version=config.version,
        config_sha256=config_sha256,
        git_sha=git_sha,
        setup=setup,
        jev_mode=jev_mode,
        start_date=result.start,
        end_date=result.end,
        data_fingerprint=data_fingerprint(
            [(item.symbol, item.bars) for item in inputs.symbols]
            + [(config.regime_symbol, inputs.benchmark)]
        ),
        metrics=metrics_payload(metrics, [qqq, survivor], config.backtest),
        pass_bar=pass_bar_payload(bar),
        passed=passes(bar),
        trade_log=trade_log_payload(result),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    path = report_path(reports_dir, run_date, setup, jev_mode)
    if path.exists():
        path = path.with_name(f"{path.stem}-{str(run.id)[:8]}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(run), encoding="utf-8")
    return run, path
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_runner.py -v && uv run mypy src && uv run ruff check .`
Expected: 5 passed; mypy and ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/runner.py tests/test_runner.py
git commit -m "feat: backtest runner that loads stored data, stores the run, and writes the report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 19: CLI — `backtest run` and `backtest show`

**Files:**
- Modify: `src/signalbench/cli.py`, `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, add `from pathlib import Path` after `from datetime import date`; change the models import to `from signalbench.db.models import BacktestRun, EarningsEvent, Ticker, TickerKind`; and add `from signalbench.strategy.config import load_strategy_config` after the `RateLimiter` import. Append:

```python
FIXTURE_CONFIG = Path(__file__).parent / "fixtures" / "strategy_test.yaml"


def test_backtest_help_lists_run_and_show() -> None:
    result = runner.invoke(app, ["backtest", "--help"])
    assert result.exit_code == 0
    for command in ("run", "show"):
        assert command in result.stdout


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--setup", "sentiment"], "--setup sentiment requires Jev readings (spec 03)."),
        (["--setup", "pullback", "--jev", "filter"], "--jev filter requires Jev readings (spec 03)."),
    ],
)
def test_backtest_run_refuses_spec_03_modes(args: list[str], message: str) -> None:
    result = runner.invoke(app, ["backtest", "run", *args])
    assert result.exit_code == 2
    assert message in result.stderr


def test_backtest_run_needs_the_config_file(tmp_path: Path) -> None:
    missing = tmp_path / "strategy_v1.yaml"
    result = runner.invoke(app, ["backtest", "run", "--setup", "pullback", "--config", str(missing)])
    assert result.exit_code == 1
    assert "not found" in result.stderr


def test_backtest_run_needs_a_committed_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "committed_unchanged", lambda _repo, _path: False)
    result = runner.invoke(
        app, ["backtest", "run", "--setup", "pullback", "--config", str(FIXTURE_CONFIG)]
    )
    assert result.exit_code == 1
    assert "must be committed, unchanged" in result.stderr


def _stored_run(session: Session) -> BacktestRun:
    run = BacktestRun(
        strategy_version="test", config_sha256="c" * 64, git_sha="abc123", setup="breakout",
        jev_mode="off", start_date=date(2012, 1, 3), end_date=date(2026, 9, 23),
        data_fingerprint="d" * 64, metrics={},
        pass_bar={"trades": {"value": 12.0, "threshold": 30.0, "passed": False}},
        passed=False, trade_log={"trades": [], "events": []},
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_backtest_run_wires_config_calendar_and_git(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: dict[str, object] = {}

    def fake_run(_session: Session, **kwargs: object) -> tuple[BacktestRun, Path]:
        calls.update(kwargs)
        return _stored_run(session), tmp_path / "2026-09-24-breakout-off.md"

    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "committed_unchanged", lambda _repo, _path: True)
    monkeypatch.setattr(cli, "git_sha", lambda _repo: "abc123")
    monkeypatch.setattr(cli, "NyseSessions", lambda: "calendar")
    monkeypatch.setattr(cli, "run_backtest", fake_run)
    result = runner.invoke(
        app, ["backtest", "run", "--setup", "breakout", "--config", str(FIXTURE_CONFIG)]
    )
    assert result.exit_code == 0, result.stderr
    assert (calls["setup"], calls["jev_mode"], calls["git_sha"]) == ("breakout", "off", "abc123")
    assert calls["calendar"] == "calendar"
    assert calls["config_sha256"] == load_strategy_config(FIXTURE_CONFIG)[1]
    assert "FAIL" in result.stdout
    assert "-- trades: 12.000 vs 30.000" in result.stdout
    assert "report: " in result.stdout


def test_backtest_show_prints_the_stored_report(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _stored_run(session)
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "render_report", lambda stored: f"report for {stored.id}\n")
    result = runner.invoke(app, ["backtest", "show", str(run.id)])
    assert result.exit_code == 0
    assert result.stdout == f"report for {run.id}\n"


def test_backtest_show_unknown_or_bad_id(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    unknown = runner.invoke(app, ["backtest", "show", "00000000-0000-0000-0000-000000000000"])
    assert unknown.exit_code == 1
    assert "No backtest run" in unknown.stderr
    bad = runner.invoke(app, ["backtest", "show", "not-a-uuid"])
    assert bad.exit_code == 1
    assert "Not a run id" in bad.stderr
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: the 8 new tests FAIL (`No such command 'backtest'`, exit code 2 where 0 or 1 is expected); the older CLI tests still pass.

- [ ] **Step 3: Implement**

In `src/signalbench/cli.py`:

1. Replace the standard-library imports at the top with:

```python
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo
```

2. Replace `from signalbench.config import settings` and the `signalbench.db.models` import with:

```python
from signalbench.backtest.provenance import committed_unchanged, git_sha
from signalbench.backtest.report import render_report
from signalbench.backtest.runner import (
    JevMode,
    RequiresSpec03Error,
    RunSetup,
    run_backtest,
    setups_for_run,
)
from signalbench.config import settings
from signalbench.db.models import BacktestRun, Ticker, TickerKind
```

3. After `from signalbench.ingest.stats import collect_stats`, add:

```python
from signalbench.market.calendar import NyseSessions
from signalbench.strategy.config import load_strategy_config
```

4. Replace the `UNIVERSE_PATH = …` line with:

```python
REPO_ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_PATH = REPO_ROOT / "data" / "cdr_universe.yaml"
STRATEGY_V1_PATH = REPO_ROOT / "data" / "strategy_v1.yaml"
REPORTS_DIR = REPO_ROOT / "reports" / "backtests"
NEW_YORK = ZoneInfo("America/New_York")
```

5. After `app.add_typer(universe_app, name="universe")`, add:

```python
backtest_app = typer.Typer(help="Pre-registered strategy backtests (spec 02).")
app.add_typer(backtest_app, name="backtest")


class SetupChoice(str, Enum):
    pullback = "pullback"
    breakout = "breakout"
    sentiment = "sentiment"
    combined = "combined"


class JevChoice(str, Enum):
    off = "off"
    filter = "filter"
```

6. Append to the end of the file:

```python
@backtest_app.command("run")
def backtest_run(
    setup: Annotated[SetupChoice, typer.Option("--setup", help="Which setup to simulate.")],
    jev: Annotated[JevChoice, typer.Option("--jev", help="Jev filter mode.")] = JevChoice.off,
    config: Annotated[
        Path, typer.Option("--config", help="Pre-registered strategy parameters.")
    ] = STRATEGY_V1_PATH,
) -> None:
    run_setup: RunSetup = setup.value
    jev_mode: JevMode = jev.value
    try:
        setups_for_run(run_setup, jev_mode)
    except RequiresSpec03Error as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from None
    if not config.exists():
        typer.echo(
            f"{config} not found. It is written after the spread survey is approved "
            "(spec 02 pre-registration).",
            err=True,
        )
        raise typer.Exit(1)
    if not committed_unchanged(REPO_ROOT, config):
        typer.echo(
            f"{config.name} must be committed, unchanged, before a backtest on real data "
            "(spec 02 pre-registration).",
            err=True,
        )
        raise typer.Exit(1)
    strategy, sha = load_strategy_config(config)
    with get_session() as session:
        run, path = run_backtest(
            session,
            setup=run_setup,
            jev_mode=jev_mode,
            config=strategy,
            config_sha256=sha,
            universe=load_universe(UNIVERSE_PATH),
            calendar=NyseSessions(),
            git_sha=git_sha(REPO_ROOT),
            run_date=datetime.now(NEW_YORK).date(),
            reports_dir=REPORTS_DIR,
        )
        typer.echo(f"run {run.id}: {'PASS' if run.passed else 'FAIL'}")
        for name, row in run.pass_bar.items():
            mark = "ok" if row["passed"] else "--"
            typer.echo(f"  {mark} {name}: {row['value']:.3f} vs {row['threshold']:.3f}")
    typer.echo(f"report: {path}")


@backtest_app.command("show")
def backtest_show(
    run_id: Annotated[str, typer.Argument(help="Run id printed by `backtest run`.")],
) -> None:
    try:
        key = uuid.UUID(run_id)
    except ValueError:
        typer.echo(f"Not a run id: {run_id}", err=True)
        raise typer.Exit(1) from None
    with get_session() as session:
        run = session.get(BacktestRun, key)
        if run is None:
            typer.echo(f"No backtest run {run_id}", err=True)
            raise typer.Exit(1)
        typer.echo(render_report(run), nl=False)
```

mypy infers `setup.value` as the literal union of the enum values, so no `cast` is needed for `RunSetup` / `JevMode`.

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_cli.py -v && uv run mypy src && uv run ruff check . && uv run signalbench backtest --help`
Expected: 15 passed; mypy and ruff clean; the help lists `run` and `show`.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/cli.py tests/test_cli.py
git commit -m "feat: backtest run and show commands with the pre-registration guard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 20: Spread survey cost, `backtest cost`, and docs

**Files:**
- Create: `src/signalbench/strategy/spread.py`, `tests/test_spread.py`, `tests/test_strategy_v1_file.py`
- Modify: `src/signalbench/cli.py`, `tests/test_cli.py`, `README.md`, `docs/superpowers/specs/2026-09-22-swing-assistant-02-strategy-backtest-design.md`

This task builds the loader only. It reads the survey format the owner already has in `data/cdr_spread_survey.yaml` (a top-level `readings:` list of `{cdr_symbol, observed_at, bid, ask, bid_size, ask_size, note}`), but the tests use temporary files. **Do not edit or stage `data/cdr_spread_survey.yaml`.**

- [ ] **Step 1: Write the failing tests**

`tests/test_spread.py`:

```python
from pathlib import Path

import pytest
from pytest import approx

from signalbench.strategy.spread import (
    SpreadSurveyError,
    cost_from_median_spread,
    load_spread_survey,
)


def _survey(tmp_path: Path, rows: list[tuple[str, float | None, float | None]]) -> Path:
    lines = ["readings:"]
    for symbol, bid, ask in rows:
        lines += [
            f"  - cdr_symbol: {symbol}",
            "    observed_at: 2026-09-24T10:45-07:00",
            f"    bid: {'' if bid is None else bid}",
            f"    ask: {'' if ask is None else ask}",
            "    bid_size:",
            "    ask_size:",
            "    note:",
        ]
    path = tmp_path / "cdr_spread_survey.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_cost_formula_matches_the_spec_example() -> None:
    assert cost_from_median_spread(0.004) == 0.003  # spec: a 0.4% spread gives 0.3%
    assert cost_from_median_spread(0.001) == 0.002  # the 0.2% floor
    assert cost_from_median_spread(0.01) == 0.006


def test_median_of_complete_rows_sets_the_cost(tmp_path: Path) -> None:
    path = _survey(tmp_path, [
        ("ZNVD", 99.9, 100.1),  # 0.2%
        ("ZMSF", 99.8, 100.2),  # 0.4%
        ("ZAAP", 99.8, 100.2),  # 0.4%
        ("ZAMZ", 99.7, 100.3),  # 0.6%
        ("ZGOO", 99.5, 100.5),  # 1.0%
        ("ZMET", None, None),  # no quote shown: does not count
    ])
    survey = load_spread_survey(path)
    assert len(survey.readings) == 5 and survey.incomplete == 1
    assert survey.readings[0].spread_pct == approx(0.002)
    assert survey.median_spread == approx(0.004)
    assert survey.cost_per_side == 0.003


def test_median_of_exactly_one_percent_is_allowed(tmp_path: Path) -> None:
    path = _survey(tmp_path, [(f"Z{i}", 99.5, 100.5) for i in range(5)])
    assert load_spread_survey(path).cost_per_side == 0.006


def test_median_above_one_percent_stops(tmp_path: Path) -> None:
    path = _survey(tmp_path, [(f"Z{i}", 99.0, 101.0) for i in range(5)])  # 2%
    with pytest.raises(SpreadSurveyError, match="above 1%"):
        load_spread_survey(path)


def test_fewer_than_five_complete_rows_stops(tmp_path: Path) -> None:
    rows: list[tuple[str, float | None, float | None]] = [(f"Z{i}", 99.9, 100.1) for i in range(4)]
    rows.append(("ZBID", 99.9, None))  # bid without ask does not count
    with pytest.raises(SpreadSurveyError, match="has 4 readings with both bid and ask"):
        load_spread_survey(_survey(tmp_path, rows))


def test_the_empty_template_stops(tmp_path: Path) -> None:
    path = _survey(tmp_path, [(name, None, None) for name in ("ZNVD", "ZMSF", "ZAAP", "ZAMZ", "ZGOO")])
    with pytest.raises(SpreadSurveyError, match="has 0 readings"):
        load_spread_survey(path)


def test_ask_below_bid_is_rejected(tmp_path: Path) -> None:
    path = _survey(tmp_path, [("ZNVD", 100.2, 99.8)] + [(f"Z{i}", 99.9, 100.1) for i in range(5)])
    with pytest.raises(SpreadSurveyError, match="ZNVD: need 0 < bid <= ask"):
        load_spread_survey(path)
```

`tests/test_strategy_v1_file.py` (skips until Task 21 creates `data/strategy_v1.yaml`):

```python
"""The pre-registered config must carry the cost computed from the committed spread survey."""

from pathlib import Path

import pytest

from signalbench.strategy.config import load_strategy_config
from signalbench.strategy.spread import load_spread_survey

DATA = Path(__file__).resolve().parents[1] / "data"
STRATEGY_V1 = DATA / "strategy_v1.yaml"
SURVEY = DATA / "cdr_spread_survey.yaml"


def test_strategy_v1_cost_matches_the_spread_survey() -> None:
    if not STRATEGY_V1.exists():
        pytest.skip(
            "data/strategy_v1.yaml is not committed yet; it is created after the owner "
            "fills and approves data/cdr_spread_survey.yaml (spec 02 pre-registration)"
        )
    config, _ = load_strategy_config(STRATEGY_V1)
    assert config.version == "v1"
    assert config.cost_per_side == load_spread_survey(SURVEY).cost_per_side
```

Append to `tests/test_cli.py`:

```python
def test_backtest_cost_prints_the_cost_per_side(tmp_path: Path) -> None:
    survey = tmp_path / "survey.yaml"
    rows = "".join(
        f"  - {{cdr_symbol: Z{i}, bid: 99.8, ask: 100.2}}\n" for i in range(5)
    )
    survey.write_text("readings:\n" + rows + "  - {cdr_symbol: ZMET, bid: , ask: }\n", encoding="utf-8")
    result = runner.invoke(app, ["backtest", "cost", "--survey", str(survey)])
    assert result.exit_code == 0, result.stderr
    assert "complete readings: 5 (incomplete: 1)" in result.stdout
    assert "median spread: 0.400%" in result.stdout
    assert "cost_per_side: 0.003" in result.stdout


def test_backtest_cost_refuses_an_unfilled_survey(tmp_path: Path) -> None:
    survey = tmp_path / "survey.yaml"
    survey.write_text("readings:\n  - {cdr_symbol: ZNVD, bid: , ask: }\n", encoding="utf-8")
    result = runner.invoke(app, ["backtest", "cost", "--survey", str(survey)])
    assert result.exit_code == 1
    assert "has 0 readings with both bid and ask" in result.stderr
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_spread.py tests/test_strategy_v1_file.py tests/test_cli.py -v`
Expected: FAIL. Collection stops with `ModuleNotFoundError: No module named 'signalbench.strategy.spread'` (both new test files import it). Run alone, `uv run pytest tests/test_cli.py -v` shows the two cost tests failing (`No such command 'cost'`).

- [ ] **Step 3: Implement the loader**

`src/signalbench/strategy/spread.py`:

```python
"""The CDR spread survey sets the backtest cost per side (overview, "Spread survey")."""

from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, cast

import yaml

MIN_COMPLETE_READINGS = 5
MAX_MEDIAN_SPREAD = 0.01
COST_FLOOR = 0.002
COST_ABOVE_HALF_SPREAD = 0.001


class SpreadSurveyError(ValueError):
    pass


@dataclass(frozen=True)
class SpreadReading:
    cdr_symbol: str
    bid: float
    ask: float

    @property
    def spread_pct(self) -> float:
        """(ask - bid) / midpoint."""
        return (self.ask - self.bid) / ((self.ask + self.bid) / 2.0)


@dataclass(frozen=True)
class SpreadSurvey:
    readings: tuple[SpreadReading, ...]
    incomplete: int  # rows without both bid and ask; they do not count
    median_spread: float
    cost_per_side: float


def cost_from_median_spread(median_spread: float) -> float:
    """max(0.2%, median / 2 + 0.1%), rounded to 6 decimals for the YAML file."""
    return round(max(COST_FLOOR, median_spread / 2.0 + COST_ABOVE_HALF_SPREAD), 6)


def _price(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SpreadSurveyError(f"{row.get('cdr_symbol')}: {key} must be a number, got {value!r}")
    return float(value)


def load_spread_survey(path: Path) -> SpreadSurvey:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = raw.get("readings") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise SpreadSurveyError(f"{path.name}: expected a top-level `readings:` list")
    readings: list[SpreadReading] = []
    incomplete = 0
    for item in rows:
        row = cast(dict[str, Any], item)
        bid, ask = _price(row, "bid"), _price(row, "ask")
        if bid is None or ask is None:
            incomplete += 1
            continue
        if bid <= 0.0 or ask < bid:
            raise SpreadSurveyError(f"{row.get('cdr_symbol')}: need 0 < bid <= ask, got {bid}/{ask}")
        readings.append(SpreadReading(str(row["cdr_symbol"]), bid, ask))
    if len(readings) < MIN_COMPLETE_READINGS:
        raise SpreadSurveyError(
            f"{path.name} has {len(readings)} readings with both bid and ask; "
            f"at least {MIN_COMPLETE_READINGS} are needed. Record them during market hours first."
        )
    spread = float(median(reading.spread_pct for reading in readings))
    if spread > MAX_MEDIAN_SPREAD:
        raise SpreadSurveyError(
            f"Median CDR spread is {spread:.2%}, above 1%. Stop and revisit the instrument "
            "choice before any backtest (overview, Spread survey)."
        )
    return SpreadSurvey(
        readings=tuple(readings),
        incomplete=incomplete,
        median_spread=spread,
        cost_per_side=cost_from_median_spread(spread),
    )
```

- [ ] **Step 4: Add `backtest cost`**

In `src/signalbench/cli.py`, add `from signalbench.strategy.spread import SpreadSurveyError, load_spread_survey` after the `load_strategy_config` import, add `SPREAD_SURVEY_PATH = REPO_ROOT / "data" / "cdr_spread_survey.yaml"` after `STRATEGY_V1_PATH`, and append:

```python
@backtest_app.command("cost")
def backtest_cost(
    survey: Annotated[
        Path, typer.Option("--survey", help="The CDR bid/ask survey.")
    ] = SPREAD_SURVEY_PATH,
) -> None:
    try:
        result = load_spread_survey(survey)
    except (SpreadSurveyError, FileNotFoundError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    for reading in result.readings:
        typer.echo(
            f"{reading.cdr_symbol}: bid {reading.bid} ask {reading.ask} "
            f"spread {reading.spread_pct:.3%}"
        )
    typer.echo(f"complete readings: {len(result.readings)} (incomplete: {result.incomplete})")
    typer.echo(f"median spread: {result.median_spread:.3%}")
    typer.echo(f"cost_per_side: {result.cost_per_side}")
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: everything passes, with exactly 1 skipped (`test_strategy_v1_cost_matches_the_spread_survey`); ruff and mypy clean.

Run: `uv run signalbench backtest cost`
Expected, while the survey is still the empty template: exit code 1 and `cdr_spread_survey.yaml has 0 readings with both bid and ask; at least 5 are needed. …`. That refusal is correct.

- [ ] **Step 6: Update `README.md`**

- **CLI reference:** add three rows after `ingest stats`:

```markdown
| `uv run signalbench backtest cost [--survey PATH]` | Median CDR spread from `data/cdr_spread_survey.yaml` and the resulting cost per side |
| `uv run signalbench backtest run --setup {pullback,breakout,combined} [--jev off] [--config PATH]` | Simulate on stored data with the committed `data/strategy_v1.yaml`, store the run, and write `reports/backtests/<date>-<setup>-<jev>.md`. `sentiment` and `--jev filter` need spec 03 |
| `uv run signalbench backtest show RUN_ID` | Reprint a stored run's report |
```

- **Project layout:** change the `backtest/` line to `Simulator, run metrics, benchmarks, pass bar, reports, and the backtest runner (spec 02)`; add `strategy/      Pre-registered config, indicators, market view, and the pure decide() (spec 02)` after it; change the `market/` line to `Adjusted OHLC bars and the NYSE session calendar`; change the `data/` line to `Checked-in CDR universe, spread survey, and strategy parameters`; add `reports/            Committed backtest reports` after `data/`.
- **Limitations and scope:** replace the first bullet ("There is no strategy, backtest, or trading signal yet …") with: `The strategy and backtest exist (spec 02), but nothing sends signals yet; live signals arrive with the bot in spec 05. Every backtest report prints its caveats: survivorship, US prices standing in for CDR prices, realized earnings dates, and US-only liquidity.`

- [ ] **Step 7: Record the implementation choices in spec 02**

Append to the Changelog of `docs/superpowers/specs/2026-09-22-swing-assistant-02-strategy-backtest-design.md`:

```markdown
- 2026-09-24: implementation choices (plan `2026-09-24-swing-02-strategy-backtest.md`):
  - `backtest_runs` is migration `0010`, because spec 01 used `0009` for `raw_documents.form`. Specs 03–05 shift to `0011`–`0013`.
  - Extra skip reasons: `held`, `no_cash`, and `no_bar`. Symbols that fail the liquidity filter are ignored without a skip record. Gate order: held, regime, paused, earnings_blackout, blocked.
  - `EntryOrder.risk_amount` is the risk after the caps. If the open plus cost would spend more than the cash left, the entry is trimmed to the cash and logged as `trimmed`.
  - Halves and the recent sample are split by entry date. `cost_per_side` is rounded to 6 decimals. `config_sha256` hashes the file with CRLF normalised to LF.
  - `backtest run` refuses a config that is not committed and unchanged in git. `git_sha` carries `-dirty` when tracked code or data files have uncommitted changes.
  - The trade log also records `stop_update` and `exit_deferred` events.
```

- [ ] **Step 8: Commit**

```bash
git add src/signalbench/strategy/spread.py src/signalbench/cli.py tests/test_spread.py tests/test_strategy_v1_file.py tests/test_cli.py README.md docs/superpowers/specs/2026-09-22-swing-assistant-02-strategy-backtest-design.md
git commit -m "feat: spread survey cost per side, backtest cost command, and docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

> ## ⛔ Controller: stop here until the owner has filled and approved the spread survey
>
> Tasks 21 and 22 use real data and fix the pre-registered parameters. Do not start them, and do not run `signalbench backtest run` against the real database, until the owner says in chat that `data/cdr_spread_survey.yaml` is filled (at least 5 CDRs with bid and ask, recorded during market hours) and approved. Report the Task 1–20 results and wait.

---

### Task 21: ⛔ Pre-register `data/strategy_v1.yaml`

**Files:**
- Commit (owner's file): `data/cdr_spread_survey.yaml`
- Create: `data/strategy_v1.yaml`

**Gated:** start only after the owner's go-ahead above.

- [ ] **Step 1: Check the survey**

Run: `uv run signalbench backtest cost`
Expected: one line per complete reading, then `complete readings: N` with N ≥ 5, `median spread: …`, and `cost_per_side: …`. If it exits 1 with "above 1%", **stop**: the overview says to revisit the instrument choice before any backtest. Report that to the owner and do nothing else.

- [ ] **Step 2: Commit the survey on its own**

Show the owner `git diff --no-index /dev/null data/cdr_spread_survey.yaml` (or the file) and the `backtest cost` output, and ask for explicit approval to commit the survey as they left it. Then:

```bash
git add data/cdr_spread_survey.yaml
git commit -m "data: CDR bid/ask spread survey (sets the backtest cost per side)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 3: Write `data/strategy_v1.yaml`**

The file is the test fixture with three differences: the header comment, `version: v1`, and the surveyed cost. Replace `<COST>` with the exact `cost_per_side` value that Step 1 printed (for example `0.003`). No other value may differ from `tests/fixtures/strategy_test.yaml`.

```yaml
# Pre-registered v1 parameters (spec 02). Committed before the first backtest on real data.
# Do not edit: any change is a new version (strategy_v2.yaml), and its reports are post-hoc.
# cost_per_side comes from data/cdr_spread_survey.yaml via `signalbench backtest cost`.
version: v1
start_equity: 100
risk_pct: 0.02
max_positions: 3
max_per_sector: 2
pause_drawdown: 0.15
auto_resume_sessions: 10
gap_up_limit: 0.01
cost_per_side: <COST>
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

- [ ] **Step 4: Verify the file**

Run: `uv run pytest tests/test_strategy_v1_file.py -v -rs`
Expected: 1 passed (no longer skipped).

Run: `git diff --no-index tests/fixtures/strategy_test.yaml data/strategy_v1.yaml`
Expected: only the header comment lines, `version`, and `cost_per_side` differ.

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: all green, 0 skipped.

- [ ] **Step 5: Commit it alone, before any real run**

```bash
git add data/strategy_v1.yaml
git commit -m "data: pre-register strategy v1 parameters

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 22: ⛔ Real v1 runs and the owner's go/no-go

**Files:**
- Create (generated): `reports/backtests/<YYYY-MM-DD>-pullback-off.md`, `reports/backtests/<YYYY-MM-DD>-breakout-off.md` (and `-combined-off.md` only if both pass)
- Modify (owner's decision): `docs/superpowers/specs/2026-09-22-swing-assistant-00-overview.md` (changelog)

**Gated:** Task 21 must be committed first. This task uses local Postgres and the data from spec 01.

- [ ] **Step 1: Database and data**

Run: `docker compose up -d && uv run alembic upgrade head`
Expected: upgrades through `0010_backtest_runs`.

Run: `uv run signalbench ingest prices && uv run signalbench ingest earnings`
Expected: per-ticker lines and no failures. Do not ingest again between the runs below, so they share one `data_fingerprint`.

- [ ] **Step 2: Confirm the ordering in git**

Run: `git status --short --untracked-files=no -- src data alembic pyproject.toml uv.lock`
Expected: no output (so `git_sha` has no `-dirty` suffix). `.env.example` may still show as modified elsewhere; it is not in those paths.

Run: `git log --oneline -1 -- data/strategy_v1.yaml`
Expected: the Task 21 commit. It must be an ancestor of `HEAD`, which is the `git_sha` every run records.

- [ ] **Step 3: Run Pullback and Breakout with Jev off**

Run: `uv run signalbench backtest run --setup pullback`
Expected: `run <uuid>: PASS` or `FAIL`, one line per criterion, and `report: …/reports/backtests/<date>-pullback-off.md`.

Run: `uv run signalbench backtest run --setup breakout`
Expected: the same for breakout.

Open both reports. Check that `config_sha256` and `data_fingerprint` match between them, that `git_sha` has no `-dirty`, that the session range starts at the first session of 2012, and that the caveats section is present. **Do not change any parameter, whatever the result.**

- [ ] **Step 4: Combined run, only if both passed**

If both reports say PASS, run `uv run signalbench backtest run --setup combined`. It is information only. If only one passed, skip this step: the combined set of passing setups is that setup alone.

- [ ] **Step 5: Commit the reports**

```bash
git add reports/backtests/<date>-pullback-off.md reports/backtests/<date>-breakout-off.md
git commit -m "docs: v1 pass-bar reports for pullback and breakout (Jev off)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(Add the combined report to the same commit if Step 4 ran.)

- [ ] **Step 6: ⛔ Owner go/no-go**

Send the owner both report paths with a two-line summary each (pass/fail per criterion, mean R, trades, Sharpe vs QQQ). The owner reads them and decides. Write their decision, in their words, as a new line in the overview's Changelog, e.g. `- <date>: spec 02 go/no-go — pullback PASS/FAIL, breakout PASS/FAIL; live setups: …; decision: …`, then commit it:

```bash
git add docs/superpowers/specs/2026-09-22-swing-assistant-00-overview.md
git commit -m "docs: record the spec 02 go/no-go decision

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

If both setups fail: stop before specs 04–05 unless spec 03's Sentiment setup passes (spec 02 Gate). Ask the owner before pushing the branch or opening a PR.

---

## Spec coverage check

| Spec 02 requirement | Task |
| --- | --- |
| Pre-registration: spread survey committed first; it sets `cost_per_side` | 20, 21 |
| All parameters in `data/strategy_v1.yaml`, committed before the first real run | 2, 21, 22 (and the guard in 19) |
| Each run records `config_sha256`, `git_sha`, `data_fingerprint` | 2, 14, 17, 18 |
| v1 report decides; v2 reports labeled post-hoc | 16, 22 |
| Adjusted US OHLC, causal indicators at the as-of close | 3, 5 |
| Regime: QQQ close > SMA200 or no entries | 5, 8, 9 |
| Earnings blackout D+1…D+3 | 5, 9 |
| No entry on a held or pending symbol | 4, 8, 9 |
| One signal per symbol by `setup_priority` | 6 |
| Liquidity `active` as of the date (backtest: US median traded value) | 5, 9 |
| Pullback, Breakout, Sentiment trend/trigger/stop/target/time rules | 6, 7 |
| Breakout trailing stop that only ratchets up | 7, 9, 11 |
| Signal at close D → entry at the open of D+1; `gap_up` and `gap_below_stop` skips | 10 |
| R from the entry fill; 2R target from the fill | 10, 11 |
| Exit order stop → earnings → target → time; fill at next open; entry day = session 1 | 7, 10, 11 |
| Cost per side on buys and sells | 10, 20 |
| Equity, 2% risk units, equity/3 cap, uncommitted-cash cap, fractional units | 8, 10 |
| 3 slots, 2 per sector, ranking (catalyst, traded value, symbol) and skip reasons | 4, 8 |
| Pause below 0.85 × peak; auto-resume after 10 sessions with peak reset | 10 |
| `decide()` signature, purity, `Decision` fields | 4, 9 |
| `MarketView` precompute; `at(as_of)` raises `LookAheadError` on future access | 5, 9 |
| `ReadingsView` port with `NullReadingsView` | 4 |
| Simulator order (open fills, close mark/peak/pause/decide), start equity 100, trade log | 10 |
| Backtest universe = US tickers with a CDR; both simplifications stated | 16, 18 |
| Per-run metrics, halves, skips by reason, pauses, recent (2026-09-15) stats | 12 |
| QQQ buy-and-hold and survivor benchmark | 13, 18 |
| Pass bar, four criteria with exact inequalities | 13 |
| Combined run is information only | 16, 18, 22 |
| Report caveats | 16 |
| `backtest_runs` table (as `0010`) | 15 |
| `data_fingerprint` reusing the fingerprint helpers | 14 |
| CLI `backtest run` / `backtest show`; report file under `reports/backtests/` | 16, 18, 19 |
| Tests: indicators vs hand values | 3 |
| Tests: fixtures per exit reason, trailing stop never loosens | 11 |
| Tests: truncated = full `decide()`; `LookAheadError` on direct access | 5, 9 |
| Tests: each skip reason | 8 (no_slot, sector_cap, no_cash), 9 (regime, paused, earnings_blackout, held, blocked), 10 (gap_up, gap_below_stop) |
| Tests: sizing (risk units, equity/3 cap, cash cap) | 8 |
| Tests: pause and auto-resume with peak reset | 10 |
| Tests: determinism of metrics and fingerprints | 9, 14 |
| Tests: pass bar at exactly each threshold | 13 |
| Gate: CI green; v1 committed first; reports committed; owner go/no-go | 20, 21, 22 |
