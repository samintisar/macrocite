# Swing Assistant 01 — Data Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the retired Together sentiment pipeline, then ingest and store everything the swing strategy needs: the US-company CDR universe, long daily price history, upgraded 8-Ks (acceptance time, items, EX-99 exhibits, clean text), earnings dates, and Finnhub news.

**Architecture:** Each data source gets one focused module under `src/signalbench/ingest/`, with network access injected (`httpx.Client`, fetch callables, `RateLimiter`) so every test runs offline on fixtures and in-memory SQLite. `cli.py` stays thin: it wires settings, sessions, and clients to those modules. Schema changes go through two Alembic migrations (`0007` drop legacy tables, `0008` new columns and tables).

**Tech Stack:** Python 3.11, SQLModel/SQLAlchemy, Alembic, httpx, BeautifulSoup + lxml, yfinance, PyYAML, Typer, pytest, ruff, mypy strict.

**Spec:** [`docs/superpowers/specs/2026-09-22-swing-assistant-01-data-foundation-design.md`](../specs/2026-09-22-swing-assistant-01-data-foundation-design.md)

---

## Conventions for every task

- Run commands from the repo root. Use `uv run …` for everything.
- mypy runs strict on `src/` only. In SQLModel `select()`, **wrap `date` and `Decimal` columns in `col()`** (e.g. `select(col(Price.date))`); bare ones fail mypy's overloads. Use `col(X).in_(…)`, `col(X).desc()`, `col(X).is_(None)` for column operators.
- Tests never touch the network. HTTP goes through `httpx.MockTransport`, and yfinance through injected fetch callables.
- After each task: `uv run pytest`, `uv run ruff check .`, and `uv run mypy src` all pass before committing.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Work on a feature branch (`feat/swing-01-data-foundation`), never on `main`.

## File map

| Path | Action | Responsibility |
| --- | --- | --- |
| `docs/personal/paper-trading-playbook.md` | Modify | Closing note for v1.1 |
| `src/signalbench/extraction/`, `src/signalbench/eval/`, `prompts/`, `evals/` | Delete | Retired Together pipeline |
| `src/signalbench/backtest/strategy.py`, `run.py`, `data/backtest_config.yaml` | Delete | Retired sentiment backtest |
| `src/signalbench/backtest/metrics.py` | Modify | Own the `Trade` dataclass (was imported from `strategy.py`) |
| `.github/workflows/eval.yml` → `ci.yml` | Replace | pytest + ruff + mypy on every push and PR |
| `alembic/versions/0007_drop_sentiment_pipeline.py` | Create | Drop legacy tables and the `eventtype` enum |
| `alembic/versions/0008_swing_data_foundation.py` | Create | Ticker kind/links, document acceptance/items/text, `earnings_events` |
| `src/signalbench/config.py` | Modify | New settings; Together settings removed |
| `src/signalbench/db/models.py` | Modify | `TickerKind`, new columns, `EarningsEvent` |
| `src/signalbench/ingest/ratelimit.py` | Create | `RateLimiter` shared by SEC and Finnhub |
| `src/signalbench/ingest/cdr.py` | Create | Cboe JSON parsing, universe YAML, sector mapping |
| `src/signalbench/ingest/seed.py` | Rewrite | Seed tickers from the universe plus benchmarks |
| `src/signalbench/ingest/prices.py` | Rewrite | Start date, incremental upsert, bar validation |
| `src/signalbench/market/__init__.py`, `market/bars.py` | Create | `adjusted_bars()` reader |
| `src/signalbench/ingest/liquidity.py` | Create | Nightly `active` flags |
| `src/signalbench/ingest/text.py` | Create | HTML → clean text; filing text composition |
| `src/signalbench/ingest/edgar.py` | Rewrite | Paging, acceptance time, items, 8-K/A, exhibits, text backfill |
| `src/signalbench/ingest/finnhub.py` | Create | Rate-limited Finnhub client |
| `src/signalbench/ingest/news.py` | Create | Company news ingest |
| `src/signalbench/ingest/earnings.py` | Create | SEC 2.02 events, Finnhub calendar, clustering |
| `src/signalbench/ingest/stats.py` | Create | Counts for the gate |
| `src/signalbench/cli.py` | Rewrite | `universe refresh`, `seed`, `ingest prices/filings/earnings/news/all/stats` |
| `data/cdr_universe.yaml` | Create (generated) | Checked-in universe |
| `data/watchlist.yaml` | Delete | Replaced by the universe |
| `tests/…` | Create/modify | One test file per module (listed in each task) |
| `README.md`, `.env.example`, `docs/superpowers/README.md` | Modify | Docs for the new direction |

---

### Task 1: Close paper book v1.1

**Files:**
- Modify: `docs/personal/paper-trading-playbook.md` (append only)

- [ ] **Step 1: Append the closing section**

Add this to the very end of the file. Do not change anything above it.

```markdown

---

## Closed — 2026-09-22

Closed before the freeze window ended. The 8-K sentiment strategy in this book was replaced by the Swing Assistant design (`docs/superpowers/specs/2026-09-22-swing-assistant-00-overview.md`).

- Trades logged under v1.1: 0 (taken, control, and shadow).
- No results exist, so closing is not a response to results, and no rule above was edited.
- The new system pre-registers its own rules before any backtest (spec 02).
```

- [ ] **Step 2: Confirm only an append happened**

Run: `git diff --stat docs/personal/paper-trading-playbook.md && git diff docs/personal/paper-trading-playbook.md | grep '^-[^-]' || echo "no removed lines"`
Expected: one file changed, insertions only, and `no removed lines`.

- [ ] **Step 3: Commit**

```bash
git add docs/personal/paper-trading-playbook.md
git commit -m "docs: close paper book v1.1 before the swing assistant rebuild

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Remove the Together pipeline and the sentiment backtest

**Files:**
- Delete: `src/signalbench/extraction/`, `src/signalbench/eval/`, `prompts/`, `evals/`, `src/signalbench/backtest/strategy.py`, `src/signalbench/backtest/run.py`, `data/backtest_config.yaml`, `.github/workflows/eval.yml`
- Delete tests: `tests/test_backtest_cli.py`, `tests/test_backtest_schema.py`, `tests/test_backtest_window.py`, `tests/test_eval_gate.py`, `tests/test_eval_labels.py`, `tests/test_eval_metrics.py`, `tests/test_eval_persist.py`, `tests/test_eval_predict.py`, `tests/test_eval_workflow.py`, `tests/test_extract.py`, `tests/test_extract_cli.py`, `tests/test_extract_contract.py`, `tests/test_lookahead.py`, `tests/test_signal_schema.py`, `tests/test_strategy.py`, `tests/fixtures/extract_doc.txt`
- Modify: `src/signalbench/backtest/metrics.py`, `src/signalbench/db/models.py`, `src/signalbench/config.py`, `src/signalbench/cli.py`, `pyproject.toml`, `tests/test_metrics.py`, `tests/test_determinism.py`

- [ ] **Step 1: Delete the retired code and its tests**

```bash
git rm -r -q src/signalbench/extraction src/signalbench/eval prompts evals \
  src/signalbench/backtest/strategy.py src/signalbench/backtest/run.py \
  data/backtest_config.yaml .github/workflows/eval.yml \
  tests/test_backtest_cli.py tests/test_backtest_schema.py tests/test_backtest_window.py \
  tests/test_eval_gate.py tests/test_eval_labels.py tests/test_eval_metrics.py \
  tests/test_eval_persist.py tests/test_eval_predict.py tests/test_eval_workflow.py \
  tests/test_extract.py tests/test_extract_cli.py tests/test_extract_contract.py \
  tests/test_lookahead.py tests/test_signal_schema.py tests/test_strategy.py \
  tests/fixtures/extract_doc.txt
```

- [ ] **Step 2: Move `Trade` into `metrics.py`**

In `src/signalbench/backtest/metrics.py`, replace:

```python
import numpy as np

from signalbench.backtest.strategy import Trade
```

with:

```python
import numpy as np


@dataclass(frozen=True)
class Trade:
    entry_date: date
    exit_date: date
    entry_price: Decimal
    exit_price: Decimal
```

In `tests/test_metrics.py`, replace the two import lines

```python
from signalbench.backtest.metrics import compute_metrics
from signalbench.backtest.strategy import Trade
```

with:

```python
from signalbench.backtest.metrics import Trade, compute_metrics
```

- [ ] **Step 3: Rewrite `tests/test_determinism.py`**

```python
from datetime import date
from decimal import Decimal

from signalbench.backtest.fingerprint import signal_set_fingerprint
from signalbench.backtest.metrics import Trade, compute_metrics


def test_fingerprint_stable() -> None:
    a = signal_set_fingerprint(["b", "a"])
    b = signal_set_fingerprint(["a", "b"])
    assert a == b
    assert len(a) == 64


def test_two_run_metrics_equal() -> None:
    prices = [
        (date(2024, 1, 2), Decimal(100)),
        (date(2024, 1, 3), Decimal(100)),
        (date(2024, 1, 4), Decimal(110)),
        (date(2024, 1, 5), Decimal(110)),
    ]
    trades = [
        Trade(
            entry_date=date(2024, 1, 2),
            exit_date=date(2024, 1, 4),
            entry_price=Decimal(100),
            exit_price=Decimal(110),
        )
    ]
    assert compute_metrics(prices, trades) == compute_metrics(prices, trades)
```

- [ ] **Step 4: Replace `src/signalbench/db/models.py`**

This removes `EventType`, `Signal`, `EvalRun`, `BacktestConfig`, and `BacktestRun`, and nothing else.

```python
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class DocType(str, Enum):
    news = "news"
    eight_k = "eight_k"
    ten_k = "ten_k"
    ten_q = "ten_q"


class Ticker(SQLModel, table=True):
    __tablename__ = "tickers"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    symbol: str = Field(unique=True, index=True)
    company_name: str
    sector: str | None = None
    active: bool = Field(default=True)
    added_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class RawDocument(SQLModel, table=True):
    __tablename__ = "raw_documents"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_raw_documents_source_external_id"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source: str
    external_id: str
    doc_type: DocType
    url: str | None = None
    title: str | None = None
    raw_text: str = Field(sa_column=Column(Text, nullable=False))
    published_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    ingested_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class DocumentTicker(SQLModel, table=True):
    __tablename__ = "document_tickers"

    document_id: uuid.UUID = Field(
        foreign_key="raw_documents.id",
        primary_key=True,
        ondelete="CASCADE",
    )
    ticker_id: uuid.UUID = Field(
        foreign_key="tickers.id",
        primary_key=True,
        ondelete="RESTRICT",
    )


class Price(SQLModel, table=True):
    __tablename__ = "prices"
    __table_args__ = (UniqueConstraint("ticker_id", "date", name="uq_prices_ticker_date"),)

    id: int | None = Field(default=None, primary_key=True)
    ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    date: date
    open: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    high: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    low: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    close: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    adj_close: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    volume: int = Field(sa_column=Column(BigInteger, nullable=False))
```

- [ ] **Step 5: Replace `src/signalbench/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://signalbench:signalbench@localhost:5432/signalbench"
    sec_user_agent: str = "SignalBench/0.1 (dev@example.com)"


settings = Settings()
```

- [ ] **Step 6: Replace `src/signalbench/cli.py` (interim; Task 18 writes the final version)**

```python
from pathlib import Path

import httpx
import typer
from sqlmodel import Session, select

from signalbench.config import settings
from signalbench.db.models import Ticker
from signalbench.db.session import get_session
from signalbench.ingest.edgar import ingest_eight_ks_for_symbol
from signalbench.ingest.prices import fetch_yfinance_daily, ingest_daily_prices
from signalbench.ingest.seed import seed_watchlist as seed_watchlist_from_yaml

WATCHLIST_PATH = Path(__file__).resolve().parents[2] / "data" / "watchlist.yaml"

app = typer.Typer()
ingest_app = typer.Typer()
app.add_typer(ingest_app, name="ingest")


@app.command()
def seed_watchlist() -> None:
    with get_session() as session:
        seed_watchlist_from_yaml(session, WATCHLIST_PATH)


@ingest_app.command()
def filings() -> None:
    with get_session() as session, httpx.Client(timeout=30.0) as client:
        for ticker in _active_tickers(session):
            ingest_eight_ks_for_symbol(
                session,
                ticker.symbol,
                client,
                settings.sec_user_agent,
            )


@ingest_app.command()
def prices() -> None:
    with get_session() as session:
        tickers = _active_tickers(session)
        typer.echo(f"Ingesting prices for {len(tickers)} tickers (2y window)")
        for index, ticker in enumerate(tickers, start=1):
            created = ingest_daily_prices(session, ticker, fetch=fetch_yfinance_daily)
            typer.echo(f"{index}/{len(tickers)} {ticker.symbol} +{created}")


def _active_tickers(session: Session) -> list[Ticker]:
    return list(session.exec(select(Ticker).where(Ticker.active)))
```

- [ ] **Step 7: Drop the `together` dependency**

In `pyproject.toml`, delete the line `  "together>=2.0.0",` from `dependencies`, and delete this block:

```toml
[[tool.mypy.overrides]]
module = ["together"]
ignore_missing_imports = true
```

Then run: `uv lock && uv sync --group dev`
Expected: the lock updates and `together` is uninstalled.

- [ ] **Step 8: Confirm nothing still references removed code**

Run: `git grep -n -E "together|signalbench\.extraction|signalbench\.eval|backtest\.run|backtest\.strategy|EventType|EvalRun|BacktestRun|BacktestConfig" -- src tests pyproject.toml alembic/env.py`
Expected: no output. (Old migration files under `alembic/versions/` still mention these tables, and that's intended.)

- [ ] **Step 9: Run the full suite and static checks**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: all tests pass; ruff `All checks passed!`; mypy `Success`.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor: remove Together extraction, eval gate, and sentiment backtest

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Migration 0007 — drop legacy tables

**Files:**
- Create: `alembic/versions/0007_drop_sentiment_pipeline.py`
- Create: `tests/test_migrations.py`

- [ ] **Step 1: Write the failing test**

`tests/test_migrations.py`:

```python
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO = Path(__file__).resolve().parents[1]
VERSIONS = REPO / "alembic" / "versions"


def _script() -> ScriptDirectory:
    config = Config(str(REPO / "alembic.ini"))
    config.set_main_option("script_location", str(REPO / "alembic"))
    return ScriptDirectory.from_config(config)


def test_migrations_have_single_head() -> None:
    assert _script().get_heads() == ["0007_drop_sentiment_pipeline"]


def test_0007_drops_sentiment_tables_and_enum() -> None:
    text = (VERSIONS / "0007_drop_sentiment_pipeline.py").read_text(encoding="utf-8")
    for table in ("signals", "eval_runs", "backtest_runs", "backtest_configs"):
        assert f'op.drop_table("{table}")' in text
    assert 'name="eventtype"' in text
    assert "checkfirst=True" in text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: FAIL. The head is `0006_backtest_config_name_unique`, and the 0007 file doesn't exist.

- [ ] **Step 3: Write the migration**

`alembic/versions/0007_drop_sentiment_pipeline.py`:

```python
"""Drop the retired sentiment pipeline tables

Revision ID: 0007_drop_sentiment_pipeline
Revises: 0006_backtest_config_name_unique
Create Date: 2026-09-22 12:00:00
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_drop_sentiment_pipeline"
down_revision: str | None = "0006_backtest_config_name_unique"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("backtest_runs")
    op.drop_table("backtest_configs")
    op.drop_table("eval_runs")
    op.drop_table("signals")
    postgresql.ENUM(
        "earnings",
        "guidance",
        "leadership",
        "legal",
        "product",
        "macro",
        "other",
        name="eventtype",
        create_type=False,
    ).drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise NotImplementedError(
        "0007 is one-way: the sentiment pipeline was removed. Restore it from git history."
    )
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/test_migrations.py -v`
Expected: 2 passed.

- [ ] **Step 5: Verify on local Postgres (if Docker is running)**

Run: `docker compose up -d && uv run alembic upgrade head`
Expected: `Running upgrade 0006_backtest_config_name_unique -> 0007_drop_sentiment_pipeline`. If Docker Desktop isn't running, start it first. If it can't be started, note "0007 not verified on Postgres" in the task report; Task 20 runs all migrations anyway.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0007_drop_sentiment_pipeline.py tests/test_migrations.py
git commit -m "feat: migration 0007 drops sentiment pipeline tables

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: ci
on:
  push:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv sync --group dev
      - run: uv run pytest -q
      - run: uv run ruff check .
      - run: uv run mypy src
