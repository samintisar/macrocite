# Phase 3 — Backtest Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Models:** cheap implementers `composer-2.5`; mid-tier implementers, reviewers, and fix loops `cursor-grok-4.6-high`. Never `*-fast` models. See [docs/superpowers/README.md](../README.md).

**Goal:** Run one long-only sentiment-threshold strategy on stored signals + daily `adj_close` and persist deterministic metrics versus buy-and-hold.

**Architecture:** Trades are as-of `raw_documents.published_at` (next trading day on or after), never `signals.extracted_at`. Prices used for PnL are `adj_close`. Each run stores `config_id`, date range, `model_version`, `prompt_version`, and `signal_set_fingerprint` (SHA-256 of sorted signal IDs). Same inputs → identical metrics dict.

**Tech Stack:** numpy (and vectorbt if it installs cleanly on Windows CI; if vectorbt import fails in tests, implement Sharpe/drawdown/win-rate/total-return in `src/signalbench/backtest/metrics.py` with numpy only — do not block the phase on vectorbt). SQLModel, Typer, pytest.

**Depends on:** Phase 2 gate green. Can run on fixture signals+prices without the full label set.

**Out of scope:** live trading, parameter sweep (Phase 5), dashboard.

---

## File map

- Modify: `src/signalbench/db/models.py` — `BacktestConfig`, `BacktestRun`
- Create: `alembic/versions/0004_backtests.py`
- Create: `src/signalbench/backtest/fingerprint.py`
- Create: `src/signalbench/backtest/strategy.py` — trades from signals + prices
- Create: `src/signalbench/backtest/metrics.py`
- Create: `src/signalbench/backtest/run.py` — `run_backtest`
- Create: `data/backtest_config.yaml`
- Modify: `src/signalbench/cli.py` — `backtest` command
- Create: `tests/test_backtest_schema.py`, `tests/test_strategy.py`, `tests/test_metrics.py`, `tests/test_determinism.py`, `tests/test_lookahead.py`, `tests/test_backtest_cli.py`

---

### Task 1: Config and run tables

**Files:**
- Modify: `src/signalbench/db/models.py`
- Create: `tests/test_backtest_schema.py`

```python
class BacktestConfig(SQLModel, table=True):
    __tablename__ = "backtest_configs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    strategy_type: str
    params: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class BacktestRun(SQLModel, table=True):
    __tablename__ = "backtest_runs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    config_id: uuid.UUID = Field(foreign_key="backtest_configs.id", ondelete="RESTRICT")
    ticker_ids: list[str] = Field(sa_column=Column(JSON, nullable=False))
    start_date: date
    end_date: date
    model_version: str
    prompt_version: str
    signal_set_fingerprint: str
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    win_rate: float | None = None
    total_return: float | None = None
    benchmark_return: float | None = None
    trade_log: list | None = Field(default=None, sa_column=Column(JSON))
    run_at: datetime = Field(default_factory=utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
```

Store `ticker_ids` as JSON list of UUID strings (bounded ~20 names). `signal_set_fingerprint` is required (`nullable=False`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_schema.py
from datetime import date

import pytest
from pydantic import ValidationError
from sqlmodel import Session

from signalbench.db.models import BacktestConfig, BacktestRun


def test_fingerprint_required(session: Session) -> None:
    cfg = BacktestConfig(name="mvp", strategy_type="sentiment_threshold_long", params={"sentiment_threshold": 0.5, "holding_days": 5})
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    with pytest.raises((ValidationError, TypeError)):
        BacktestRun(
            config_id=cfg.id,
            ticker_ids=[str(cfg.id)],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
            prompt_version="v1",
            # fingerprint omitted
        )
```

If SQLModel allows omitting with a default, do **not** give a default. Test that constructing without `signal_set_fingerprint` raises.

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_backtest_schema.py -v
```

Verify: `uv run pytest tests/test_backtest_schema.py -v`
Expect: FAIL missing models.

- [ ] **Step 3: Add models + Alembic `0004_backtests.py`**

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_backtest_schema.py -v
```

Verify: same command
Expect: fingerprint required; FK to config works.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/db/models.py alembic/versions/0004_backtests.py tests/test_backtest_schema.py
git commit -m "feat: add backtest config and run tables with fingerprint"
```

---

### Task 2: Strategy trades from fixture series

**Files:**
- Create: `src/signalbench/backtest/strategy.py`
- Create: `tests/test_strategy.py`

Rules (MVP):

- One ticker at a time.
- Entry: first session **on or after** `published_at.date()` where `adj_close` exists and signal `sentiment > threshold`.
- Exit: after `holding_days` trading sessions, or if stop_loss / take_profit hit on `adj_close` vs entry (stop_loss `-0.03`, take_profit `None` unless set).
- No overlapping position on that ticker.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_strategy.py
from datetime import date, datetime, timezone
from decimal import Decimal

from signalbench.backtest.strategy import Trade, build_trades


def test_known_trade_list() -> None:
    prices = [
        (date(2024, 1, 2), Decimal("100")),
        (date(2024, 1, 3), Decimal("101")),
        (date(2024, 1, 4), Decimal("102")),
        (date(2024, 1, 5), Decimal("103")),
        (date(2024, 1, 8), Decimal("104")),
    ]
    signals = [
        {
            "sentiment": 0.8,
            "published_at": datetime(2024, 1, 2, 15, 0, tzinfo=timezone.utc),
        }
    ]
    trades = build_trades(
        prices=prices,
        signals=signals,
        sentiment_threshold=0.5,
        holding_days=3,
        stop_loss=None,
        take_profit=None,
    )
    assert trades == [
        Trade(entry_date=date(2024, 1, 2), exit_date=date(2024, 1, 5), entry_price=Decimal("100"), exit_price=Decimal("103"))
    ]
```

Use `adj_close` as the price tuple’s second element.

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_strategy.py -v
```

Verify: `uv run pytest tests/test_strategy.py -v`
Expect: FAIL import `build_trades`.

- [ ] **Step 3: Implement `Trade` dataclass and `build_trades`**

Count holding_days as trading days in the `prices` list, not calendar days.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_strategy.py -v
```

Verify: same command
Expect: exact `Trade` list.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/strategy.py tests/test_strategy.py
git commit -m "feat: build long trades from sentiment threshold"
```

---

### Task 3: Metrics vs buy-and-hold

**Files:**
- Create: `src/signalbench/backtest/metrics.py`
- Create: `tests/test_metrics.py`

Equity curve: start at 1.0, apply trade returns sequentially (fully invested per trade, flat otherwise). Sharpe: daily excess return vs 0, `sqrt(252) * mean / std` (sample std, 0 if std=0). Max drawdown: min of `(peak - value) / peak`. Win rate: fraction of trades with `exit > entry`. Total return: final equity - 1. Benchmark: buy-and-hold `adj_close` first to last in window.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metrics.py
from datetime import date
from decimal import Decimal

from signalbench.backtest.metrics import compute_metrics
from signalbench.backtest.strategy import Trade


def test_golden_metrics() -> None:
    prices = [
        (date(2024, 1, 2), Decimal("100")),
        (date(2024, 1, 3), Decimal("100")),
        (date(2024, 1, 4), Decimal("110")),
        (date(2024, 1, 5), Decimal("110")),
    ]
    trades = [
        Trade(
            entry_date=date(2024, 1, 2),
            exit_date=date(2024, 1, 4),
            entry_price=Decimal("100"),
            exit_price=Decimal("110"),
        )
    ]
    m = compute_metrics(prices, trades)
    assert m.total_return == 0.10
    assert m.win_rate == 1.0
    assert m.benchmark_return == 0.10
    assert m.max_drawdown == 0.0
    assert isinstance(m.sharpe_ratio, float)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_metrics.py -v
```

Verify: `uv run pytest tests/test_metrics.py -v`
Expect: FAIL import.

- [ ] **Step 3: Implement `compute_metrics` in numpy. Prefer vectorbt only if a one-liner maps to the same numbers; tests lock numpy results.**

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_metrics.py -v
```

Verify: same command
Expect: golden total_return / win_rate / benchmark / drawdown.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/metrics.py tests/test_metrics.py
git commit -m "feat: backtest Sharpe drawdown win-rate vs buy-and-hold"
```

---

### Task 4: Determinism

**Files:**
- Create: `src/signalbench/backtest/run.py`
- Create: `src/signalbench/backtest/fingerprint.py`
- Create: `tests/test_determinism.py`

```python
def signal_set_fingerprint(signal_ids: list[str]) -> str:
    import hashlib

    joined = ",".join(sorted(signal_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
```

`run_backtest` returns a metrics dict (JSON-serializable floats). Call twice.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_determinism.py
from datetime import date
from decimal import Decimal

from signalbench.backtest.fingerprint import signal_set_fingerprint
from signalbench.backtest.run import metrics_as_json
from signalbench.backtest.strategy import Trade


def test_fingerprint_stable() -> None:
    a = signal_set_fingerprint(["b", "a"])
    b = signal_set_fingerprint(["a", "b"])
    assert a == b
    assert len(a) == 64


def test_two_run_metrics_equal() -> None:
    prices = [
        (date(2024, 1, 2), Decimal("100")),
        (date(2024, 1, 3), Decimal("100")),
        (date(2024, 1, 4), Decimal("110")),
        (date(2024, 1, 5), Decimal("110")),
    ]
    trades = [
        Trade(
            entry_date=date(2024, 1, 2),
            exit_date=date(2024, 1, 4),
            entry_price=Decimal("100"),
            exit_price=Decimal("110"),
        )
    ]
    m1 = metrics_as_json(prices, trades)
    m2 = metrics_as_json(prices, trades)
    assert m1 == m2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_determinism.py -v
```

Verify: `uv run pytest tests/test_determinism.py -v`
Expect: FAIL missing functions.

- [ ] **Step 3: Implement fingerprint + `metrics_as_json` (round floats to 10 decimal places for stable JSON).**

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_determinism.py -v
```

Verify: same command
Expect: two `run()` metrics dicts equal.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/run.py src/signalbench/backtest/fingerprint.py tests/test_determinism.py
git commit -m "feat: fingerprint signal sets and lock deterministic metrics"
```

---

### Task 5: Look-ahead guard

**Files:**
- Create: `tests/test_lookahead.py`
- Modify: `src/signalbench/backtest/run.py` — join path uses `published_at`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lookahead.py
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlmodel import Session

from signalbench.db.models import EventType, Price, RawDocument, Signal, Ticker
from signalbench.backtest.run import run_backtest_for_ticker


def test_extracted_in_2026_trades_asof_2022(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    doc = RawDocument(
        source="sec_edgar",
        external_id="old-8k",
        doc_type="eight_k",
        raw_text="beat",
        published_at=datetime(2022, 6, 1, tzinfo=timezone.utc),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    session.add(
        Signal(
            document_id=doc.id,
            ticker_id=ticker.id,
            model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
            prompt_version="v1",
            sentiment=0.9,
            event_type=EventType.earnings,
            confidence=0.9,
            rationale="beat",
            extracted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    session.add(
        Price(
            ticker_id=ticker.id,
            date=date(2022, 6, 1),
            open=Decimal("10"),
            high=Decimal("10"),
            low=Decimal("10"),
            close=Decimal("10"),
            adj_close=Decimal("10"),
            volume=1,
        )
    )
    session.add(
        Price(
            ticker_id=ticker.id,
            date=date(2022, 6, 8),
            open=Decimal("11"),
            high=Decimal("11"),
            low=Decimal("11"),
            close=Decimal("11"),
            adj_close=Decimal("11"),
            volume=1,
        )
    )
    session.commit()
    trades = run_backtest_for_ticker(
        session,
        ticker_id=ticker.id,
        sentiment_threshold=0.5,
        holding_days=1,
        model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
        prompt_version="v1",
    )
    assert trades[0].entry_date == date(2022, 6, 1)
    assert trades[0].entry_date.year != 2026
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_lookahead.py -v
```

Verify: `uv run pytest tests/test_lookahead.py -v`
Expect: FAIL if implementation uses `extracted_at` for entry.

- [ ] **Step 3: `run_backtest_for_ticker` loads signals joined to `RawDocument.published_at` only.**

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_lookahead.py -v
```

Verify: same command
Expect: entry year 2022.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/backtest/run.py tests/test_lookahead.py
git commit -m "fix: backtest as-of document published_at not extract time"
```

---

### Task 6: CLI

**Files:**
- Create: `data/backtest_config.yaml`
- Modify: `src/signalbench/cli.py`
- Create: `tests/test_backtest_cli.py`

```yaml
# data/backtest_config.yaml
name: mvp_long
strategy_type: sentiment_threshold_long
params:
  sentiment_threshold: 0.5
  holding_days: 5
  stop_loss: -0.03
```

- [ ] **Step 1: Write the failing test**

```python
from typer.testing import CliRunner
from signalbench.cli import app

def test_backtest_help() -> None:
    result = CliRunner().invoke(app, ["backtest", "--help"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_backtest_cli.py -v
```

Verify: `uv run pytest tests/test_backtest_cli.py -v`
Expect: FAIL no `backtest` command.

- [ ] **Step 3: Implement `backtest` CLI plus `persist_run`**

`persist_run(session, ticker, params) -> BacktestRun` upserts `BacktestConfig` by `name`, calls `run_backtest_for_ticker`, inserts `BacktestRun` with fingerprint + metrics.

Add this test to `tests/test_backtest_cli.py` (full constructors, not abbreviated):

```python
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlmodel import Session

from signalbench.backtest.run import persist_run
from signalbench.db.models import EventType, Price, RawDocument, Signal, Ticker


def test_second_run_matches_fingerprint(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    doc = RawDocument(
        source="sec_edgar",
        external_id="cli-bt",
        doc_type="eight_k",
        raw_text="beat",
        published_at=datetime(2022, 6, 1, tzinfo=timezone.utc),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    session.add(
        Signal(
            document_id=doc.id,
            ticker_id=ticker.id,
            model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
            prompt_version="v1",
            sentiment=0.9,
            event_type=EventType.earnings,
            confidence=0.9,
            rationale="beat",
        )
    )
    session.add(
        Price(
            ticker_id=ticker.id,
            date=date(2022, 6, 1),
            open=Decimal("10"),
            high=Decimal("10"),
            low=Decimal("10"),
            close=Decimal("10"),
            adj_close=Decimal("10"),
            volume=1,
        )
    )
    session.add(
        Price(
            ticker_id=ticker.id,
            date=date(2022, 6, 8),
            open=Decimal("11"),
            high=Decimal("11"),
            low=Decimal("11"),
            close=Decimal("11"),
            adj_close=Decimal("11"),
            volume=1,
        )
    )
    session.commit()
    params = {"sentiment_threshold": 0.5, "holding_days": 1, "name": "mvp"}
    r1 = persist_run(session, ticker_id=ticker.id, params=params, model_version="deepseek-ai/DeepSeek-V4-Flash-0731", prompt_version="v1")
    r2 = persist_run(session, ticker_id=ticker.id, params=params, model_version="deepseek-ai/DeepSeek-V4-Flash-0731", prompt_version="v1")
    assert r1.signal_set_fingerprint == r2.signal_set_fingerprint
    assert r1.sharpe_ratio == r2.sharpe_ratio
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_backtest_cli.py tests/test_determinism.py tests/test_lookahead.py -v
```

Verify: that command
Expect: all passed.

- [ ] **Step 5: Commit**

```bash
git add data/backtest_config.yaml src/signalbench/cli.py tests/test_backtest_cli.py
git commit -m "feat: add backtest CLI with reproducible runs"
```

---

## Phase 3 gate

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src tests
```

Verify:

- Two metric computations on frozen fixtures are equal.
- Strategy uses `adj_close`.
- Look-ahead test: 2022 filing extracted in 2026 enters in 2022.
- `BacktestRun` has `model_version`, `prompt_version`, `signal_set_fingerprint`.
- Phase 0–2 tests still pass.