```

- [ ] **Step 2: Check the YAML parses**

Run: `uv run python -c "import yaml; print(sorted(yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']['test']))"`
Expected: `['runs-on', 'steps']`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run pytest, ruff, and mypy on every push and PR

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Dependencies and settings

**Files:**
- Modify: `pyproject.toml`, `src/signalbench/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:

```python
from datetime import date

import pytest

from signalbench.config import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FINNHUB_API_KEY", "PRICE_HISTORY_START", "FILINGS_BACKFILL_START"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.finnhub_api_key is None
    assert settings.price_history_start == date(2010, 1, 1)
    assert settings.filings_backfill_start == date(2016, 1, 1)


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRICE_HISTORY_START", "2012-01-03")
    monkeypatch.setenv("FILINGS_BACKFILL_START", "2018-06-01")
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    settings = Settings(_env_file=None)
    assert settings.price_history_start == date(2012, 1, 3)
    assert settings.filings_backfill_start == date(2018, 6, 1)
    assert settings.finnhub_api_key == "test-key"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'finnhub_api_key'`.

- [ ] **Step 3: Implement**

`src/signalbench/config.py`:

```python
from datetime import date

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://signalbench:signalbench@localhost:5432/signalbench"
    sec_user_agent: str = "SignalBench/0.1 (dev@example.com)"
    finnhub_api_key: str | None = None
    price_history_start: date = date(2010, 1, 1)
    filings_backfill_start: date = date(2016, 1, 1)


settings = Settings()
```

In `pyproject.toml` `dependencies`, add these three lines after `"pydantic-settings>=2.6",`:

```toml
  "beautifulsoup4>=4.12",
  "lxml>=5.0",
  "tzdata>=2024.1",
```

Run: `uv lock && uv sync --group dev`

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_config.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/signalbench/config.py tests/test_config.py
git commit -m "feat: settings for Finnhub key and history start dates

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Schema — ticker kinds, document fields, earnings events (migration 0008)

**Files:**
- Modify: `src/signalbench/db/models.py`, `tests/test_schema.py`, `tests/test_migrations.py`
- Create: `alembic/versions/0008_swing_data_foundation.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_schema.py`. Add these imports at the top of the file, merging with the existing ones: `from signalbench.db.models import DocType, EarningsEvent, TickerKind`.

```python
def test_ticker_kind_defaults_to_us_stock(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple")
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    assert ticker.kind is TickerKind.us_stock
    assert ticker.price_symbol is None
    assert ticker.us_ticker_id is None


def test_cdr_links_to_its_us_ticker(session: Session) -> None:
    us = Ticker(symbol="NVDA", company_name="Nvidia")
    session.add(us)
    session.commit()
    session.refresh(us)
    session.add(
        Ticker(
            symbol="ZNVD",
            company_name="Nvidia",
            kind=TickerKind.cdr,
            price_symbol="ZNVD.NE",
            us_ticker_id=us.id,
        )
    )
    session.commit()
    cdr = session.exec(select(Ticker).where(Ticker.symbol == "ZNVD")).one()
    assert cdr.kind is TickerKind.cdr
    assert cdr.us_ticker_id == us.id
    assert cdr.price_symbol == "ZNVD.NE"


def test_raw_document_new_fields(session: Session) -> None:
    accepted = datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)
    filing = RawDocument(
        source="sec_edgar",
        external_id="0000320193-26-000018",
        doc_type=DocType.eight_k,
        raw_text="<html></html>",
        published_at=accepted,
        acceptance_at=accepted,
        items="2.02,9.01",
        text="Item 2.02 Results of Operations",
    )
    news = RawDocument(
        source="finnhub",
        external_id="123",
        doc_type=DocType.news,
        raw_text="Headline",
        published_at=accepted,
    )
    session.add(filing)
    session.add(news)
    session.commit()
    session.refresh(filing)
    session.refresh(news)
    assert filing.acceptance_at == accepted
    assert filing.items == "2.02,9.01"
    assert filing.text == "Item 2.02 Results of Operations"
    assert news.acceptance_at is None
    assert news.text is None


def test_earnings_event_unique_per_ticker_date_source(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple")
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    session.add(EarningsEvent(ticker_id=ticker.id, event_date=date(2026, 7, 30), source="sec_2.02"))
    session.commit()
    session.add(EarningsEvent(ticker_id=ticker.id, event_date=date(2026, 7, 30), source="sec_2.02"))
    with pytest.raises(IntegrityError):
        session.commit()
```

In `tests/test_migrations.py`, change the head assertion to `["0008_swing_data_foundation"]` and append:

```python
def test_0008_creates_ticker_kind_enum_idempotently() -> None:
    text = (VERSIONS / "0008_swing_data_foundation.py").read_text(encoding="utf-8")
    assert 'name="tickerkind"' in text
    assert "checkfirst=True" in text
    assert 'op.create_table(\n        "earnings_events"' in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_schema.py tests/test_migrations.py -v`
Expected: FAIL with an ImportError for `EarningsEvent`/`TickerKind`.

- [ ] **Step 3: Update the models**

In `src/signalbench/db/models.py`, add `TickerKind` after `DocType`:

```python
class TickerKind(str, Enum):
    us_stock = "us_stock"
    cdr = "cdr"
    benchmark = "benchmark"
```

Add three fields to `Ticker`, after `added_at`:

```python
    kind: TickerKind = Field(default=TickerKind.us_stock)
    price_symbol: str | None = None
    us_ticker_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="tickers.id",
        ondelete="RESTRICT",
    )
```

Add three fields to `RawDocument`, after `published_at`:

```python
    acceptance_at: datetime | None = Field(
        default=None,
        sa_column=Column(UTCDateTime(), nullable=True),
    )
    items: str | None = None
    text: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
```

Append a new model at the end of the file:

```python
class EarningsEvent(SQLModel, table=True):
    __tablename__ = "earnings_events"
    __table_args__ = (
        UniqueConstraint(
            "ticker_id",
            "event_date",
            "source",
            name="uq_earnings_events_ticker_date_source",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    event_date: date
    source: str
```

- [ ] **Step 4: Write migration 0008**

`alembic/versions/0008_swing_data_foundation.py`:

```python
"""Ticker kinds and links, document acceptance/items/text, earnings events

Revision ID: 0008_swing_data_foundation
Revises: 0007_drop_sentiment_pipeline
Create Date: 2026-09-22 12:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_swing_data_foundation"
down_revision: str | None = "0007_drop_sentiment_pipeline"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    tickerkind = postgresql.ENUM(
        "us_stock",
        "cdr",
        "benchmark",
        name="tickerkind",
        create_type=False,
    )
    tickerkind.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "tickers",
        sa.Column("kind", tickerkind, nullable=False, server_default="us_stock"),
    )
    op.add_column("tickers", sa.Column("price_symbol", sa.String(), nullable=True))
    op.add_column("tickers", sa.Column("us_ticker_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_tickers_us_ticker_id",
        "tickers",
        "tickers",
        ["us_ticker_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "raw_documents",
        sa.Column("acceptance_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("raw_documents", sa.Column("items", sa.String(), nullable=True))
    op.add_column("raw_documents", sa.Column("text", sa.Text(), nullable=True))

    op.create_table(
        "earnings_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker_id", sa.Uuid(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["ticker_id"], ["tickers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker_id",
            "event_date",
            "source",
            name="uq_earnings_events_ticker_date_source",
        ),
    )


def downgrade() -> None:
    op.drop_table("earnings_events")
    op.drop_column("raw_documents", "text")
    op.drop_column("raw_documents", "items")
    op.drop_column("raw_documents", "acceptance_at")
    op.drop_constraint("fk_tickers_us_ticker_id", "tickers", type_="foreignkey")
    op.drop_column("tickers", "us_ticker_id")
    op.drop_column("tickers", "price_symbol")
    op.drop_column("tickers", "kind")
    postgresql.ENUM(
        "us_stock",
        "cdr",
        "benchmark",
        name="tickerkind",
        create_type=False,
    ).drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_schema.py tests/test_migrations.py -v && uv run mypy src`
Expected: all passed; mypy `Success`.

- [ ] **Step 6: Verify on local Postgres (if Docker is running)**

Run: `uv run alembic upgrade head && uv run alembic downgrade 0007_drop_sentiment_pipeline && uv run alembic upgrade head`
Expected: all three succeed. The round trip proves 0008's downgrade works.

- [ ] **Step 7: Commit**

```bash
git add src/signalbench/db/models.py alembic/versions/0008_swing_data_foundation.py tests/test_schema.py tests/test_migrations.py
git commit -m "feat: ticker kinds, document acceptance/text, earnings events (0008)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Rate limiter

**Files:**
- Create: `src/signalbench/ingest/ratelimit.py`, `tests/test_ratelimit.py`

- [ ] **Step 1: Write the failing test**

`tests/test_ratelimit.py`:

```python
from signalbench.ingest.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _limiter(fake: FakeClock) -> RateLimiter:
    return RateLimiter(calls=8, period=1.0, clock=fake.clock, sleep=fake.sleep)


def test_first_call_does_not_sleep() -> None:
    fake = FakeClock()
    _limiter(fake).wait()
    assert fake.sleeps == []


def test_calls_are_spaced_by_period_over_calls() -> None:
    fake = FakeClock()
    limiter = _limiter(fake)
    limiter.wait()
    limiter.wait()
    limiter.wait()
    assert fake.sleeps == [0.125, 0.125]


def test_no_sleep_when_caller_is_already_slow() -> None:
    fake = FakeClock()
    limiter = _limiter(fake)
    limiter.wait()
    fake.now += 1.0
    limiter.wait()
    assert fake.sleeps == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_ratelimit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.ingest.ratelimit'`.

- [ ] **Step 3: Implement**

`src/signalbench/ingest/ratelimit.py`:

```python
import time
from collections.abc import Callable


class RateLimiter:
    """Spaces calls evenly so at most `calls` happen per `period` seconds."""

    def __init__(
        self,
        calls: int,
        period: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = period / calls
        self._clock = clock
        self._sleep = sleep
        self._next_allowed = 0.0

    def wait(self) -> None:
        now = self._clock()
        if now < self._next_allowed:
            self._sleep(self._next_allowed - now)
            now = self._next_allowed
        self._next_allowed = now + self._interval
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_ratelimit.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/ingest/ratelimit.py tests/test_ratelimit.py
git commit -m "feat: shared rate limiter for SEC and Finnhub

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: CDR universe parsing and YAML

**Files:**
- Create: `src/signalbench/ingest/cdr.py`, `tests/test_cdr.py`, `tests/fixtures/cboe_directory.json`

- [ ] **Step 1: Add the fixture**

`tests/fixtures/cboe_directory.json` (trimmed from the real endpoint on 2026-09-22):

```json
{
  "data": [
    {"symbol": "ZNVD", "name": "NVIDIA (NVDA) BMO CDR (CAD HEDGED)", "currency": "CAD", "security": "dr", "security_sub_type": "deprcpt", "marketcap": 1.0, "last": 12.17, "changepcnt": 0.1, "volume": 1639},
    {"symbol": "ZBRK", "name": "BERKSHIRE HATHAWAY (BRK/B) BMO CDR (CAD HEDGED)", "currency": "CAD", "security": "dr", "security_sub_type": "deprcpt", "marketcap": 1.0, "last": 9.96, "changepcnt": 0.0, "volume": 306},
    {"symbol": "ZMCD", "name": "MCDONALD'S (MCD) BMO CDR (CAD HEDGED)", "currency": "CAD", "security": "dr", "security_sub_type": "deprcpt", "marketcap": 1.0, "last": 8.04, "changepcnt": 0.0, "volume": 0},
    {"symbol": "MB", "name": "MERCEDES CDR (CAD HEDGED)", "currency": "CAD", "security": "dr", "security_sub_type": "deprcpt", "marketcap": 1.0, "last": 7.59, "changepcnt": -0.5, "volume": 312},
    {"symbol": "XYZ", "name": "SOME ETF (XYZ)", "currency": "CAD", "security": "etf", "security_sub_type": "etf", "marketcap": 1.0, "last": 20.0, "changepcnt": 0.0, "volume": 10}
  ]
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_cdr.py`:

```python
import json
from pathlib import Path

import pytest

from signalbench.ingest.cdr import (
    CdrEntry,
    build_entries,
    diff_universe,
    dump_universe,
    gics_sector,
    load_universe,
    parse_cboe_directory,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _payload() -> dict[str, object]:
    return json.loads((FIXTURES / "cboe_directory.json").read_text(encoding="utf-8"))


def _nvda() -> CdrEntry:
    return CdrEntry(
        us_symbol="NVDA",
        cdr_symbol="ZNVD",
        price_symbol="ZNVD.NE",
        company_name="Nvidia",
        sector="Information Technology",
    )


def test_parse_keeps_us_company_drs_only() -> None:
    listings = parse_cboe_directory(_payload())
    assert [(item.us_symbol, item.cdr_symbol) for item in listings] == [
        ("BRK-B", "ZBRK"),
        ("MCD", "ZMCD"),
        ("NVDA", "ZNVD"),
    ]


def test_company_names_are_capwords() -> None:
    names = {item.us_symbol: item.company_name for item in parse_cboe_directory(_payload())}
    assert names["NVDA"] == "Nvidia"
    assert names["MCD"] == "Mcdonald's"
    assert names["BRK-B"] == "Berkshire Hathaway"


def test_parse_rejects_two_cdrs_for_one_us_symbol() -> None:
    payload = {
        "data": [
            {"symbol": "ZNVD", "name": "NVIDIA (NVDA) BMO CDR (CAD HEDGED)", "security": "dr"},
            {"symbol": "NVDA", "name": "NVIDIA (NVDA) CIBC CDR (CAD HEDGED)", "security": "dr"},
        ]
    }
    with pytest.raises(ValueError, match="NVDA"):
        parse_cboe_directory(payload)


def test_gics_mapping() -> None:
    assert gics_sector("Technology") == "Information Technology"
    assert gics_sector("Consumer Cyclical") == "Consumer Discretionary"
    with pytest.raises(ValueError, match="Crypto"):
        gics_sector("Crypto")


def test_build_entries_reuses_known_sectors_and_looks_up_new_ones() -> None:
    calls: list[str] = []

    def lookup(symbol: str) -> str:
        calls.append(symbol)
        return "Financials"

    entries = build_entries(parse_cboe_directory(_payload()), lookup, existing=[_nvda()])
    by_symbol = {entry.us_symbol: entry for entry in entries}
    assert calls == ["BRK-B", "MCD"]
    assert by_symbol["NVDA"].sector == "Information Technology"
    assert by_symbol["BRK-B"].price_symbol == "ZBRK.NE"
    assert by_symbol["MCD"].sector == "Financials"


def test_yaml_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "universe.yaml"
    dump_universe([_nvda()], path)
    assert load_universe(path) == [_nvda()]
    assert path.read_text(encoding="utf-8").startswith("# Generated by")


def test_load_rejects_duplicate_us_symbols(tmp_path: Path) -> None:
    path = tmp_path / "universe.yaml"
    dump_universe([_nvda(), _nvda()], path)
    with pytest.raises(ValueError, match="NVDA"):
        load_universe(path)


def test_diff_universe() -> None:
    aapl = CdrEntry(
        us_symbol="AAPL",
        cdr_symbol="ZAAP",
        price_symbol="ZAAP.NE",
        company_name="Apple",
        sector="Information Technology",
    )
    diff = diff_universe([_nvda(), aapl], parse_cboe_directory(_payload()))
    assert [item.us_symbol for item in diff.added] == ["BRK-B", "MCD"]
    assert [entry.us_symbol for entry in diff.removed] == ["AAPL"]
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_cdr.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.ingest.cdr'`.

- [ ] **Step 4: Implement**

`src/signalbench/ingest/cdr.py`:

```python
import re
import string
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import httpx
import yaml

CBOE_DIRECTORY_URL = "https://www-api.cboe.com/ca/equities/listing-directory-data/"
CDR_PRICE_SUFFIX = ".NE"
_US_TICKER_IN_NAME = re.compile(r"\(([A-Z][A-Z./]{0,6})\)")
_YAML_HEADER = (
    "# Generated by `signalbench universe refresh --write`.\n"
    "# Review every change in git before committing.\n"
)

YF_SECTOR_TO_GICS: dict[str, str] = {
    "Technology": "Information Technology",
    "Communication Services": "Communication Services",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Financial Services": "Financials",
    "Healthcare": "Health Care",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Basic Materials": "Materials",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities",
}


@dataclass(frozen=True)
class CdrListing:
    cdr_symbol: str
    us_symbol: str
    company_name: str


@dataclass(frozen=True)
class CdrEntry:
    us_symbol: str
    cdr_symbol: str
    price_symbol: str
    company_name: str
    sector: str


@dataclass(frozen=True)
class UniverseDiff:
    added: list[CdrListing]
    removed: list[CdrEntry]


def parse_cboe_directory(payload: dict[str, Any]) -> list[CdrListing]:
    """US-company CDRs: depositary receipts whose name carries a US ticker in parentheses."""
    by_us_symbol: dict[str, CdrListing] = {}
    for row in payload["data"]:
        if row.get("security") != "dr":
            continue
        name = str(row["name"])
        match = _US_TICKER_IN_NAME.search(name)
        if match is None:
            continue
        us_symbol = match.group(1).replace("/", "-").replace(".", "-")
        if us_symbol in by_us_symbol:
            raise ValueError(
                f"Two CDRs list US symbol {us_symbol}: "
                f"{by_us_symbol[us_symbol].cdr_symbol} and {row['symbol']}"
            )
        by_us_symbol[us_symbol] = CdrListing(
            cdr_symbol=str(row["symbol"]),
            us_symbol=us_symbol,
            company_name=string.capwords(name.split(" (", 1)[0].strip()),
        )
    return [by_us_symbol[symbol] for symbol in sorted(by_us_symbol)]


def gics_sector(yf_sector: str) -> str:
    try:
        return YF_SECTOR_TO_GICS[yf_sector]
    except KeyError:
        raise ValueError(f"Unmapped yfinance sector: {yf_sector!r}") from None


def build_entries(
    listings: list[CdrListing],
    sector_lookup: Callable[[str], str],
    existing: list[CdrEntry],
) -> list[CdrEntry]:
    known_sectors = {entry.us_symbol: entry.sector for entry in existing}
    return [
        CdrEntry(
            us_symbol=listing.us_symbol,
            cdr_symbol=listing.cdr_symbol,
            price_symbol=f"{listing.cdr_symbol}{CDR_PRICE_SUFFIX}",
            company_name=listing.company_name,
            sector=known_sectors.get(listing.us_symbol) or sector_lookup(listing.us_symbol),
        )
        for listing in listings
    ]


def diff_universe(current: list[CdrEntry], listings: list[CdrListing]) -> UniverseDiff:
    listed = {listing.us_symbol for listing in listings}
    current_symbols = {entry.us_symbol for entry in current}
    return UniverseDiff(
        added=[listing for listing in listings if listing.us_symbol not in current_symbols],
        removed=[entry for entry in current if entry.us_symbol not in listed],
    )


def load_universe(path: Path) -> list[CdrEntry]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    entries = [CdrEntry(**cast(dict[str, str], item)) for item in raw]
    seen: set[str] = set()
    for entry in entries:
        if entry.us_symbol in seen:
            raise ValueError(f"Duplicate us_symbol in {path}: {entry.us_symbol}")
        seen.add(entry.us_symbol)
    return entries


def dump_universe(entries: list[CdrEntry], path: Path) -> None:
    body = yaml.safe_dump([asdict(entry) for entry in entries], sort_keys=False, allow_unicode=True)
    path.write_text(_YAML_HEADER + body, encoding="utf-8")


def fetch_cboe_directory(client: httpx.Client) -> dict[str, Any]:
    response = client.get(CBOE_DIRECTORY_URL)
    response.raise_for_status()
    return cast(dict[str, Any], response.json())


def yfinance_sector(symbol: str) -> str:
    import yfinance as yf

    sector = yf.Ticker(symbol).info.get("sector")
    if not sector:
        raise ValueError(f"yfinance has no sector for {symbol}")
    return str(sector)
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_cdr.py -v && uv run mypy src`
Expected: 8 passed; mypy `Success`.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/ingest/cdr.py tests/test_cdr.py tests/fixtures/cboe_directory.json
git commit -m "feat: parse Cboe CDR directory into a checked-in universe

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Seed tickers from the universe

**Files:**
- Rewrite: `src/signalbench/ingest/seed.py`, `tests/test_seed.py`
- Modify: `src/signalbench/cli.py`, `tests/test_cli.py`
- Delete: `data/watchlist.yaml`

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_seed.py`:

```python
import pytest
from sqlmodel import Session, select

from signalbench.db.models import Ticker, TickerKind
from signalbench.ingest.cdr import CdrEntry
from signalbench.ingest.seed import seed_universe

ENTRIES = [
    CdrEntry(
        us_symbol="NVDA",
        cdr_symbol="ZNVD",
        price_symbol="ZNVD.NE",
        company_name="Nvidia",
        sector="Information Technology",
    ),
    CdrEntry(
        us_symbol="JPM",
        cdr_symbol="ZJPM",
        price_symbol="ZJPM.NE",
        company_name="Jpmorgan",
        sector="Financials",
    ),
]


def _by_symbol(session: Session) -> dict[str, Ticker]:
    return {ticker.symbol: ticker for ticker in session.exec(select(Ticker)).all()}


def test_seed_creates_pairs_and_benchmarks(session: Session) -> None:
    result = seed_universe(session, ENTRIES)
    rows = _by_symbol(session)
    assert set(rows) == {"NVDA", "ZNVD", "JPM", "ZJPM", "QQQ", "SPY"}
    assert rows["NVDA"].kind is TickerKind.us_stock
    assert rows["NVDA"].price_symbol == "NVDA"
    assert rows["NVDA"].sector == "Information Technology"
    assert rows["ZNVD"].kind is TickerKind.cdr
    assert rows["ZNVD"].price_symbol == "ZNVD.NE"
    assert rows["ZNVD"].us_ticker_id == rows["NVDA"].id
    assert rows["QQQ"].kind is TickerKind.benchmark
    assert all(row.active for row in rows.values())
    assert result.deactivated == []


def test_seed_is_idempotent(session: Session) -> None:
    seed_universe(session, ENTRIES)
    seed_universe(session, ENTRIES)
    assert len(_by_symbol(session)) == 6


def test_seed_deactivates_names_dropped_from_universe(session: Session) -> None:
    seed_universe(session, ENTRIES)
    result = seed_universe(session, ENTRIES[:1])
    rows = _by_symbol(session)
    assert result.deactivated == ["JPM", "ZJPM"]
    assert rows["JPM"].active is False
    assert rows["ZJPM"].active is False
    assert rows["NVDA"].active is True


def test_seed_refuses_to_change_a_ticker_kind(session: Session) -> None:
    session.add(Ticker(symbol="ZNVD", company_name="Not a CDR", kind=TickerKind.us_stock))
    session.commit()
    with pytest.raises(ValueError, match="ZNVD"):
        seed_universe(session, ENTRIES)
```

In `tests/test_cli.py`, replace the body of `test_help_lists_seed_and_ingest` with:

```python
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "seed" in result.stdout
    assert "seed-watchlist" not in result.stdout
    assert "ingest" in result.stdout
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_seed.py tests/test_cli.py -v`
Expected: FAIL with `ImportError: cannot import name 'seed_universe'`.

- [ ] **Step 3: Implement**

Replace `src/signalbench/ingest/seed.py`:

```python
import uuid
from dataclasses import dataclass

from sqlmodel import Session, select

from signalbench.db.models import Ticker, TickerKind
from signalbench.ingest.cdr import CdrEntry

BENCHMARKS: tuple[tuple[str, str], ...] = (
    ("QQQ", "Invesco QQQ Trust"),
    ("SPY", "SPDR S&P 500 ETF Trust"),
)


@dataclass(frozen=True)
class SeedResult:
    us_stocks: int
    cdrs: int
    benchmarks: int
    deactivated: list[str]


def seed_universe(session: Session, entries: list[CdrEntry]) -> SeedResult:
    by_symbol = {ticker.symbol: ticker for ticker in session.exec(select(Ticker)).all()}
    keep: set[str] = set()
    for entry in entries:
        us = _upsert(
            session,
            by_symbol,
            symbol=entry.us_symbol,
            company_name=entry.company_name,
            kind=TickerKind.us_stock,
            sector=entry.sector,
            price_symbol=entry.us_symbol,
            us_ticker_id=None,
        )
        _upsert(
            session,
            by_symbol,
            symbol=entry.cdr_symbol,
            company_name=entry.company_name,
            kind=TickerKind.cdr,
            sector=entry.sector,
            price_symbol=entry.price_symbol,
            us_ticker_id=us.id,
        )
        keep.update((entry.us_symbol, entry.cdr_symbol))
    for symbol, name in BENCHMARKS:
        _upsert(
            session,
            by_symbol,
            symbol=symbol,
            company_name=name,
            kind=TickerKind.benchmark,
            sector=None,
            price_symbol=symbol,
            us_ticker_id=None,
        )
        keep.add(symbol)
    deactivated = sorted(
        symbol for symbol, ticker in by_symbol.items() if symbol not in keep and ticker.active
    )
    for symbol in deactivated:
        by_symbol[symbol].active = False
        session.add(by_symbol[symbol])
    session.commit()
    return SeedResult(
        us_stocks=len(entries),
        cdrs=len(entries),
        benchmarks=len(BENCHMARKS),
        deactivated=deactivated,
    )


def _upsert(
    session: Session,
    by_symbol: dict[str, Ticker],
    *,
    symbol: str,
    company_name: str,
    kind: TickerKind,
    sector: str | None,
    price_symbol: str,
    us_ticker_id: uuid.UUID | None,
) -> Ticker:
    ticker = by_symbol.get(symbol)
    if ticker is None:
        ticker = Ticker(symbol=symbol, company_name=company_name, kind=kind)
        by_symbol[symbol] = ticker
    elif ticker.kind is not kind:
        raise ValueError(
            f"{symbol} is already a {ticker.kind.value}; refusing to reseed it as {kind.value}"
        )
    ticker.company_name = company_name
    ticker.sector = sector
    ticker.price_symbol = price_symbol
    ticker.us_ticker_id = us_ticker_id
    ticker.active = True
    session.add(ticker)
    session.flush()
    return ticker
```

In `src/signalbench/cli.py`, replace the line

```python
from signalbench.ingest.seed import seed_watchlist as seed_watchlist_from_yaml
```

with

```python
from signalbench.ingest.cdr import load_universe
from signalbench.ingest.seed import seed_universe
```

Replace

```python
WATCHLIST_PATH = Path(__file__).resolve().parents[2] / "data" / "watchlist.yaml"
```

with

```python
UNIVERSE_PATH = Path(__file__).resolve().parents[2] / "data" / "cdr_universe.yaml"
```

Replace the whole `seed_watchlist` command with:

```python
@app.command()
def seed() -> None:
    entries = load_universe(UNIVERSE_PATH)
    with get_session() as session:
        result = seed_universe(session, entries)
    typer.echo(
        f"Seeded {result.us_stocks} US stocks, {result.cdrs} CDRs, "
        f"{result.benchmarks} benchmarks; deactivated {len(result.deactivated)}"
    )
```

Then: `git rm -q data/watchlist.yaml`

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_seed.py tests/test_cli.py -v && uv run mypy src`
Expected: all passed; mypy `Success`.

- [ ] **Step 5: Commit**

```bash
git add -A src/signalbench/ingest/seed.py src/signalbench/cli.py tests/test_seed.py tests/test_cli.py data/watchlist.yaml
git commit -m "feat: seed US stocks, CDRs, and benchmarks from the universe

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Price ingest with history start, upsert, and validation; adjusted bars

**Files:**
- Rewrite: `src/signalbench/ingest/prices.py`, `tests/test_prices.py`
- Create: `src/signalbench/market/__init__.py`, `src/signalbench/market/bars.py`, `tests/test_bars.py`
- Modify: `src/signalbench/cli.py`

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_prices.py`:

```python
from datetime import date
from decimal import Decimal

from sqlmodel import Session, select

from signalbench.db.models import Price, Ticker, TickerKind
from signalbench.ingest.prices import DailyBar, fetch_start, ingest_daily_prices

HISTORY_START = date(2010, 1, 1)


def _bar(day: date, adj_close: str = "185.2000", high: str = "186.0000", close: str = "185.5000") -> DailyBar:
    return DailyBar(
        date=day,
        open=Decimal("185.0000"),
        high=Decimal(high),
        low=Decimal("184.0000"),
        close=Decimal(close),
        adj_close=Decimal(adj_close),
        volume=50_000_000,
    )


def _ticker(session: Session, symbol: str = "AAPL", price_symbol: str | None = None) -> Ticker:
    ticker = Ticker(symbol=symbol, company_name=symbol, price_symbol=price_symbol)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    return ticker


def test_ingest_writes_adj_close_and_is_idempotent(session: Session) -> None:
    ticker = _ticker(session)

    def fetch(_symbol: str, _start: date) -> list[DailyBar]:
        return [_bar(date(2024, 1, 2))]

    first = ingest_daily_prices(session, ticker, fetch=fetch, history_start=HISTORY_START)
    second = ingest_daily_prices(session, ticker, fetch=fetch, history_start=HISTORY_START)
    assert (first.created, first.updated, first.rejected) == (1, 0, 0)
    assert (second.created, second.updated, second.rejected) == (0, 0, 0)
    row = session.exec(select(Price)).one()
    assert row.adj_close == Decimal("185.2000")
    assert row.ticker_id == ticker.id


def test_fetch_start_uses_history_start_then_refetches_ten_days(session: Session) -> None:
    ticker = _ticker(session)
    assert fetch_start(session, ticker, HISTORY_START) == HISTORY_START
    ingest_daily_prices(
        session, ticker, fetch=lambda _s, _d: [_bar(date(2024, 1, 12))], history_start=HISTORY_START
    )
    assert fetch_start(session, ticker, HISTORY_START) == date(2024, 1, 2)


def test_refetch_updates_changed_values(session: Session) -> None:
    ticker = _ticker(session)
    ingest_daily_prices(
        session, ticker, fetch=lambda _s, _d: [_bar(date(2024, 1, 2))], history_start=HISTORY_START
    )
    result = ingest_daily_prices(
        session,
        ticker,
        fetch=lambda _s, _d: [_bar(date(2024, 1, 2), adj_close="180.0000")],
        history_start=HISTORY_START,
    )
    assert (result.created, result.updated) == (0, 1)
    assert session.exec(select(Price)).one().adj_close == Decimal("180.0000")


def test_invalid_bars_are_rejected(session: Session) -> None:
    ticker = _ticker(session)
    bars = [
        _bar(date(2024, 1, 2), high="100.0000"),
        _bar(date(2024, 1, 3), close="0"),
    ]
    result = ingest_daily_prices(session, ticker, fetch=lambda _s, _d: bars, history_start=HISTORY_START)
    assert (result.created, result.rejected) == (0, 2)
    assert session.exec(select(Price)).all() == []


def test_fetch_uses_price_symbol(session: Session) -> None:
    ticker = _ticker(session, symbol="ZNVD", price_symbol="ZNVD.NE")
    ticker.kind = TickerKind.cdr
    requested: list[tuple[str, date]] = []

    def fetch(symbol: str, start: date) -> list[DailyBar]:
        requested.append((symbol, start))
        return []

    ingest_daily_prices(session, ticker, fetch=fetch, history_start=HISTORY_START)
    assert requested == [("ZNVD.NE", HISTORY_START)]
```

`tests/test_bars.py`:

```python
from datetime import date
from decimal import Decimal

from sqlmodel import Session

from signalbench.db.models import Price, Ticker
from signalbench.market.bars import adjusted_bars


def test_adjusted_bars_scale_ohl_by_adj_factor_in_date_order(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple")
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    for day, adj in ((date(2024, 1, 3), "100.0000"), (date(2024, 1, 2), "200.0000")):
        session.add(
            Price(
                ticker_id=ticker.id,
                date=day,
                open=Decimal("210.0000"),
                high=Decimal("220.0000"),
                low=Decimal("190.0000"),
                close=Decimal("200.0000"),
                adj_close=Decimal(adj),
                volume=1_000,
            )
        )
    session.commit()

    bars = adjusted_bars(session, ticker.id)
    assert [bar.date for bar in bars] == [date(2024, 1, 2), date(2024, 1, 3)]
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close) == (210.0, 220.0, 190.0, 200.0)
    assert (bars[1].open, bars[1].high, bars[1].low, bars[1].close) == (105.0, 110.0, 95.0, 100.0)
    assert [bar.date for bar in adjusted_bars(session, ticker.id, start=date(2024, 1, 3))] == [
        date(2024, 1, 3)
    ]
    assert [bar.date for bar in adjusted_bars(session, ticker.id, end=date(2024, 1, 2))] == [
        date(2024, 1, 2)
    ]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_prices.py tests/test_bars.py -v`
Expected: FAIL with ImportErrors for `fetch_start` and `signalbench.market`.

- [ ] **Step 3: Implement prices**

Replace `src/signalbench/ingest/prices.py`:

```python
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlmodel import Session, col, select

from signalbench.db.models import Price, Ticker

logger = logging.getLogger(__name__)

# 10 calendar days always covers at least 5 sessions, so late adjustments are picked up.
REFETCH_CALENDAR_DAYS = 10


@dataclass(frozen=True)
class DailyBar:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal
    volume: int


@dataclass(frozen=True)
class PriceIngestResult:
    created: int
    updated: int
    rejected: int


PriceFetcher = Callable[[str, date], list[DailyBar]]


def validate_bar(bar: DailyBar) -> str | None:
    if min(bar.open, bar.high, bar.low, bar.close, bar.adj_close) <= 0:
        return "non_positive_price"
    if bar.high < bar.low:
        return "high_below_low"
    if bar.volume < 0:
        return "negative_volume"
    return None


def fetch_start(session: Session, ticker: Ticker, history_start: date) -> date:
    last: date | None = session.exec(
        select(func.max(Price.date)).where(Price.ticker_id == ticker.id)
    ).one()
    if last is None:
        return history_start
    return max(history_start, last - timedelta(days=REFETCH_CALENDAR_DAYS))


def ingest_daily_prices(
    session: Session,
    ticker: Ticker,
    fetch: PriceFetcher,
    history_start: date,
) -> PriceIngestResult:
    start = fetch_start(session, ticker, history_start)
    symbol = ticker.price_symbol or ticker.symbol
    existing = {
        row.date: row
        for row in session.exec(
            select(Price).where(Price.ticker_id == ticker.id, col(Price.date) >= start)
        ).all()
    }
    created = updated = rejected = 0
    for bar in fetch(symbol, start):
        reason = validate_bar(bar)
        if reason is not None:
            logger.warning("Rejected %s bar on %s: %s", symbol, bar.date, reason)
            rejected += 1
            continue
        row = existing.get(bar.date)
        if row is None:
            row = Price(
                ticker_id=ticker.id,
                date=bar.date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                adj_close=bar.adj_close,
                volume=bar.volume,
            )
            existing[bar.date] = row
            created += 1
        elif _row_values(row) != _bar_values(bar):
            row.open = bar.open
            row.high = bar.high
            row.low = bar.low
            row.close = bar.close
            row.adj_close = bar.adj_close
            row.volume = bar.volume
            updated += 1
        else:
            continue
        session.add(row)
    session.commit()
    return PriceIngestResult(created=created, updated=updated, rejected=rejected)


def _row_values(row: Price) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, int]:
    return (row.open, row.high, row.low, row.close, row.adj_close, row.volume)


def _bar_values(bar: DailyBar) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, int]:
    return (bar.open, bar.high, bar.low, bar.close, bar.adj_close, bar.volume)


def fetch_yfinance_daily(symbol: str, start: date) -> list[DailyBar]:
    import yfinance as yf

    frame = yf.Ticker(symbol).history(start=start.isoformat(), auto_adjust=False, timeout=30)
    bars: list[DailyBar] = []
    for idx, row in frame.iterrows():
        adj = row["Adj Close"] if "Adj Close" in row.index else row["Close"]
        values = [
            float(row["Open"]),
            float(row["High"]),
            float(row["Low"]),
            float(row["Close"]),
            float(adj),
            float(row["Volume"]),
        ]
        # yfinance returns NaN for the current, unfinished session.
        if any(math.isnan(value) for value in values):
            continue
        bars.append(
            DailyBar(
                date=idx.date(),
                open=_decimal(values[0]),
                high=_decimal(values[1]),
                low=_decimal(values[2]),
                close=_decimal(values[3]),
                adj_close=_decimal(values[4]),
                volume=int(values[5]),
            )
        )
    return bars


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))
```

- [ ] **Step 4: Implement adjusted bars**

`src/signalbench/market/__init__.py`: an empty file.

`src/signalbench/market/bars.py`:

```python
import uuid
from dataclasses import dataclass
from datetime import date

from sqlmodel import Session, col, select

from signalbench.db.models import Price


@dataclass(frozen=True)
class AdjustedBar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


def adjust(row: Price) -> AdjustedBar:
    """Scale open/high/low by adj_close/close so the whole bar is split- and dividend-adjusted."""
    factor = float(row.adj_close) / float(row.close)
    return AdjustedBar(
        date=row.date,
        open=float(row.open) * factor,
        high=float(row.high) * factor,
        low=float(row.low) * factor,
        close=float(row.adj_close),
        volume=row.volume,
    )


def adjusted_bars(
    session: Session,
    ticker_id: uuid.UUID,
    start: date | None = None,
    end: date | None = None,
) -> list[AdjustedBar]:
    query = select(Price).where(Price.ticker_id == ticker_id)
    if start is not None:
        query = query.where(col(Price.date) >= start)
    if end is not None:
        query = query.where(col(Price.date) <= end)
    return [adjust(row) for row in session.exec(query.order_by(col(Price.date))).all()]
```

- [ ] **Step 5: Update the interim CLI `prices` command**

In `src/signalbench/cli.py`, replace the whole `prices` command with:

```python
@ingest_app.command()
def prices() -> None:
    with get_session() as session:
        tickers = _active_tickers(session)
        for index, ticker in enumerate(tickers, start=1):
            result = ingest_daily_prices(
                session,
                ticker,
                fetch=fetch_yfinance_daily,
                history_start=settings.price_history_start,
            )
            typer.echo(
                f"{index}/{len(tickers)} {ticker.symbol} "
                f"+{result.created} ~{result.updated} x{result.rejected}"
            )
```

- [ ] **Step 6: Run the tests and checks**

Run: `uv run pytest tests/test_prices.py tests/test_bars.py -v && uv run mypy src && uv run ruff check .`
Expected: all passed; mypy `Success`; ruff clean.

- [ ] **Step 7: Commit**

```bash
git add src/signalbench/ingest/prices.py src/signalbench/market src/signalbench/cli.py tests/test_prices.py tests/test_bars.py
git commit -m "feat: price history from a start date with upsert, validation, adjusted bars

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Liquidity flags

**Files:**
- Create: `src/signalbench/ingest/liquidity.py`, `tests/test_liquidity.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_liquidity.py`:

```python
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from signalbench.db.models import Price, Ticker
from signalbench.ingest.cdr import CdrEntry
from signalbench.ingest.liquidity import update_liquidity_flags
from signalbench.ingest.seed import seed_universe

DAY0 = date(2026, 8, 3)


def _entry(us: str, cdr: str) -> CdrEntry:
    return CdrEntry(
        us_symbol=us,
        cdr_symbol=cdr,
        price_symbol=f"{cdr}.NE",
        company_name=us,
        sector="Information Technology",
    )


def _ticker(session: Session, symbol: str) -> Ticker:
    return session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()


def _prices(session: Session, symbol: str, days: list[date], close: str, volume: int) -> None:
    ticker = _ticker(session, symbol)
    for day in days:
        session.add(
            Price(
                ticker_id=ticker.id,
                date=day,
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                adj_close=Decimal(close),
                volume=volume,
            )
        )
    session.commit()


def _days(count: int) -> list[date]:
    return [DAY0 + timedelta(days=offset) for offset in range(count)]


def test_liquidity_flags(session: Session) -> None:
    entries = [
        _entry("NVDA", "ZNVD"),
        _entry("EDGE", "ZEDG"),
        _entry("THIN", "ZTHN"),
        _entry("NEWB", "ZNEW"),
        _entry("STAL", "ZSTL"),
    ]
    seed_universe(session, entries)
    days = _days(20)
    _prices(session, "QQQ", days, "400", 1)
    _prices(session, "NVDA", days, "100", 1_000_000)  # median 100M: active
    _prices(session, "EDGE", days, "50", 1_000_000)  # exactly 50M: active
    _prices(session, "THIN", days, "10", 1_000_000)  # 10M: illiquid
    _prices(session, "NEWB", days[:19], "100", 1_000_000)  # 19 rows: too short
    _prices(session, "STAL", days, "100", 1_000_000)
    for cdr in ("ZNVD", "ZEDG", "ZTHN", "ZNEW"):
        _prices(session, cdr, days[-1:], "12", 0)  # zero volume still counts as priced
    _prices(session, "ZSTL", days[:10], "12", 5)  # last CDR price is older than 5 sessions

    result = update_liquidity_flags(session, entries)

    assert result.active == ["EDGE", "NVDA"]
    assert result.inactive == {
        "THIN": "us_illiquid",
        "NEWB": "us_history_short",
        "STAL": "cdr_no_recent_price",
    }
    assert _ticker(session, "ZNVD").active is True
    assert _ticker(session, "THIN").active is False
    assert _ticker(session, "ZTHN").active is False


def test_requires_benchmark_sessions(session: Session) -> None:
    seed_universe(session, [_entry("NVDA", "ZNVD")])
    with pytest.raises(RuntimeError, match="QQQ"):
        update_liquidity_flags(session, [_entry("NVDA", "ZNVD")])


def test_unseeded_entry_is_inactive(session: Session) -> None:
    seed_universe(session, [])
    _prices(session, "QQQ", _days(5), "400", 1)
    result = update_liquidity_flags(session, [_entry("NVDA", "ZNVD")])
    assert result.inactive == {"NVDA": "not_seeded"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_liquidity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.ingest.liquidity'`.

- [ ] **Step 3: Implement**

`src/signalbench/ingest/liquidity.py`:

```python
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median

from sqlmodel import Session, col, select

from signalbench.db.models import Price, Ticker
from signalbench.ingest.cdr import CdrEntry

US_MIN_MEDIAN_TRADED_VALUE_USD = Decimal(50_000_000)
TRADED_VALUE_SESSIONS = 20
CDR_RECENT_SESSIONS = 5
SESSION_CALENDAR_SYMBOL = "QQQ"


@dataclass(frozen=True)
class LiquidityResult:
    active: list[str]
    inactive: dict[str, str]


def median_traded_value(rows: list[Price]) -> Decimal | None:
    if len(rows) < TRADED_VALUE_SESSIONS:
        return None
    recent = sorted(rows, key=lambda row: row.date)[-TRADED_VALUE_SESSIONS:]
    return median([row.close * row.volume for row in recent])


def recent_sessions(session: Session, count: int) -> list[date]:
    """The last `count` session dates, taken from the QQQ price series."""
    calendar = session.exec(select(Ticker).where(Ticker.symbol == SESSION_CALENDAR_SYMBOL)).first()
    if calendar is None:
        return []
    dates = session.exec(
        select(col(Price.date))
        .where(Price.ticker_id == calendar.id)
        .order_by(col(Price.date).desc())
        .limit(count)
    ).all()
    return sorted(dates)


def update_liquidity_flags(session: Session, entries: list[CdrEntry]) -> LiquidityResult:
    sessions = recent_sessions(session, CDR_RECENT_SESSIONS)
    if len(sessions) < CDR_RECENT_SESSIONS:
        raise RuntimeError(
            f"Need {CDR_RECENT_SESSIONS} {SESSION_CALENDAR_SYMBOL} price rows to define recent "
            "sessions; run `signalbench ingest prices` first."
        )
    oldest_recent = sessions[0]
    by_symbol = {ticker.symbol: ticker for ticker in session.exec(select(Ticker)).all()}
    active: list[str] = []
    inactive: dict[str, str] = {}
    for entry in entries:
        us = by_symbol.get(entry.us_symbol)
        cdr = by_symbol.get(entry.cdr_symbol)
        if us is None or cdr is None:
            inactive[entry.us_symbol] = "not_seeded"
            continue
        us_rows = list(
            session.exec(
                select(Price)
                .where(Price.ticker_id == us.id)
                .order_by(col(Price.date).desc())
                .limit(TRADED_VALUE_SESSIONS)
            ).all()
        )
        traded_value = median_traded_value(us_rows)
        cdr_recent = session.exec(
            select(Price.id).where(Price.ticker_id == cdr.id, col(Price.date) >= oldest_recent)
        ).first()
        reason: str | None
        if traded_value is None:
            reason = "us_history_short"
        elif traded_value < US_MIN_MEDIAN_TRADED_VALUE_USD:
            reason = "us_illiquid"
        elif cdr_recent is None:
            reason = "cdr_no_recent_price"
        else:
            reason = None
        us.active = reason is None
        cdr.active = reason is None
        session.add(us)
        session.add(cdr)
        if reason is None:
            active.append(entry.us_symbol)
        else:
            inactive[entry.us_symbol] = reason
    session.commit()
    return LiquidityResult(active=sorted(active), inactive=inactive)
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_liquidity.py -v && uv run mypy src`
Expected: 3 passed; mypy `Success`.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/ingest/liquidity.py tests/test_liquidity.py
git commit -m "feat: nightly liquidity flags from US traded value and CDR freshness

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Filing text cleaning

**Files:**
- Create: `src/signalbench/ingest/text.py`, `tests/test_text.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_text.py`:

```python
from signalbench.ingest.text import (
    DOCUMENT_SEPARATOR,
    MAX_TEXT_CHARS,
    compose_filing_text,
    html_to_text,
    strip_boilerplate,
)


def test_html_to_text_drops_scripts_styles_and_xbrl_header() -> None:
    html = (
        "<html><head><style>p { color: red }</style><script>var x = 1;</script></head>"
        "<body><ix:header><ix:hidden>HIDDEN XBRL</ix:hidden></ix:header>"
        "<p>Item 2.02   Results of   Operations</p><div>Second paragraph</div></body></html>"
    )
    assert html_to_text(html) == "Item 2.02 Results of Operations\nSecond paragraph"


def test_strip_boilerplate_removes_signature_block() -> None:
    text = (
        "Item 8.01 Other Events\n"
        "The company announced a buyback.\n"
        "SIGNATURES\n"
        "Pursuant to the requirements of the Securities Exchange Act of 1934\n"
        "Apple Inc."
    )
    assert strip_boilerplate(text) == "Item 8.01 Other Events\nThe company announced a buyback."


def test_strip_boilerplate_removes_safe_harbor_heading_and_its_paragraph() -> None:
    text = (
        "Revenue grew 8%.\n"
        "Forward-Looking Statements\n"
        "This release contains statements about future plans.\n"
        "Contact: Investor Relations"
    )
    assert strip_boilerplate(text) == "Revenue grew 8%.\nContact: Investor Relations"


def test_strip_boilerplate_removes_inline_safe_harbor_paragraph() -> None:
    paragraph = (
        "This press release contains forward-looking statements within the meaning of the "
        "Private Securities Litigation Reform Act of 1995. These statements involve risks and "
        "uncertainties that could cause actual results to differ materially."
    )
    assert strip_boilerplate(f"Revenue grew 8%.\n{paragraph}") == "Revenue grew 8%."


def test_strip_boilerplate_keeps_ordinary_sentences() -> None:
    text = "We will not update forward-looking statements.\nRevenue grew 8%."
    assert strip_boilerplate(text) == text


def test_compose_joins_documents_and_caps_length() -> None:
    text = compose_filing_text("<p>Primary</p>", ["<p>Exhibit one</p>", "<p></p>"])
    assert text == f"Primary{DOCUMENT_SEPARATOR}Exhibit one"
    long_text = compose_filing_text("<p>" + "a" * (MAX_TEXT_CHARS + 500) + "</p>", [])
    assert len(long_text) == MAX_TEXT_CHARS
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_text.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.ingest.text'`.

- [ ] **Step 3: Implement**

`src/signalbench/ingest/text.py`:

```python
from bs4 import BeautifulSoup, Tag

MAX_TEXT_CHARS = 96_000  # about 24K tokens, inside Jev's 32K state budget
DOCUMENT_SEPARATOR = "\n\n---\n\n"
_SIGNATURE_HEADINGS = frozenset({"signature", "signatures"})
_FORWARD_LOOKING = "forward-looking statements"
_HEADING_MAX_CHARS = 80


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "ix:header"]):
        if isinstance(tag, Tag):
            tag.decompose()
    lines = (" ".join(line.split()) for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def strip_boilerplate(text: str) -> str:
    lines = text.split("\n")
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip().lower() in _SIGNATURE_HEADINGS:
            lines = lines[:index]
            break
    kept: list[str] = []
    skip_next = False
    for line in lines:
        if skip_next:
            skip_next = False
            continue
        lowered = line.lower()
        if _FORWARD_LOOKING in lowered:
            if _is_heading(line):
                skip_next = True
                continue
            if "risks" in lowered or "uncertainties" in lowered:
                continue
        kept.append(line)
    return "\n".join(kept)


def clean_document(html: str) -> str:
    return strip_boilerplate(html_to_text(html))


def compose_filing_text(primary_html: str, exhibit_htmls: list[str]) -> str:
    parts = [clean_document(primary_html), *(clean_document(html) for html in exhibit_htmls)]
    return DOCUMENT_SEPARATOR.join(part for part in parts if part)[:MAX_TEXT_CHARS]


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) <= _HEADING_MAX_CHARS and not stripped.endswith(".")
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_text.py -v && uv run mypy src`
Expected: 6 passed; mypy `Success`.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/ingest/text.py tests/test_text.py
git commit -m "feat: clean filing HTML into capped plain text

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: EDGAR upgrade — paging, acceptance time, items, 8-K/A, exhibits

**Files:**
- Rewrite: `src/signalbench/ingest/edgar.py`, `tests/test_edgar.py`
- Modify: `tests/fixtures/edgar_submissions.json`, `tests/fixtures/edgar_8k.html`
- Create: `tests/fixtures/edgar_index.htm`, `tests/fixtures/edgar_ex991.htm`

- [ ] **Step 1: Update and add the fixtures**

`tests/fixtures/edgar_submissions.json`:

```json
{
  "cik": "0000320193",
  "filings": {
    "recent": {
      "accessionNumber": ["0000320193-24-000001"],
      "form": ["8-K"],
      "filingDate": ["2024-01-15"],
      "acceptanceDateTime": ["2024-01-15T21:30:05.000Z"],
      "items": ["2.02,9.01"],
      "primaryDocument": ["aapl-20240115.htm"]
    },
    "files": []
  }
}
```

`tests/fixtures/edgar_8k.html`:

```html
<html><head><style>p { margin: 0 }</style><script>var tracking = 1;</script></head><body>
<ix:header><ix:hidden>HIDDEN XBRL</ix:hidden></ix:header>
<p>Apple 8-K Item 2.02 Results of Operations and Financial Condition.</p>
<p>The information in Exhibit 99.1 is furnished herewith.</p>
<p>SIGNATURE</p>
<p>Pursuant to the requirements of the Securities Exchange Act of 1934, the registrant has duly caused this report to be signed.</p>
</body></html>
```

`tests/fixtures/edgar_index.htm`:

```html
<html><body>
<table class="tableFile" summary="Document Format Files">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>8-K</td><td><a href="/Archives/edgar/data/320193/000032019324000001/aapl-20240115.htm">aapl-20240115.htm</a></td><td>8-K</td><td>38350</td></tr>
<tr><td>2</td><td>EX-99.1</td><td><a href="/Archives/edgar/data/320193/000032019324000001/a8-kex991.htm">a8-kex991.htm</a></td><td>EX-99.1</td><td>173484</td></tr>
<tr><td>3</td><td>GRAPHIC</td><td><a href="/Archives/edgar/data/320193/000032019324000001/logo.jpg">logo.jpg</a></td><td>GRAPHIC</td><td>1264</td></tr>
</table>
<table class="tableFile" summary="Data Files">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>4</td><td>XBRL SCHEMA</td><td><a href="/Archives/edgar/data/320193/000032019324000001/aapl.xsd">aapl.xsd</a></td><td>EX-101.SCH</td><td>3650</td></tr>
</table>
</body></html>
```

`tests/fixtures/edgar_ex991.htm`:

```html
<html><body>
<p>Apple reports record first quarter revenue.</p>
<p>Forward-Looking Statements</p>
<p>This press release contains forward-looking statements that involve risks and uncertainties.</p>
</body></html>
```

- [ ] **Step 2: Write the failing tests**

Replace `tests/test_edgar.py`:

```python
import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from sqlmodel import Session, SQLModel, create_engine, select

from signalbench.db.models import DocType, DocumentTicker, RawDocument, Ticker
from signalbench.ingest.edgar import (
    exhibit_documents,
    ingest_eight_ks_for_symbol,
    parse_acceptance,
)
from signalbench.ingest.ratelimit import RateLimiter
from signalbench.ingest.text import DOCUMENT_SEPARATOR

FIXTURES = Path(__file__).parent / "fixtures"
UA = "SignalBench/0.1 (test@example.com)"
FAST = RateLimiter(calls=1_000_000, period=1.0)
ACCESSION = "0000320193-24-000001"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _routes(overrides: dict[str, str] | None = None) -> dict[str, str]:
    routes = {
        "company_tickers.json": _fixture("company_tickers.json"),
        "submissions/CIK0000320193.json": _fixture("edgar_submissions.json"),
        "aapl-20240115.htm": _fixture("edgar_8k.html"),
        f"{ACCESSION}-index.htm": _fixture("edgar_index.htm"),
        "a8-kex991.htm": _fixture("edgar_ex991.htm"),
    }
    routes.update(overrides or {})
    return routes


def _client(routes: dict[str, str], requested: list[str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if requested is not None:
            requested.append(url)
        for suffix, body in routes.items():
            if url.endswith(suffix):
                return httpx.Response(200, text=body)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _add_aapl(session: Session) -> Ticker:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    return ticker


def _columns(rows: list[tuple[str, str, str, str, str, str]]) -> dict[str, list[str]]:
    keys = ["accessionNumber", "form", "filingDate", "acceptanceDateTime", "items", "primaryDocument"]
    return {key: [row[index] for row in rows] for index, key in enumerate(keys)}


def test_ingest_stores_one_8k_and_issuer_link(session: Session) -> None:
    ticker = _add_aapl(session)
    created = ingest_eight_ks_for_symbol(session, "AAPL", _client(_routes()), UA, limiter=FAST)
    assert created == 1
    doc = session.exec(select(RawDocument)).one()
    assert doc.source == "sec_edgar"
    assert doc.external_id == ACCESSION
    assert doc.doc_type is DocType.eight_k
    assert "Item 2.02" in doc.raw_text
    links = session.exec(select(DocumentTicker)).all()
    assert [(link.document_id, link.ticker_id) for link in links] == [(doc.id, ticker.id)]


def test_ingest_records_acceptance_items_and_exhibit_text(session: Session) -> None:
    _add_aapl(session)
    ingest_eight_ks_for_symbol(session, "AAPL", _client(_routes()), UA, limiter=FAST)
    doc = session.exec(select(RawDocument)).one()
    accepted = datetime(2024, 1, 15, 21, 30, 5, tzinfo=UTC)
    assert doc.acceptance_at == accepted
    assert doc.published_at == accepted
    assert doc.items == "2.02,9.01"
    assert doc.text is not None
    assert "Item 2.02 Results of Operations" in doc.text
    assert DOCUMENT_SEPARATOR in doc.text
    assert "record first quarter revenue" in doc.text
    assert "risks and uncertainties" not in doc.text
    assert "Pursuant to the requirements" not in doc.text
    assert "HIDDEN XBRL" not in doc.text


def test_missing_filing_index_means_no_exhibits(session: Session) -> None:
    _add_aapl(session)
    routes = _routes()
    del routes[f"{ACCESSION}-index.htm"]
    ingest_eight_ks_for_symbol(session, "AAPL", _client(routes), UA, limiter=FAST)
    doc = session.exec(select(RawDocument)).one()
    assert doc.text is not None
    assert DOCUMENT_SEPARATOR not in doc.text


def test_ingest_retries_archive_on_429(session: Session) -> None:
    _add_aapl(session)
    routes = _routes()
    hits = {"archive": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("aapl-20240115.htm"):
            hits["archive"] += 1
            if hits["archive"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
        for suffix, body in routes.items():
            if url.endswith(suffix):
                return httpx.Response(200, text=body)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert ingest_eight_ks_for_symbol(session, "AAPL", client, UA, limiter=FAST) == 1
    assert hits["archive"] == 2


def test_second_ingest_is_noop(session: Session) -> None:
    _add_aapl(session)
    client = _client(_routes())
    ingest_eight_ks_for_symbol(session, "AAPL", client, UA, limiter=FAST)
    assert ingest_eight_ks_for_symbol(session, "AAPL", client, UA, limiter=FAST) == 0
    assert len(session.exec(select(RawDocument)).all()) == 1


def test_ingest_commits_so_filings_survive_session_close() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        _add_aapl(session)
        ingest_eight_ks_for_symbol(session, "AAPL", _client(_routes()), UA, limiter=FAST)
    with Session(engine) as session:
        assert len(session.exec(select(RawDocument)).all()) == 1
        assert len(session.exec(select(DocumentTicker)).all()) == 1


def test_pages_older_filings_and_skips_pages_before_since(session: Session) -> None:
    _add_aapl(session)
    submissions = {
        "cik": "0000320193",
        "filings": {
            "recent": _columns([]),
            "files": [
                {"name": "CIK0000320193-submissions-001.json", "filingTo": "2017-12-31"},
                {"name": "CIK0000320193-submissions-002.json", "filingTo": "2012-12-31"},
            ],
        },
    }
    page = _columns(
        [
            (
                "0000320193-17-000009",
                "8-K",
                "2017-05-02",
                "2017-05-02T20:31:00.000Z",
                "2.02,9.01",
                "a8-k20170502.htm",
            )
        ]
    )
    requested: list[str] = []
    routes = _routes(
        {
            "submissions/CIK0000320193.json": json.dumps(submissions),
            "CIK0000320193-submissions-001.json": json.dumps(page),
            "a8-k20170502.htm": "<html><body><p>Item 2.02 fiscal 2017 results</p></body></html>",
        }
    )
    created = ingest_eight_ks_for_symbol(
        session, "AAPL", _client(routes, requested), UA, since=date(2016, 1, 1), limiter=FAST
    )
    assert created == 1
    assert any(url.endswith("submissions-001.json") for url in requested)
    assert not any(url.endswith("submissions-002.json") for url in requested)


def test_keeps_8k_amendments_and_skips_other_forms_and_old_filings(session: Session) -> None:
    _add_aapl(session)
    recent = _columns(
        [
            (ACCESSION, "8-K", "2024-01-15", "2024-01-15T21:30:05.000Z", "2.02,9.01", "aapl-20240115.htm"),
            ("0000320193-24-000002", "8-K/A", "2024-02-01", "2024-02-01T12:00:00.000Z", "5.02", "aapl-8ka.htm"),
            ("0000320193-24-000003", "10-Q", "2024-02-02", "2024-02-02T12:00:00.000Z", "", "aapl-10q.htm"),
            ("0000320193-15-000004", "8-K", "2015-06-01", "2015-06-01T12:00:00.000Z", "8.01", "aapl-2015.htm"),
        ]
    )
    routes = _routes(
        {
            "submissions/CIK0000320193.json": json.dumps({"cik": "0000320193", "filings": {"recent": recent, "files": []}}),
            "aapl-8ka.htm": "<html><body><p>Item 5.02 amendment</p></body></html>",
        }
    )
    created = ingest_eight_ks_for_symbol(
        session, "AAPL", _client(routes), UA, since=date(2016, 1, 1), limiter=FAST
    )
    assert created == 2
    ids = {doc.external_id for doc in session.exec(select(RawDocument)).all()}
    assert ids == {ACCESSION, "0000320193-24-000002"}


def test_parse_acceptance() -> None:
    assert parse_acceptance("2026-07-30T20:30:28.000Z") == datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)
    assert parse_acceptance("") is None


def test_exhibit_documents_lists_only_ex99_in_order() -> None:
    assert exhibit_documents(_fixture("edgar_index.htm")) == ["a8-kex991.htm"]
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_edgar.py -v`
Expected: FAIL with `ImportError: cannot import name 'exhibit_documents'`.

- [ ] **Step 4: Implement**

Replace `src/signalbench/ingest/edgar.py`:

```python
import json
import logging
import time as time_module
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, cast

import httpx
from bs4 import BeautifulSoup, Tag
from sqlmodel import Session, col, select

from signalbench.db.models import DocType, DocumentTicker, RawDocument, Ticker
from signalbench.ingest.ratelimit import RateLimiter
from signalbench.ingest.text import compose_filing_text

logger = logging.getLogger(__name__)

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik10}.json"
SUBMISSIONS_PAGE_URL_TEMPLATE = "https://data.sec.gov/submissions/{name}"
ARCHIVE_URL_TEMPLATE = (
    "https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/{accession_nodash}/{document}"
)
FILING_INDEX_URL_TEMPLATE = (
    "https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/{accession_nodash}/{accession}-index.htm"
)
EIGHT_K_FORMS = frozenset({"8-K", "8-K/A"})
DEFAULT_SINCE = date(2016, 1, 1)
# SEC allows 10 requests/second; stay under it.
SEC_LIMITER = RateLimiter(calls=8, period=1.0)


@dataclass(frozen=True)
class FilingRef:
    accession_number: str
    form: str
    filing_date: date
    acceptance_at: datetime | None
    items: str
    primary_document: str

    @property
    def available_on(self) -> date:
        return self.acceptance_at.date() if self.acceptance_at is not None else self.filing_date


def parse_acceptance(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def refs_from_columns(columns: dict[str, list[Any]]) -> list[FilingRef]:
    count = len(columns["accessionNumber"])
    acceptance = columns.get("acceptanceDateTime", [""] * count)
    items = columns.get("items", [""] * count)
    return [
        FilingRef(
            accession_number=str(columns["accessionNumber"][index]),
            form=str(columns["form"][index]),
            filing_date=date.fromisoformat(str(columns["filingDate"][index])),
            acceptance_at=parse_acceptance(str(acceptance[index] or "")),
            items=str(items[index] or ""),
            primary_document=str(columns["primaryDocument"][index]),
        )
        for index in range(count)
    ]


def exhibit_documents(index_html: str) -> list[str]:
    """File names of EX-99.* documents listed in a filing's -index.htm page, in order."""
    soup = BeautifulSoup(index_html, "lxml")
    names: list[str] = []
    for table in soup.find_all("table", class_="tableFile"):
        if not isinstance(table, Tag):
            continue
        for row in table.find_all("tr"):
            if not isinstance(row, Tag):
                continue
            cells = row.find_all("td")
            if len(cells) < 4 or not cells[3].get_text(strip=True).upper().startswith("EX-99"):
                continue
            link = cells[2].find("a")
            if isinstance(link, Tag) and link.get("href"):
                names.append(str(link["href"]).rsplit("/", 1)[-1])
    return names


def filing_refs(
    client: httpx.Client,
    headers: dict[str, str],
    cik10: str,
    since: date,
    limiter: RateLimiter,
) -> list[FilingRef]:
    submissions = _get_json(client, SUBMISSIONS_URL_TEMPLATE.format(cik10=cik10), headers, limiter)
    refs = refs_from_columns(submissions["filings"]["recent"])
    for page in submissions["filings"].get("files", []):
        if date.fromisoformat(str(page["filingTo"])) < since:
            continue
        url = SUBMISSIONS_PAGE_URL_TEMPLATE.format(name=page["name"])
        refs.extend(refs_from_columns(_get_json(client, url, headers, limiter)))
    return refs


def ingest_eight_ks_for_symbol(
    session: Session,
    symbol: str,
    client: httpx.Client,
    user_agent: str,
    since: date = DEFAULT_SINCE,
    cik10: str | None = None,
    limiter: RateLimiter = SEC_LIMITER,
) -> int:
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
    headers = {"User-Agent": user_agent}
    resolved_cik = cik10 or _lookup_cik_for_symbol(client, headers, symbol, limiter)

    created = 0
    for ref in filing_refs(client, headers, resolved_cik, since, limiter):
        if ref.form not in EIGHT_K_FORMS or ref.available_on < since:
            continue
        existing = session.exec(
            select(RawDocument).where(
                RawDocument.source == "sec_edgar",
                RawDocument.external_id == ref.accession_number,
            )
        ).first()
        if existing is not None:
            continue

        primary_html = _get_text(client, _archive_url(resolved_cik, ref), headers, limiter)
        exhibits = _fetch_exhibits(client, headers, resolved_cik, ref, limiter)
        document = RawDocument(
            source="sec_edgar",
            external_id=ref.accession_number,
            doc_type=DocType.eight_k,
            url=_archive_url(resolved_cik, ref),
            title=ref.primary_document,
            raw_text=primary_html,
            text=compose_filing_text(primary_html, exhibits),
            items=ref.items or None,
            acceptance_at=ref.acceptance_at,
            published_at=ref.acceptance_at
            or datetime.combine(ref.filing_date, time.min, tzinfo=UTC),
        )
        session.add(document)
        session.flush()
        session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
        session.commit()
        created += 1

    return created


def backfill_filing_text(
    session: Session,
    symbol: str,
    client: httpx.Client,
    user_agent: str,
    cik10: str | None = None,
    limiter: RateLimiter = SEC_LIMITER,
) -> int:
    """Fill acceptance_at, items, and text for SEC documents stored before spec 01."""
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
    document_ids = list(
        session.exec(
            select(DocumentTicker.document_id).where(DocumentTicker.ticker_id == ticker.id)
        ).all()
    )
    if not document_ids:
        return 0
    documents = [
        document
        for document in session.exec(
            select(RawDocument).where(
                RawDocument.source == "sec_edgar",
                col(RawDocument.id).in_(document_ids),
            )
        ).all()
        if document.text is None
    ]
    if not documents:
        return 0

    headers = {"User-Agent": user_agent}
    resolved_cik = cik10 or _lookup_cik_for_symbol(client, headers, symbol, limiter)
    since = min(document.published_at.date() for document in documents)
    refs = {
        ref.accession_number: ref
        for ref in filing_refs(client, headers, resolved_cik, since, limiter)
    }
    updated = 0
    for document in documents:
        ref = refs.get(document.external_id)
        if ref is None:
            logger.warning("No submissions entry for %s; text not backfilled", document.external_id)
            continue
        exhibits = _fetch_exhibits(client, headers, resolved_cik, ref, limiter)
        document.text = compose_filing_text(document.raw_text, exhibits)
        document.items = ref.items or None
        document.acceptance_at = ref.acceptance_at
        if ref.acceptance_at is not None:
            document.published_at = ref.acceptance_at
        session.add(document)
        session.commit()
        updated += 1
    return updated


def _fetch_exhibits(
    client: httpx.Client,
    headers: dict[str, str],
    cik10: str,
    ref: FilingRef,
    limiter: RateLimiter,
) -> list[str]:
    index_url = FILING_INDEX_URL_TEMPLATE.format(
        cik_no_zeros=str(int(cik10)),
        accession_nodash=ref.accession_number.replace("-", ""),
        accession=ref.accession_number,
    )
    try:
        index_html = _get_text(client, index_url, headers, limiter)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            logger.warning("No filing index for %s", ref.accession_number)
            return []
        raise
    return [
        _get_text(client, _archive_url(cik10, ref, name), headers, limiter)
        for name in exhibit_documents(index_html)
    ]


def _archive_url(cik10: str, ref: FilingRef, document: str | None = None) -> str:
    return ARCHIVE_URL_TEMPLATE.format(
        cik_no_zeros=str(int(cik10)),
        accession_nodash=ref.accession_number.replace("-", ""),
        document=document or ref.primary_document,
    )


def _lookup_cik_for_symbol(
    client: httpx.Client,
    headers: dict[str, str],
    symbol: str,
    limiter: RateLimiter,
) -> str:
    payload = _company_tickers(client, headers, limiter)
    normalized_symbol = symbol.upper()
    for company in payload.values():
        if company["ticker"].upper() == normalized_symbol:
            return f"{int(company['cik_str']):010d}"
    raise ValueError(f"Ticker not found in SEC company_tickers.json: {symbol}")


_COMPANY_TICKERS: dict[str, Any] | None = None


def _company_tickers(
    client: httpx.Client,
    headers: dict[str, str],
    limiter: RateLimiter,
) -> dict[str, Any]:
    global _COMPANY_TICKERS
    if _COMPANY_TICKERS is None:
        _COMPANY_TICKERS = _get_json(client, COMPANY_TICKERS_URL, headers, limiter)
    return _COMPANY_TICKERS


def _get_json(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    limiter: RateLimiter,
) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_get(client, url, headers, limiter).text))


def _get_text(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    limiter: RateLimiter,
) -> str:
    return _get(client, url, headers, limiter).text


def _get(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    limiter: RateLimiter,
    retries: int = 5,
) -> httpx.Response:
    last_error: httpx.HTTPStatusError | None = None
    for _attempt in range(retries):
        limiter.wait()
        response = client.get(url, headers=headers)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        last_error = httpx.HTTPStatusError(
            f"429 Too Many Requests for {url}",
            request=response.request,
            response=response,
        )
        retry_after = response.headers.get("Retry-After")
        try:
            delay = max(10.0, float(retry_after)) if retry_after is not None else 10.0
        except ValueError:
            delay = 10.0
        if retry_after == "0":
            delay = 0.0
        time_module.sleep(delay)
    assert last_error is not None
    raise last_error
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_edgar.py -v && uv run mypy src && uv run ruff check .`
Expected: 10 passed; mypy `Success`; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/ingest/edgar.py tests/test_edgar.py tests/fixtures/edgar_submissions.json tests/fixtures/edgar_8k.html tests/fixtures/edgar_index.htm tests/fixtures/edgar_ex991.htm
git commit -m "feat: 8-K ingest with paging, acceptance time, items, amendments, exhibits

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Backfill text for already-stored filings

**Files:**
- Modify: `tests/test_edgar.py`

`backfill_filing_text` was implemented in Task 13. This task proves it works.

- [ ] **Step 1: Write the test**

Add `backfill_filing_text` to the `signalbench.ingest.edgar` import list in `tests/test_edgar.py`, then append:

```python
def test_backfill_filing_text_fills_rows_stored_before_spec_01(session: Session) -> None:
    ticker = _add_aapl(session)
    legacy = RawDocument(
        source="sec_edgar",
        external_id=ACCESSION,
        doc_type=DocType.eight_k,
        raw_text=_fixture("edgar_8k.html"),
        published_at=datetime(2024, 1, 15, tzinfo=UTC),
    )
    session.add(legacy)
    session.flush()
    session.add(DocumentTicker(document_id=legacy.id, ticker_id=ticker.id))
    session.commit()

    client = _client(_routes())
    assert backfill_filing_text(session, "AAPL", client, UA, limiter=FAST) == 1
    session.refresh(legacy)
    assert legacy.text is not None
    assert "record first quarter revenue" in legacy.text
    assert legacy.items == "2.02,9.01"
    assert legacy.acceptance_at == datetime(2024, 1, 15, 21, 30, 5, tzinfo=UTC)
    assert legacy.published_at == legacy.acceptance_at
    assert backfill_filing_text(session, "AAPL", client, UA, limiter=FAST) == 0
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_edgar.py::test_backfill_filing_text_fills_rows_stored_before_spec_01 -v`
Expected: PASS. If it fails, fix `backfill_filing_text` in `src/signalbench/ingest/edgar.py`, not the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_edgar.py
git commit -m "test: backfill text for filings stored before spec 01

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Finnhub client

**Files:**
- Create: `src/signalbench/ingest/finnhub.py`, `tests/test_finnhub.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_finnhub.py`:

```python
import httpx
import pytest

from signalbench.ingest.finnhub import FinnhubClient
from signalbench.ingest.ratelimit import RateLimiter

FAST = RateLimiter(calls=1_000_000, period=1.0)


def _finnhub(handler: object, sleeps: list[float] | None = None) -> FinnhubClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    record = sleeps if sleeps is not None else []
    return FinnhubClient(
        "secret-key",
        httpx.Client(transport=transport),
        limiter=FAST,
        sleep=record.append,
    )


def test_sends_token_in_header_not_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{"id": 1}])

    result = _finnhub(handler).get("/company-news", {"symbol": "NVDA"})
    assert result == [{"id": 1}]
    assert seen[0].headers["X-Finnhub-Token"] == "secret-key"
    assert "secret-key" not in str(seen[0].url)
    assert str(seen[0].url) == "https://finnhub.io/api/v1/company-news?symbol=NVDA"


def test_retries_429_with_backoff() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429)
        return httpx.Response(200, json={"ok": True})

    assert _finnhub(handler, sleeps).get("/x", {}) == {"ok": True}
    assert sleeps == [1.0, 2.0]


def test_gives_up_after_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    with pytest.raises(httpx.HTTPStatusError, match="429"):
        _finnhub(handler).get("/x", {})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_finnhub.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.ingest.finnhub'`.

- [ ] **Step 3: Implement**

`src/signalbench/ingest/finnhub.py`:

```python
import time
from collections.abc import Callable
from typing import Any

import httpx

from signalbench.ingest.ratelimit import RateLimiter

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
# The free tier allows 60 calls/minute; stay under it.
FINNHUB_CALLS_PER_MINUTE = 50


class FinnhubClient:
    def __init__(
        self,
        api_key: str,
        client: httpx.Client,
        limiter: RateLimiter | None = None,
        retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._limiter = limiter or RateLimiter(calls=FINNHUB_CALLS_PER_MINUTE, period=60.0)
        self._retries = retries
        self._sleep = sleep

    def get(self, path: str, params: dict[str, str]) -> Any:
        last: httpx.Response | None = None
        for attempt in range(self._retries):
            self._limiter.wait()
            response = self._client.get(
                f"{FINNHUB_BASE_URL}{path}",
                params=params,
                headers={"X-Finnhub-Token": self._api_key},
            )
            if response.status_code == 429:
                last = response
                self._sleep(min(60.0, 2.0**attempt))
                continue
            response.raise_for_status()
            return response.json()
        assert last is not None
        raise httpx.HTTPStatusError(
            f"429 Too Many Requests after {self._retries} attempts: {path}",
            request=last.request,
            response=last,
        )
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_finnhub.py -v && uv run mypy src`
Expected: 3 passed; mypy `Success`.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/ingest/finnhub.py tests/test_finnhub.py
git commit -m "feat: rate-limited Finnhub client with 429 backoff

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Finnhub company news

**Files:**
- Create: `src/signalbench/ingest/news.py`, `tests/test_news.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_news.py`:

```python
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlmodel import Session, select

from signalbench.db.models import DocType, DocumentTicker, RawDocument, Ticker
from signalbench.ingest.finnhub import FinnhubClient
from signalbench.ingest.news import NEWS_SOURCE, ingest_company_news, last_news_date, news_windows
from signalbench.ingest.ratelimit import RateLimiter

FAST = RateLimiter(calls=1_000_000, period=1.0)
TODAY = date(2026, 9, 22)
ARTICLES = [
    {"id": 101, "datetime": 1789000000, "headline": "Nvidia wins contract", "summary": "Big deal.", "url": "https://example.com/a", "related": "NVDA,MSFT"},
    {"id": 102, "datetime": 1789003600, "headline": "Nvidia CFO speaks", "summary": "", "url": "", "related": "NVDA"},
    {"id": 103, "datetime": 1789007200, "headline": "", "summary": "No headline", "url": "https://example.com/c", "related": "NVDA"},
]


def _finnhub(articles: list[dict[str, object]]) -> FinnhubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=articles)

    return FinnhubClient("k", httpx.Client(transport=httpx.MockTransport(handler)), limiter=FAST)


def _ticker(session: Session, symbol: str) -> Ticker:
    ticker = Ticker(symbol=symbol, company_name=symbol)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    return ticker


def test_ingest_stores_headline_summary_and_links_queried_symbol(session: Session) -> None:
    nvda = _ticker(session, "NVDA")
    created = ingest_company_news(session, _finnhub(ARTICLES), nvda, TODAY - timedelta(days=5), TODAY)
    assert created == 2
    docs = {doc.external_id: doc for doc in session.exec(select(RawDocument)).all()}
    assert set(docs) == {"101", "102"}
    first = docs["101"]
    assert first.source == NEWS_SOURCE
    assert first.doc_type is DocType.news
    assert first.text == "Nvidia wins contract\n\nBig deal."
    assert first.url == "https://example.com/a"
    assert first.published_at == datetime.fromtimestamp(1789000000, tz=UTC)
    assert first.acceptance_at is None
    assert docs["102"].text == "Nvidia CFO speaks"
    assert docs["102"].url is None
    links = session.exec(select(DocumentTicker)).all()
    assert {link.ticker_id for link in links} == {nvda.id}


def test_rerun_is_noop_and_other_symbol_gets_link_without_duplicate(session: Session) -> None:
    nvda = _ticker(session, "NVDA")
    msft = _ticker(session, "MSFT")
    finnhub = _finnhub(ARTICLES[:1])
    ingest_company_news(session, finnhub, nvda, TODAY, TODAY)
    assert ingest_company_news(session, finnhub, nvda, TODAY, TODAY) == 0
    assert ingest_company_news(session, finnhub, msft, TODAY, TODAY) == 0
    assert len(session.exec(select(RawDocument)).all()) == 1
    assert {link.ticker_id for link in session.exec(select(DocumentTicker)).all()} == {nvda.id, msft.id}


def test_last_news_date(session: Session) -> None:
    nvda = _ticker(session, "NVDA")
    assert last_news_date(session, nvda) is None
    ingest_company_news(session, _finnhub(ARTICLES[:2]), nvda, TODAY, TODAY)
    assert last_news_date(session, nvda) == datetime.fromtimestamp(1789003600, tz=UTC).date()


def test_news_windows_backfill_a_year_in_contiguous_chunks() -> None:
    windows = news_windows(None, TODAY)
    assert windows[0][0] == TODAY - timedelta(days=365)
    assert windows[-1][1] == TODAY
    assert all(end - start <= timedelta(days=29) for start, end in windows)
    assert all(nxt[0] == prev[1] + timedelta(days=1) for prev, nxt in zip(windows, windows[1:]))


def test_news_windows_overlap_two_days_after_last_article() -> None:
    assert news_windows(date(2026, 9, 20), TODAY) == [(date(2026, 9, 18), TODAY)]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_news.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.ingest.news'`.

- [ ] **Step 3: Implement**

`src/signalbench/ingest/news.py`:

```python
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from sqlalchemy import func
from sqlmodel import Session, col, select

from signalbench.db.models import DocType, DocumentTicker, RawDocument, Ticker
from signalbench.ingest.finnhub import FinnhubClient

NEWS_SOURCE = "finnhub"
NEWS_BACKFILL_DAYS = 365
NEWS_OVERLAP_DAYS = 2
NEWS_CHUNK_DAYS = 30


def news_windows(last_published: date | None, today: date) -> list[tuple[date, date]]:
    """Date ranges to request: a year of backfill, or from 2 days before the last article."""
    if last_published is None:
        start = today - timedelta(days=NEWS_BACKFILL_DAYS)
    else:
        start = last_published - timedelta(days=NEWS_OVERLAP_DAYS)
    windows: list[tuple[date, date]] = []
    while start <= today:
        end = min(today, start + timedelta(days=NEWS_CHUNK_DAYS - 1))
        windows.append((start, end))
        start = end + timedelta(days=1)
    return windows


def last_news_date(session: Session, ticker: Ticker) -> date | None:
    document_ids = list(
        session.exec(
            select(DocumentTicker.document_id).where(DocumentTicker.ticker_id == ticker.id)
        ).all()
    )
    if not document_ids:
        return None
    latest: datetime | None = session.exec(
        select(func.max(RawDocument.published_at)).where(
            RawDocument.source == NEWS_SOURCE,
            col(RawDocument.id).in_(document_ids),
        )
    ).one()
    return None if latest is None else latest.date()


def ingest_company_news(
    session: Session,
    finnhub: FinnhubClient,
    ticker: Ticker,
    start: date,
    end: date,
) -> int:
    articles = cast(
        list[dict[str, Any]],
        finnhub.get(
            "/company-news",
            {"symbol": ticker.symbol, "from": start.isoformat(), "to": end.isoformat()},
        ),
    )
    external_ids = [str(article["id"]) for article in articles]
    known: dict[str, uuid.UUID] = {}
    if external_ids:
        known = {
            document.external_id: document.id
            for document in session.exec(
                select(RawDocument).where(
                    RawDocument.source == NEWS_SOURCE,
                    col(RawDocument.external_id).in_(external_ids),
                )
            ).all()
        }
    linked: set[uuid.UUID] = set()
    if known:
        linked = set(
            session.exec(
                select(DocumentTicker.document_id).where(
                    DocumentTicker.ticker_id == ticker.id,
                    col(DocumentTicker.document_id).in_(list(known.values())),
                )
            ).all()
        )

    created = 0
    for article in articles:
        external_id = str(article["id"])
        if external_id in known:
            document_id = known[external_id]
            if document_id not in linked:
                session.add(DocumentTicker(document_id=document_id, ticker_id=ticker.id))
                linked.add(document_id)
            continue
        headline = str(article.get("headline") or "").strip()
        if not headline:
            continue
        summary = str(article.get("summary") or "").strip()
        text = f"{headline}\n\n{summary}" if summary else headline
        document = RawDocument(
            source=NEWS_SOURCE,
            external_id=external_id,
            doc_type=DocType.news,
            url=str(article["url"]) if article.get("url") else None,
            title=headline,
            raw_text=text,
            text=text,
            published_at=datetime.fromtimestamp(int(article["datetime"]), tz=UTC),
        )
        session.add(document)
        session.flush()
        session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
        known[external_id] = document.id
        linked.add(document.id)
        created += 1
    session.commit()
    return created
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_news.py -v && uv run mypy src`
Expected: 5 passed; mypy `Success`.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/ingest/news.py tests/test_news.py
git commit -m "feat: Finnhub company news ingest linked to the queried symbol

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 17: Earnings dates

**Files:**
- Create: `src/signalbench/ingest/earnings.py`, `tests/test_earnings.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_earnings.py`:

```python
from datetime import UTC, date, datetime

import httpx
from sqlmodel import Session, select

from signalbench.db.models import DocType, DocumentTicker, EarningsEvent, RawDocument, Ticker
from signalbench.ingest.earnings import (
    FINNHUB_EARNINGS_SOURCE,
    SEC_EARNINGS_SOURCE,
    cluster_earliest,
    earnings_dates,
    ingest_finnhub_calendar,
    sync_sec_earnings_events,
)
from signalbench.ingest.finnhub import FinnhubClient
from signalbench.ingest.ratelimit import RateLimiter

FAST = RateLimiter(calls=1_000_000, period=1.0)


def _ticker(session: Session, symbol: str) -> Ticker:
    ticker = Ticker(symbol=symbol, company_name=symbol)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    return ticker


def _filing(session: Session, ticker: Ticker, accession: str, accepted: datetime, items: str) -> None:
    document = RawDocument(
        source="sec_edgar",
        external_id=accession,
        doc_type=DocType.eight_k,
        raw_text="x",
        published_at=accepted,
        acceptance_at=accepted,
        items=items,
    )
    session.add(document)
    session.flush()
    session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
    session.commit()


def _events(session: Session, source: str) -> list[tuple[str, date]]:
    tickers = {ticker.id: ticker.symbol for ticker in session.exec(select(Ticker)).all()}
    rows = session.exec(select(EarningsEvent).where(EarningsEvent.source == source)).all()
    return sorted((tickers[row.ticker_id], row.event_date) for row in rows)


def test_sec_events_use_new_york_date_of_item_202_filings(session: Session) -> None:
    aapl = _ticker(session, "AAPL")
    _filing(session, aapl, "a-1", datetime(2024, 1, 15, 21, 30, tzinfo=UTC), "2.02,9.01")
    _filing(session, aapl, "a-2", datetime(2024, 4, 1, 2, 0, tzinfo=UTC), "2.02")  # 22:00 ET on Mar 31
    _filing(session, aapl, "a-3", datetime(2024, 5, 1, 12, 0, tzinfo=UTC), "5.02")
    assert sync_sec_earnings_events(session) == 2
    assert sync_sec_earnings_events(session) == 0
    assert _events(session, SEC_EARNINGS_SOURCE) == [
        ("AAPL", date(2024, 1, 15)),
        ("AAPL", date(2024, 3, 31)),
    ]


def test_finnhub_calendar_replaces_its_future_window(session: Session) -> None:
    _ticker(session, "NVDA")
    payloads = [
        {"earningsCalendar": [{"symbol": "NVDA", "date": "2026-10-01"}, {"symbol": "ZZZZ", "date": "2026-10-02"}]},
        {"earningsCalendar": [{"symbol": "NVDA", "date": "2026-10-03"}]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payloads.pop(0))

    finnhub = FinnhubClient("k", httpx.Client(transport=httpx.MockTransport(handler)), limiter=FAST)
    today = date(2026, 9, 22)
    assert ingest_finnhub_calendar(session, finnhub, today) == 1
    assert ingest_finnhub_calendar(session, finnhub, today) == 1
    assert _events(session, FINNHUB_EARNINGS_SOURCE) == [("NVDA", date(2026, 10, 3))]


def test_cluster_earliest() -> None:
    days = [date(2026, 1, 5), date(2026, 1, 3), date(2026, 1, 1), date(2026, 4, 30), date(2026, 5, 1)]
    assert cluster_earliest(days) == [date(2026, 1, 1), date(2026, 1, 5), date(2026, 4, 30)]


def test_earnings_dates_merges_sources(session: Session) -> None:
    nvda = _ticker(session, "NVDA")
    session.add(EarningsEvent(ticker_id=nvda.id, event_date=date(2026, 8, 27), source=SEC_EARNINGS_SOURCE))
    session.add(EarningsEvent(ticker_id=nvda.id, event_date=date(2026, 8, 26), source=FINNHUB_EARNINGS_SOURCE))
    session.commit()
    assert earnings_dates(session, nvda.id) == [date(2026, 8, 26)]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_earnings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.ingest.earnings'`.

- [ ] **Step 3: Implement**

`src/signalbench/ingest/earnings.py`:

```python
import uuid
from datetime import date, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from signalbench.db.models import DocumentTicker, EarningsEvent, RawDocument, Ticker, TickerKind
from signalbench.ingest.finnhub import FinnhubClient

NEW_YORK = ZoneInfo("America/New_York")
SEC_EARNINGS_SOURCE = "sec_2.02"
FINNHUB_EARNINGS_SOURCE = "finnhub"
CLUSTER_DAYS = 3
CALENDAR_DAYS_AHEAD = 30


def has_item_202(items: str | None) -> bool:
    return items is not None and "2.02" in [item.strip() for item in items.split(",")]


def sync_sec_earnings_events(session: Session) -> int:
    """One event per ticker on the New York date each Item 2.02 8-K was accepted."""
    existing = {
        (row.ticker_id, row.event_date)
        for row in session.exec(
            select(EarningsEvent).where(EarningsEvent.source == SEC_EARNINGS_SOURCE)
        ).all()
    }
    documents = session.exec(
        select(RawDocument).where(
            RawDocument.source == "sec_edgar",
            col(RawDocument.acceptance_at).is_not(None),
        )
    ).all()
    created = 0
    for document in documents:
        if document.acceptance_at is None or not has_item_202(document.items):
            continue
        event_date = document.acceptance_at.astimezone(NEW_YORK).date()
        ticker_ids = session.exec(
            select(DocumentTicker.ticker_id).where(DocumentTicker.document_id == document.id)
        ).all()
        for ticker_id in ticker_ids:
            if (ticker_id, event_date) in existing:
                continue
            session.add(
                EarningsEvent(ticker_id=ticker_id, event_date=event_date, source=SEC_EARNINGS_SOURCE)
            )
            existing.add((ticker_id, event_date))
            created += 1
    session.commit()
    return created


def ingest_finnhub_calendar(session: Session, finnhub: FinnhubClient, today: date) -> int:
    """Replace Finnhub's upcoming events for the next 30 days with the current calendar."""
    end = today + timedelta(days=CALENDAR_DAYS_AHEAD)
    payload = cast(
        dict[str, Any],
        finnhub.get("/calendar/earnings", {"from": today.isoformat(), "to": end.isoformat()}),
    )
    for stale in session.exec(
        select(EarningsEvent).where(
            EarningsEvent.source == FINNHUB_EARNINGS_SOURCE,
            col(EarningsEvent.event_date) >= today,
        )
    ).all():
        session.delete(stale)
    session.flush()

    ticker_ids = {
        ticker.symbol: ticker.id
        for ticker in session.exec(select(Ticker).where(Ticker.kind == TickerKind.us_stock)).all()
    }
    seen: set[tuple[uuid.UUID, date]] = set()
    created = 0
    for row in payload.get("earningsCalendar", []):
        ticker_id = ticker_ids.get(str(row.get("symbol", "")))
        if ticker_id is None or not row.get("date"):
            continue
        event_date = date.fromisoformat(str(row["date"]))
        if (ticker_id, event_date) in seen:
            continue
        seen.add((ticker_id, event_date))
        session.add(
            EarningsEvent(ticker_id=ticker_id, event_date=event_date, source=FINNHUB_EARNINGS_SOURCE)
        )
        created += 1
    session.commit()
    return created


def cluster_earliest(dates: list[date]) -> list[date]:
    """Collapse dates within CLUSTER_DAYS of a cluster's first date to that first date."""
    clusters: list[date] = []
    for day in sorted(set(dates)):
        if clusters and (day - clusters[-1]).days <= CLUSTER_DAYS:
            continue
        clusters.append(day)
    return clusters


def earnings_dates(session: Session, ticker_id: uuid.UUID) -> list[date]:
    dates = session.exec(
        select(col(EarningsEvent.event_date)).where(EarningsEvent.ticker_id == ticker_id)
    ).all()
    return cluster_earliest(list(dates))
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_earnings.py -v && uv run mypy src`
Expected: 4 passed; mypy `Success`.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/ingest/earnings.py tests/test_earnings.py
git commit -m "feat: earnings dates from SEC 2.02 filings and the Finnhub calendar

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Stats and the final CLI

**Files:**
- Create: `src/signalbench/ingest/stats.py`, `tests/test_stats.py`
- Rewrite: `src/signalbench/cli.py`, `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_stats.py`:

```python
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlmodel import Session

from signalbench.db.models import DocType, EarningsEvent, Price, RawDocument, Ticker, TickerKind
from signalbench.ingest.stats import collect_stats
from signalbench.ingest.text import DOCUMENT_SEPARATOR


def test_collect_stats(session: Session) -> None:
    us = Ticker(symbol="NVDA", company_name="Nvidia", kind=TickerKind.us_stock, active=True)
    idle = Ticker(symbol="THIN", company_name="Thin", kind=TickerKind.us_stock, active=False)
    cdr = Ticker(symbol="ZNVD", company_name="Nvidia", kind=TickerKind.cdr)
    session.add_all([us, idle, cdr])
    session.commit()
    for ticker in (us, cdr):
        session.add(
            Price(
                ticker_id=ticker.id,
                date=date(2026, 9, 21),
                open=Decimal(1),
                high=Decimal(1),
                low=Decimal(1),
                close=Decimal(1),
                adj_close=Decimal(1),
                volume=1,
            )
        )
    accepted = datetime(2024, 1, 15, 21, 30, tzinfo=UTC)
    session.add_all(
        [
            RawDocument(source="sec_edgar", external_id="a", doc_type=DocType.eight_k, raw_text="x", published_at=accepted, acceptance_at=accepted, text=f"p{DOCUMENT_SEPARATOR}e"),
            RawDocument(source="sec_edgar", external_id="b", doc_type=DocType.eight_k, raw_text="x", published_at=accepted, acceptance_at=accepted, text="p"),
            RawDocument(source="sec_edgar", external_id="c", doc_type=DocType.eight_k, raw_text="x", published_at=datetime(2015, 1, 1, tzinfo=UTC), acceptance_at=datetime(2015, 1, 1, tzinfo=UTC)),
            RawDocument(source="finnhub", external_id="n", doc_type=DocType.news, raw_text="h", published_at=accepted),
            EarningsEvent(ticker_id=us.id, event_date=date(2024, 1, 15), source="sec_2.02"),
        ]
    )
    session.commit()

    assert dict(collect_stats(session, since=date(2016, 1, 1))) == {
        "us_stocks": 2,
        "active_us_stocks": 1,
        "cdrs": 1,
        "cdrs_with_prices": 1,
        "price_rows": 2,
        "eight_ks_since": 2,
        "eight_ks_with_exhibit_text": 1,
        "news_rows": 1,
        "earnings_events": 1,
    }
```

Replace `tests/test_cli.py`:

```python
from typer.testing import CliRunner

from signalbench.cli import app

runner = CliRunner()


def test_top_level_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("universe", "seed", "ingest"):
        assert command in result.stdout
    assert "seed-watchlist" not in result.stdout


def test_ingest_help_lists_every_source() -> None:
    result = runner.invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0
    for command in ("prices", "filings", "earnings", "news", "all", "stats"):
        assert command in result.stdout


def test_universe_help_lists_refresh() -> None:
    result = runner.invoke(app, ["universe", "--help"])
    assert result.exit_code == 0
    assert "refresh" in result.stdout
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_stats.py tests/test_cli.py -v`
Expected: FAIL. `signalbench.ingest.stats` doesn't exist, and the CLI lacks `universe`.

- [ ] **Step 3: Implement stats**

`src/signalbench/ingest/stats.py`:

```python
from datetime import UTC, date, datetime

from sqlalchemy import func
from sqlmodel import Session, col, select

from signalbench.db.models import DocType, EarningsEvent, Price, RawDocument, Ticker, TickerKind
from signalbench.ingest.news import NEWS_SOURCE
from signalbench.ingest.text import DOCUMENT_SEPARATOR


def collect_stats(session: Session, since: date) -> list[tuple[str, int]]:
    since_at = datetime(since.year, since.month, since.day, tzinfo=UTC)
    cdr_ids = select(Ticker.id).where(Ticker.kind == TickerKind.cdr)
    eight_ks = select(func.count()).select_from(RawDocument).where(
        RawDocument.doc_type == DocType.eight_k,
        col(RawDocument.acceptance_at) >= since_at,
    )
    return [
        ("us_stocks", _count(session, select(func.count()).select_from(Ticker).where(Ticker.kind == TickerKind.us_stock))),
        ("active_us_stocks", _count(session, select(func.count()).select_from(Ticker).where(Ticker.kind == TickerKind.us_stock, Ticker.active))),
        ("cdrs", _count(session, select(func.count()).select_from(Ticker).where(Ticker.kind == TickerKind.cdr))),
        ("cdrs_with_prices", _count(session, select(func.count(func.distinct(Price.ticker_id))).where(col(Price.ticker_id).in_(cdr_ids)))),
        ("price_rows", _count(session, select(func.count()).select_from(Price))),
        ("eight_ks_since", _count(session, eight_ks)),
        ("eight_ks_with_exhibit_text", _count(session, eight_ks.where(col(RawDocument.text).contains(DOCUMENT_SEPARATOR)))),
        ("news_rows", _count(session, select(func.count()).select_from(RawDocument).where(RawDocument.source == NEWS_SOURCE))),
        ("earnings_events", _count(session, select(func.count()).select_from(EarningsEvent))),
    ]


def _count(session: Session, statement: object) -> int:
    return int(session.exec(statement).one())  # type: ignore[call-overload]
```

- [ ] **Step 4: Replace `src/signalbench/cli.py`**

```python
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer
from sqlmodel import Session, col, select

from signalbench.config import settings
from signalbench.db.models import Ticker, TickerKind
from signalbench.db.session import get_session
from signalbench.ingest.cdr import (
    build_entries,
    diff_universe,
    dump_universe,
    fetch_cboe_directory,
    gics_sector,
    load_universe,
    parse_cboe_directory,
    yfinance_sector,
)
from signalbench.ingest.earnings import ingest_finnhub_calendar, sync_sec_earnings_events
from signalbench.ingest.edgar import backfill_filing_text, ingest_eight_ks_for_symbol
from signalbench.ingest.finnhub import FinnhubClient
from signalbench.ingest.liquidity import update_liquidity_flags
from signalbench.ingest.news import ingest_company_news, last_news_date, news_windows
from signalbench.ingest.prices import fetch_yfinance_daily, ingest_daily_prices
from signalbench.ingest.seed import BENCHMARKS, seed_universe
from signalbench.ingest.stats import collect_stats

UNIVERSE_PATH = Path(__file__).resolve().parents[2] / "data" / "cdr_universe.yaml"

app = typer.Typer(help="SignalBench swing assistant.")
ingest_app = typer.Typer(help="Load research data into Postgres.")
universe_app = typer.Typer(help="Manage the checked-in CDR universe.")
app.add_typer(ingest_app, name="ingest")
app.add_typer(universe_app, name="universe")


@universe_app.command("refresh")
def universe_refresh(
    write: Annotated[bool, typer.Option("--write", help="Write data/cdr_universe.yaml.")] = False,
) -> None:
    headers = {"User-Agent": "Mozilla/5.0 (SignalBench universe refresh)"}
    with httpx.Client(timeout=30.0, headers=headers) as client:
        listings = parse_cboe_directory(fetch_cboe_directory(client))
    current = load_universe(UNIVERSE_PATH) if UNIVERSE_PATH.exists() else []
    diff = diff_universe(current, listings)
    typer.echo(f"Cboe lists {len(listings)} US-company CDRs")
    for listing in diff.added:
        typer.echo(f"+ {listing.us_symbol} ({listing.cdr_symbol}) {listing.company_name}")
    for entry in diff.removed:
        typer.echo(f"- {entry.us_symbol} ({entry.cdr_symbol}) {entry.company_name}")
    if write:
        entries = build_entries(
            listings,
            sector_lookup=lambda symbol: gics_sector(yfinance_sector(symbol)),
            existing=current,
        )
        dump_universe(entries, UNIVERSE_PATH)
        typer.echo(f"Wrote {len(entries)} entries to {UNIVERSE_PATH}")
    elif diff.added or diff.removed:
        typer.echo("Run again with --write to update the file.")


@app.command()
def seed() -> None:
    entries = load_universe(UNIVERSE_PATH)
    with get_session() as session:
        result = seed_universe(session, entries)
    typer.echo(
        f"Seeded {result.us_stocks} US stocks, {result.cdrs} CDRs, "
        f"{result.benchmarks} benchmarks; deactivated {len(result.deactivated)}"
    )


@ingest_app.command()
def prices() -> None:
    with get_session() as session:
        _ingest_prices(session)


@ingest_app.command()
def filings(
    backfill_text: Annotated[
        bool,
        typer.Option("--backfill-text", help="Fill text/acceptance/items for older rows first."),
    ] = False,
) -> None:
    with get_session() as session, httpx.Client(timeout=30.0) as client:
        _ingest_filings(session, client, backfill_text=backfill_text)


@ingest_app.command()
def earnings() -> None:
    with get_session() as session:
        _ingest_earnings(session)


@ingest_app.command()
def news() -> None:
    if settings.finnhub_api_key is None:
        typer.echo("FINNHUB_API_KEY is not set in .env", err=True)
        raise typer.Exit(1)
    with get_session() as session, httpx.Client(timeout=30.0) as client:
        _ingest_news(session, FinnhubClient(settings.finnhub_api_key, client))


@ingest_app.command("all")
def ingest_all() -> None:
    with get_session() as session, httpx.Client(timeout=30.0) as client:
        _ingest_prices(session)
        _ingest_filings(session, client, backfill_text=False)
        _ingest_earnings(session)
        if settings.finnhub_api_key is None:
            typer.echo("FINNHUB_API_KEY is not set; skipping news", err=True)
        else:
            _ingest_news(session, FinnhubClient(settings.finnhub_api_key, client))


@ingest_app.command()
def stats() -> None:
    with get_session() as session:
        for label, value in collect_stats(session, since=settings.filings_backfill_start):
            typer.echo(f"{label}: {value}")


def _ingest_prices(session: Session) -> None:
    tickers = _universe_tickers(session)
    for index, ticker in enumerate(tickers, start=1):
        result = ingest_daily_prices(
            session,
            ticker,
            fetch=fetch_yfinance_daily,
            history_start=settings.price_history_start,
        )
        typer.echo(
            f"prices {index}/{len(tickers)} {ticker.symbol} "
            f"+{result.created} ~{result.updated} x{result.rejected}"
        )
    liquidity = update_liquidity_flags(session, load_universe(UNIVERSE_PATH))
    typer.echo(f"liquidity: {len(liquidity.active)} active, {len(liquidity.inactive)} inactive")
    for symbol, reason in sorted(liquidity.inactive.items()):
        typer.echo(f"  inactive {symbol}: {reason}")


def _ingest_filings(session: Session, client: httpx.Client, backfill_text: bool) -> None:
    tickers = _universe_tickers(session, {TickerKind.us_stock})
    for index, ticker in enumerate(tickers, start=1):
        if backfill_text:
            filled = backfill_filing_text(session, ticker.symbol, client, settings.sec_user_agent)
            typer.echo(f"filings {index}/{len(tickers)} {ticker.symbol} text backfilled {filled}")
        created = ingest_eight_ks_for_symbol(
            session,
            ticker.symbol,
            client,
            settings.sec_user_agent,
            since=settings.filings_backfill_start,
        )
        typer.echo(f"filings {index}/{len(tickers)} {ticker.symbol} +{created}")


def _ingest_earnings(session: Session) -> None:
    typer.echo(f"earnings from SEC 2.02: +{sync_sec_earnings_events(session)}")
    if settings.finnhub_api_key is None:
        typer.echo("FINNHUB_API_KEY is not set; skipping the upcoming earnings calendar", err=True)
        return
    with httpx.Client(timeout=30.0) as client:
        finnhub = FinnhubClient(settings.finnhub_api_key, client)
        created = ingest_finnhub_calendar(session, finnhub, datetime.now(UTC).date())
    typer.echo(f"earnings from Finnhub calendar: {created} upcoming")


def _ingest_news(session: Session, finnhub: FinnhubClient) -> None:
    today = datetime.now(UTC).date()
    tickers = _universe_tickers(session, {TickerKind.us_stock})
    for index, ticker in enumerate(tickers, start=1):
        created = 0
        for start, end in news_windows(last_news_date(session, ticker), today):
            created += ingest_company_news(session, finnhub, ticker, start, end)
        typer.echo(f"news {index}/{len(tickers)} {ticker.symbol} +{created}")


def _universe_tickers(session: Session, kinds: set[TickerKind] | None = None) -> list[Ticker]:
    """Universe members plus benchmarks, active or not (liquidity needs their prices)."""
    entries = load_universe(UNIVERSE_PATH)
    symbols = (
        {entry.us_symbol for entry in entries}
        | {entry.cdr_symbol for entry in entries}
        | {symbol for symbol, _name in BENCHMARKS}
    )
    rows = session.exec(select(Ticker).where(col(Ticker.symbol).in_(symbols))).all()
    return sorted(
        (ticker for ticker in rows if kinds is None or ticker.kind in kinds),
        key=lambda ticker: ticker.symbol,
    )
```

- [ ] **Step 5: Run the full suite and checks**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: all pass. If mypy flags the `type: ignore` in `stats.py` as unused (`warn_unused_ignores`), delete that comment.

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/ingest/stats.py src/signalbench/cli.py tests/test_stats.py tests/test_cli.py
git commit -m "feat: universe, seed, ingest all, and stats commands

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 19: Generate and commit the universe file

**Files:**
- Create (generated): `data/cdr_universe.yaml`
- Create: `tests/test_universe_file.py`

This task uses the network (Cboe, yfinance).

- [ ] **Step 1: Generate the file**

Run: `uv run signalbench universe refresh --write`
Expected: `Cboe lists N US-company CDRs` with N ≥ 30 (about 40 on 2026-09-22), then one `+` line per name and `Wrote N entries`. If a sector lookup raises `Unmapped yfinance sector`, add the missing name to `YF_SECTOR_TO_GICS` in `src/signalbench/ingest/cdr.py` with its GICS equivalent, and rerun.

- [ ] **Step 2: Review the file**

Open `data/cdr_universe.yaml`. Check that every `price_symbol` ends in `.NE`, every `sector` is a GICS name, and the company names look right. Spot-check 3 entries on Cboe's site.

- [ ] **Step 3: Write the test that locks the file's shape**

`tests/test_universe_file.py`:

```python
from pathlib import Path

from signalbench.ingest.cdr import YF_SECTOR_TO_GICS, load_universe

UNIVERSE = Path(__file__).resolve().parents[1] / "data" / "cdr_universe.yaml"


def test_checked_in_universe_is_well_formed() -> None:
    entries = load_universe(UNIVERSE)
    assert len(entries) >= 30
    assert all(entry.price_symbol == f"{entry.cdr_symbol}.NE" for entry in entries)
    assert {entry.sector for entry in entries} <= set(YF_SECTOR_TO_GICS.values())
    assert len({entry.cdr_symbol for entry in entries}) == len(entries)
```

Run: `uv run pytest tests/test_universe_file.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add data/cdr_universe.yaml tests/test_universe_file.py
git commit -m "data: check in the US-company CDR universe from Cboe Canada

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 20: Docs — README, .env.example, docs index

**Files:**
- Modify: `README.md`, `.env.example`, `docs/superpowers/README.md`

- [ ] **Step 1: Ask about the uncommitted README edit**

`README.md` had uncommitted owner edits when this plan was written. Run `git diff README.md`. If the diff shows changes that aren't from this plan, **ask the owner** whether to include them in this commit before touching the file. Build on the working-tree version either way. Never discard it.

- [ ] **Step 2: Update `.env.example`**

Replace the Together block (the comment lines about live extract and eval, `TOGETHER_API_KEY=`, and the `MODEL_VERSION`/`PROMPT_VERSION` override comments) with:

```dotenv
# Required for `signalbench ingest news` and the upcoming-earnings calendar.
# Free key: https://finnhub.io/register  (free tier: ~1 year of company news, 60 calls/min)
FINNHUB_API_KEY=

# Optional overrides (defaults are in src/signalbench/config.py).
# PRICE_HISTORY_START=2010-01-01
# FILINGS_BACKFILL_START=2016-01-01
```

- [ ] **Step 3: Update `README.md`**

Make these edits on top of the working copy:
- **Intro:** keep "not a trading bot" and "does not connect to a broker". Replace the LLM-extraction framing with: a personal swing-trading research assistant for US-company CDRs on Cboe Canada, where rules come from code, Jev reads filings and news (spec 03), and the owner places every trade by hand.
- **Current capabilities:** replace the list with what exists after spec 01: the CDR universe from Cboe; US, CDR (`.NE`), and QQQ/SPY daily prices from 2010 with incremental upsert; liquidity flags; 8-Ks since 2016 with acceptance time, items, EX-99 exhibits, and cleaned text; earnings dates from SEC Item 2.02 plus the Finnhub calendar; Finnhub company news.
- **Architecture diagram:**

```text
Cboe CDR directory ──> data/cdr_universe.yaml ──> tickers (US stock, CDR, benchmark)
yfinance ─────────────> prices ──> liquidity flags
SEC EDGAR ────────────> 8-Ks (+ EX-99, clean text) ──> earnings events (Item 2.02)
Finnhub ──────────────> news, upcoming earnings
```

- **Tech stack:** replace "Together AI, Pydantic structured schemas" with "BeautifulSoup + lxml (filing text)", and add "Finnhub" to market data.
- **Quick start:** replace the Together key with `FINNHUB_API_KEY`. Replace steps 3–6 with: `uv run alembic upgrade head`, `uv run signalbench seed`, `uv run signalbench ingest all`, `uv run signalbench ingest stats`.
- **CLI reference:** replace the table with the commands in `signalbench --help` and `signalbench ingest --help`.
- **Testing:** keep. Remove the sentence about running `signalbench eval`.
- **Project layout:** remove `eval/` and `extraction/`, add `market/` and the new `ingest/` modules, drop `evals/` and `prompts/`.
- **Roadmap:** replace with "Specs 02–05 in `docs/superpowers/specs/`: strategy and backtest, Jev reader, ledger, Telegram bot."

- [ ] **Step 4: Update `docs/superpowers/README.md`**

In the Swing Assistant table's row 01, append ` · plan: [2026-09-22-swing-01-data-foundation.md](plans/2026-09-22-swing-01-data-foundation.md)` to the Spec cell.

- [ ] **Step 5: Check for stale references**

Run: `git grep -n -i -E "together|signalbench eval|extract --llm|seed-watchlist|watchlist.yaml" -- README.md .env.example`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add README.md .env.example docs/superpowers/README.md
git commit -m "docs: README and env example for the swing data foundation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 21: Gate — real ingest run

This task uses the network and local Postgres. It needs `SEC_USER_AGENT` and `FINNHUB_API_KEY` in `.env`. **Ask the owner to add the Finnhub key themselves. Never ask them to paste it into chat.**

- [ ] **Step 1: Static checks**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: all green.

- [ ] **Step 2: Database**

Run: `docker compose up -d && uv run alembic upgrade head`
Expected: upgrades through `0008_swing_data_foundation`.

- [ ] **Step 3: Seed and backfill text for legacy rows**

Run: `uv run signalbench seed && uv run signalbench ingest prices && uv run signalbench ingest filings --backfill-text`
Expected:
- seed prints about 40 US stocks and 40 CDRs, and deactivates the old watchlist names that aren't in the universe
- prices prints one line per ticker, then `liquidity: N active`
- filings takes a long time: about 40 names × about 10 years of 8-Ks with exhibits, at 8 requests/second, so roughly 30–60 minutes. Run it in the background and let it finish.

- [ ] **Step 4: Earnings and news**

Run: `uv run signalbench ingest earnings && uv run signalbench ingest news`
Expected: SEC 2.02 events created, an upcoming Finnhub count, and one news line per ticker. The news backfill is about 40 × 13 requests at 50/minute, roughly 10 minutes.

- [ ] **Step 5: Idempotency check**

Run: `uv run signalbench ingest all`
Expected: filings `+0` or small numbers for every ticker, prices mostly `+0/+1` with a few `~` updates, news small. There are no errors.

- [ ] **Step 6: Record the counts**

Run: `uv run signalbench ingest stats`
Expected thresholds: `cdrs_with_prices` ≥ 30, `eight_ks_since` > 0, `eight_ks_with_exhibit_text` > 0, `news_rows` > 0, `earnings_events` > 0.

Append the full stats output and the date to the Changelog of `docs/superpowers/specs/2026-09-22-swing-assistant-01-data-foundation-design.md`, as `- <date>: gate run — <label>: <value>, …`.

- [ ] **Step 7: Commit, then ask before pushing**

```bash
git add docs/superpowers/specs/2026-09-22-swing-assistant-01-data-foundation-design.md
git commit -m "docs: record spec 01 gate counts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Ask the owner before pushing the branch or opening a PR.

---

## Spec coverage check

| Spec 01 requirement | Task |
| --- | --- |
| A1 close v1.1 | 1 |
| A2 remove Together pipeline, settings, dependency, tests | 2, 5 |
| A3 remove sentiment backtest; keep metrics and fingerprint | 2 |
| A4 migration 0007 with `checkfirst` enum drop | 3 |
| A5 CI workflow | 4 |
| A6 README merged with the owner's working copy | 20 |
| B Cboe JSON source, US-ticker parsing, `BRK/B` normalisation | 8 |
| B checked-in YAML, `refresh` diff, `--write` | 8, 18, 19 |
| B GICS mapping that fails loudly | 8 |
| B ticker `kind`/`us_ticker_id`/`price_symbol` (0008); seeding replaces watchlist | 6, 9 |
| B liquidity: US ≥ $50M median; CDR priced in last 5 sessions | 11 |
| C prices from 2010, CDR `.NE`, incremental −10 days (≥5 sessions), upsert | 10 |
| C `adjusted_bars()` | 10 |
| C reject NaN, non-positive, and `high < low` rows | 10 |
| D `acceptance_at`, `items`, `text` (0008) | 6, 13 |
| D paging through `filings.files`, backfill since 2016, 8-K/A | 13 |
| D EX-99 exhibits via `-index.htm`, cleaning, 96,000-char cap | 12, 13 |
| D `--backfill-text` for existing rows | 13, 14, 18 |
| D SEC limiter at 8/s plus 429 retry | 7, 13 |
| E SEC 2.02 events, Finnhub calendar, earliest date in a 3-day cluster | 17 |
| F Finnhub news, 1-year backfill, −2-day overlap, dedupe, queried-symbol link, 50/min | 15, 16 |
| Configuration keys | 5, 20 |
| CLI | 18 |
| Gate counts | 21 |
