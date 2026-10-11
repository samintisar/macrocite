# Spec 11: Crypto Paper Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the pre-registered `crypto-v1` Breakout rules forward on Kraken BTC/ETH daily candles, night by night on botbox, with an append-only record, PAPER Telegram messages, and a fixed 12-month review.

**Architecture:** A new `signalbench.paper` package. Each night `run_paper` fetches Kraken candles, replays the strategy from the paper start day with the backtest's own `simulator.step()`, checks that every saved day comes out exactly as saved, appends only the new days, and messages the owner. Four new tables hold portfolios, days, events, and runs. CLI commands live under `signalbench crypto paper`. systemd units for botbox are checked in under `scripts/botbox/`.

**Tech Stack:** Python 3.11, uv, SQLModel/SQLAlchemy, Alembic (Postgres 16; tests on in-memory SQLite), Typer, httpx, python-telegram-bot (send only), pytest, ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-10-11-swing-assistant-11-crypto-paper-design.md`

**Conventions (whole plan):**
- Run everything with `uv run` from the repo root. Full check before every commit: `uv run pytest -q`, `uv run ruff check src tests`, `uv run mypy src`.
- Commit messages end with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Do not change the live code paths (`src/signalbench/live/`, `backtest/runner.py`, `backtest/simulator.py`).
- Comments: short, explain why, match the surrounding density.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/signalbench/db/models.py` (modify) | `PaperRun`, `PaperPortfolio`, `PaperDay`, `PaperEvent` |
| `alembic/versions/0018_paper_crypto.py` (create) | The four tables |
| `src/signalbench/backtest/crypto_runner.py` (modify) | Expose `check_crypto_config`, `check_crypto_series`, `crypto_market` for reuse |
| `src/signalbench/paper/__init__.py` (create) | Empty |
| `src/signalbench/paper/paper_file.py` (create) | `PaperError`, `PaperFile`, `ReviewRules`, `add_months`, `load_paper_file`, `parse_paper_file` |
| `src/signalbench/paper/replay.py` (create) | `ReplayDay`, `order_events`, `replay` |
| `src/signalbench/paper/store.py` (create) | `SavedDay`, `as_json`, `row_values`, `load_saved`, `first_difference`, `append_days`, `find_portfolio` |
| `src/signalbench/paper/messages.py` (create) | The PAPER texts for orders, fills, closed trades, alerts |
| `src/signalbench/paper/summary.py` (create) | `PaperSummary`, `paper_summary`, `summary_text`, `Review`, `paper_review`, `close_deadline`, `worst_drawdown` |
| `src/signalbench/paper/run.py` (create) | `PAPER_LOCK`, `PaperRules`, `check_rules`, `start_paper`, `run_paper`, `PaperOutcome`, `ReplayMismatch` |
| `src/signalbench/cli.py` (modify) | `crypto paper start|run|status|review` |
| `scripts/botbox/*` (create) | systemd units, backup script, env example, runbook |
| `tests/paper_helpers.py` (create) | Shared fake candles, paper file writer, fake fetch |
| `tests/test_paper_*.py` (create) | One file per module |

---

### Task 1: Paper tables (models and migration 0018)

**Files:**
- Modify: `src/signalbench/db/models.py` (append at the end)
- Create: `alembic/versions/0018_paper_crypto.py`
- Modify: `tests/test_migrations.py`
- Create: `tests/test_paper_schema.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_migrations.py`, change `test_migrations_have_single_head` to expect `["0018_paper_crypto"]`, and add after it:

```python
def test_0018_creates_the_four_paper_tables() -> None:
    text = (VERSIONS / "0018_paper_crypto.py").read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0017_crypto_ticker_kind"' in text
    for table in ("paper_runs", "paper_portfolios", "paper_days", "paper_events"):
        assert f'op.create_table(\n        "{table}"' in text
    assert 'sa.PrimaryKeyConstraint("portfolio_id", "day")' in text
    assert 'sa.Column("state", sa.JSON(), nullable=False)' in text
    assert 'sa.UniqueConstraint("name", name="uq_paper_portfolios_name")' in text
```

Create `tests/test_paper_schema.py`:

```python
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from signalbench.db.models import PaperDay, PaperEvent, PaperPortfolio, PaperRun

T = datetime(2026, 10, 14, 0, 30, tzinfo=UTC)
DAY = date(2026, 10, 13)


def _run(session: Session) -> PaperRun:
    run = PaperRun(started_at=T, status="running", git_sha="abc123")
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _portfolio(session: Session, name: str = "crypto-v1-paper") -> PaperPortfolio:
    portfolio = PaperPortfolio(
        name=name, paper_file="paper_crypto-v1.yaml", strategy_sha256="f" * 64, start=DAY
    )
    session.add(portfolio)
    session.commit()
    session.refresh(portfolio)
    return portfolio


def _day(portfolio: PaperPortfolio, run: PaperRun, state: dict[str, object]) -> PaperDay:
    assert portfolio.id is not None and run.id is not None
    return PaperDay(
        portfolio_id=portfolio.id, day=DAY, equity=100.0, cash=100.0, state=state,
        recorded_at=T, run_id=run.id,
    )


def test_a_run_starts_with_zero_counts(session: Session) -> None:
    run = _run(session)
    assert (run.days_added, run.messages_failed, run.summary_sent) == (0, 0, False)
    assert run.finished_at is None and run.error is None


def test_a_paper_day_reads_its_state_back_exactly(session: Session) -> None:
    state: dict[str, object] = {"cash": 0.1 + 0.2, "positions": [{"units": 0.000527}]}
    session.add(_day(_portfolio(session), _run(session), state))
    session.commit()
    session.expire_all()
    saved = session.get(PaperDay, (1, DAY))
    assert saved is not None
    assert saved.state == state
    assert saved.recorded_at == T


def test_a_day_is_saved_once_per_portfolio(session: Session) -> None:
    portfolio, run = _portfolio(session), _run(session)
    session.add(_day(portfolio, run, {}))
    session.commit()
    session.expunge_all()
    session.add(_day(portfolio, run, {}))
    with pytest.raises(IntegrityError):
        session.commit()


def test_portfolio_names_are_unique(session: Session) -> None:
    _portfolio(session)
    session.expunge_all()
    with pytest.raises(IntegrityError):
        _portfolio(session)


def test_an_event_keeps_its_payload(session: Session) -> None:
    portfolio, run = _portfolio(session), _run(session)
    assert portfolio.id is not None and run.id is not None
    payload = {"date": DAY.isoformat(), "event": "entry", "price": 62661.648, "trimmed": False}
    session.add(
        PaperEvent(
            portfolio_id=portfolio.id, day=DAY, kind="entry", payload=payload,
            recorded_at=T, run_id=run.id,
        )
    )
    session.commit()
    session.expire_all()
    event = session.get(PaperEvent, 1)
    assert event is not None and event.payload == payload
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_migrations.py tests/test_paper_schema.py -q`
Expected: FAIL (`ImportError: cannot import name 'PaperDay'`, and the head is still 0017).

- [ ] **Step 3: Add the models**

Append to `src/signalbench/db/models.py` (all names used are already imported there: `JSON`, `Column`, `Index`, `PrimaryKeyConstraint`, `Text`, `UniqueConstraint`, `UTCDateTime`, `_created_at`, `Any`, `date`, `datetime`, `Field`, `SQLModel`):

```python
class PaperRun(SQLModel, table=True):
    """One `signalbench crypto paper run` that got the lock (spec 11)."""

    __tablename__ = "paper_runs"

    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    finished_at: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime(), nullable=True)
    )
    status: str  # running | ok | failed | mismatch
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    git_sha: str
    days_added: int = 0
    messages_failed: int = 0  # Telegram sends that failed; the days are saved anyway
    summary_sent: bool = False  # this run sent the week's summary


class PaperPortfolio(SQLModel, table=True):
    """The portfolio of one pre-registered paper file (spec 11), made by `crypto paper start`."""

    __tablename__ = "paper_portfolios"
    __table_args__ = (UniqueConstraint("name", name="uq_paper_portfolios_name"),)

    id: int | None = Field(default=None, primary_key=True)
    name: str
    paper_file: str  # the file's name, e.g. paper_crypto-v1.yaml
    strategy_sha256: str
    start: date
    created_at: datetime = _created_at()


class PaperDay(SQLModel, table=True):
    """One replayed day of a paper portfolio (spec 11). Written once, never updated."""

    __tablename__ = "paper_days"
    __table_args__ = (PrimaryKeyConstraint("portfolio_id", "day"),)

    portfolio_id: int = Field(foreign_key="paper_portfolios.id", ondelete="RESTRICT")
    day: date
    equity: float
    cash: float
    state: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))  # SimState after the close
    recorded_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    run_id: int = Field(foreign_key="paper_runs.id", ondelete="RESTRICT")


class PaperEvent(SQLModel, table=True):
    """One event of a paper day (spec 11): a simulator event, or an order decided at its close
    (`order_entry`, `order_exit`). Append-only; read back in id order."""

    __tablename__ = "paper_events"
    __table_args__ = (Index("ix_paper_events_portfolio_day", "portfolio_id", "day"),)

    id: int | None = Field(default=None, primary_key=True)
    portfolio_id: int = Field(foreign_key="paper_portfolios.id", ondelete="RESTRICT")
    day: date
    kind: str
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    recorded_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    run_id: int = Field(foreign_key="paper_runs.id", ondelete="RESTRICT")
```

- [ ] **Step 4: Add the migration**

Create `alembic/versions/0018_paper_crypto.py`:

```python
"""Spec 11: paper trading of crypto-v1, an append-only record of every replayed day

Revision ID: 0018_paper_crypto
Revises: 0017_crypto_ticker_kind
Create Date: 2026-10-11 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_paper_crypto"
down_revision: str | None = "0017_crypto_ticker_kind"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("git_sha", sa.String(), nullable=False),
        sa.Column("days_added", sa.Integer(), nullable=False),
        sa.Column("messages_failed", sa.Integer(), nullable=False),
        sa.Column("summary_sent", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "paper_portfolios",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("paper_file", sa.String(), nullable=False),
        sa.Column("strategy_sha256", sa.String(), nullable=False),
        sa.Column("start", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_paper_portfolios_name"),
    )
    op.create_table(
        "paper_days",
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("cash", sa.Float(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["paper_portfolios.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["paper_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("portfolio_id", "day"),
    )
    op.create_table(
        "paper_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["paper_portfolios.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["paper_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_paper_events_portfolio_day", "paper_events", ["portfolio_id", "day"]
    )


def downgrade() -> None:
    op.drop_index("ix_paper_events_portfolio_day", table_name="paper_events")
    op.drop_table("paper_events")
    op.drop_table("paper_days")
    op.drop_table("paper_portfolios")
    op.drop_table("paper_runs")
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/test_migrations.py tests/test_paper_schema.py -q`
Expected: PASS.

- [ ] **Step 6: Check the migration on Postgres (local research DB)**

The research DB is the docker compose project `signalbench-research` on `localhost:5433` (start it with `docker compose -p signalbench-research -f docker-compose.yml -f docker-compose.port.yml up -d` if it is not running). In PowerShell:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://signalbench:signalbench@localhost:5433/signalbench"
uv run alembic upgrade head
uv run alembic downgrade 0017_crypto_ticker_kind
uv run alembic upgrade head
```

Expected: each command ends without an error. If Docker is not available, note it in the task report and skip this step.

- [ ] **Step 7: Full check and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
git add src/signalbench/db/models.py alembic/versions/0018_paper_crypto.py tests/test_migrations.py tests/test_paper_schema.py
git commit -m "feat: paper trading tables, migration 0018 (spec 11)"
```

---

### Task 2: Reusable crypto checks and market in the crypto runner

The paper job must judge data and build its market exactly as the spec 09 backtest did. Move those pieces into public functions; `run_crypto_backtest` keeps its behaviour.

**Files:**
- Modify: `src/signalbench/backtest/crypto_runner.py`
- Modify: `tests/test_crypto_runner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_crypto_runner.py` (and add `check_crypto_config` and `crypto_market` to its import from `signalbench.backtest.crypto_runner`):

```python
def test_check_crypto_config_refuses_another_regime() -> None:
    with pytest.raises(RunRefusedError, match="gates on XBTUSD"):
        check_crypto_config(replace(_config(), regime_symbol="QQQ"))


def test_check_crypto_config_refuses_another_cost() -> None:
    with pytest.raises(RunRefusedError):
        check_crypto_config(replace(_config(), cost_per_side=0.002))


def test_crypto_market_holds_the_run_days() -> None:
    bars = {"ETHUSD": _eth(), "XBTUSD": _btc()}
    market = crypto_market(bars, _config(), DailySessions(), DAYS[250], DAYS[260])
    assert market.sessions_between(DAYS[250], DAYS[260]) == DAYS[250:261]


def test_crypto_market_refuses_an_empty_run() -> None:
    bars = {"ETHUSD": _eth(), "XBTUSD": _btc()}
    with pytest.raises(RunRefusedError, match="No sessions"):
        crypto_market(bars, _config(), DailySessions(), DAYS[260], DAYS[250])
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_crypto_runner.py -q`
Expected: FAIL with `ImportError: cannot import name 'check_crypto_config'`.

- [ ] **Step 3: Refactor the runner**

In `src/signalbench/backtest/crypto_runner.py`:

1. Rename `_check_series` to `check_crypto_series` (keep its body and docstring). Run `grep -rn "_check_series" src tests` and update every caller.
2. Add these two functions just above `run_crypto_backtest`:

```python
def check_crypto_config(config: StrategyConfig) -> None:
    """Kraken's top fee, BTC as the regime, and idle cash kept as cash (spec 09)."""
    check_cost_matches_kraken(config.cost_per_side)
    if config.regime_symbol != BTC or config.cash_vehicle is not None:
        raise RunRefusedError(
            f"A crypto config gates on {BTC} and keeps idle cash as cash (spec 09); this one "
            f"has regime {config.regime_symbol!r} and cash_vehicle {config.cash_vehicle!r}."
        )


def crypto_market(
    bars: dict[str, list[AdjustedBar]],
    config: StrategyConfig,
    calendar: Sessions,
    start: date,
    last: date,
) -> MarketView:
    """The market the crypto backtest and the paper replay both run on: every day from `start`
    to `last`, plus the earnings lookahead (crypto has no earnings, so it never fires)."""
    run_sessions = calendar.sessions_between(start, last)
    if not run_sessions:
        raise RunRefusedError(f"No sessions between {start} and {last}.")
    lookahead = max(config.earnings_blackout_sessions, config.earnings_exit_sessions)
    return MarketView(
        [
            SymbolInput(symbol=symbol, sector=CRYPTO_SECTOR, bars=series, earnings=[])
            for symbol, series in bars.items()
        ],
        bars[BTC],
        run_sessions + calendar.next_sessions(last, lookahead),
        config,
    )
```

3. In `run_crypto_backtest`, replace the cost check and the regime/cash-vehicle `if` block with `check_crypto_config(config)`, replace the `_check_series` call with `check_crypto_series`, and replace everything from `run_sessions = calendar.sessions_between(start, last)` through the end of the `MarketView(...)` call with:

```python
    market = crypto_market(bars, config, calendar, start, last)
```

The rest of the function (`simulate(...)` onward) stays as it is.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_crypto_runner.py tests/test_crypto_cli.py -q`
Expected: PASS, including every test that existed before.

- [ ] **Step 5: Full check and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
git add src/signalbench/backtest/crypto_runner.py tests/test_crypto_runner.py
git commit -m "refactor: crypto config check, series check, and market as public functions (spec 11)"
```

---

### Task 3: The paper file

**Files:**
- Create: `src/signalbench/paper/__init__.py` (empty)
- Create: `src/signalbench/paper/paper_file.py`
- Create: `tests/test_paper_file.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_file.py`:

```python
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from signalbench.paper.paper_file import (
    PaperError,
    PaperFile,
    ReviewRules,
    add_months,
    load_paper_file,
    parse_paper_file,
)

SHA = "a" * 64
VALID = f"""name: crypto-v1-paper
strategy: data/strategy_crypto-v1.yaml
strategy_sha256: {SHA}
setup: breakout
start: 2026-10-13
review:
  months: 12
  max_late_days: 7
  max_drawdown: 0.35
"""


def _raw(**changes: Any) -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(VALID)
    raw.update(changes)
    return raw


def test_loads_a_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "paper.yaml"
    path.write_text(VALID, encoding="utf-8")
    paper = load_paper_file(path)
    assert paper == PaperFile(
        name="crypto-v1-paper",
        strategy="data/strategy_crypto-v1.yaml",
        strategy_sha256=SHA,
        setup="breakout",
        start=date(2026, 10, 13),
        review=ReviewRules(months=12, max_late_days=7, max_drawdown=0.35),
    )
    assert paper.review_date == date(2027, 10, 13)


def test_add_months_keeps_the_day_or_clamps_to_the_month_end() -> None:
    assert add_months(date(2026, 10, 13), 12) == date(2027, 10, 13)
    assert add_months(date(2027, 1, 31), 1) == date(2027, 2, 28)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"extra": 1}, "exactly the keys"),
        ({"name": ""}, "name must be"),
        ({"strategy": "data/strategy.txt"}, "strategy must be"),
        ({"strategy_sha256": "abc"}, "64 lowercase hex"),
        ({"setup": "scalp"}, "unknown setup"),
        ({"start": "2026-10-13"}, "start must be a date"),
        ({"review": {"months": 12, "max_late_days": 7}}, "review needs exactly"),
        ({"review": {"months": 0, "max_late_days": 7, "max_drawdown": 0.35}}, "review.months"),
        (
            {"review": {"months": 12, "max_late_days": -1, "max_drawdown": 0.35}},
            "review.max_late_days",
        ),
        (
            {"review": {"months": 12, "max_late_days": 7, "max_drawdown": 1.5}},
            "review.max_drawdown",
        ),
    ],
)
def test_refuses_a_malformed_file(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(PaperError, match=message):
        parse_paper_file(_raw(**changes), "paper.yaml")


def test_a_missing_file_is_a_paper_error(tmp_path: Path) -> None:
    with pytest.raises(PaperError, match="missing.yaml"):
        load_paper_file(tmp_path / "missing.yaml")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_paper_file.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.paper'`.

- [ ] **Step 3: Write the module**

Create `src/signalbench/paper/__init__.py` as an empty file, then `src/signalbench/paper/paper_file.py`:

```python
"""The pre-registered paper file (spec 11): which strategy runs forward, from which day, and
how it is reviewed. Committed on its own before `crypto paper start`; never edited. A new
idea is a new paper file with a new name."""

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TypeGuard, cast, get_args

import yaml

from signalbench.strategy.config import SetupName

KEYS = frozenset({"name", "strategy", "strategy_sha256", "setup", "start", "review"})
REVIEW_KEYS = frozenset({"months", "max_late_days", "max_drawdown"})
HEX64 = re.compile(r"[0-9a-f]{64}")


class PaperError(ValueError):
    """A paper command or run refused; the message says why."""


@dataclass(frozen=True)
class ReviewRules:
    months: int  # the review date is start + months
    max_late_days: int  # days recorded more than 24 hours after their candle closed
    max_drawdown: float  # the worst peak-to-trough drop must stay under this


@dataclass(frozen=True)
class PaperFile:
    name: str
    strategy: str  # repo-relative path of the strategy config
    strategy_sha256: str
    setup: SetupName
    start: date
    review: ReviewRules

    @property
    def review_date(self) -> date:
        return add_months(self.start, self.review.months)


def add_months(day: date, months: int) -> date:
    """`day` moved by whole months, clamped to the month's last day (31 Jan + 1 = 28 Feb)."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def load_paper_file(path: Path) -> PaperFile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise PaperError(f"{path.name}: {error}") from None
    return parse_paper_file(raw, path.name)


def parse_paper_file(raw: object, where: str) -> PaperFile:
    if not isinstance(raw, dict) or set(raw) != KEYS:
        raise PaperError(f"{where}: needs exactly the keys {', '.join(sorted(KEYS))}")
    name, strategy, sha = raw["name"], raw["strategy"], raw["strategy_sha256"]
    setup, start, review = raw["setup"], raw["start"], raw["review"]
    if not isinstance(name, str) or not name:
        raise PaperError(f"{where}: name must be a non-empty string")
    if not isinstance(strategy, str) or not strategy.endswith(".yaml"):
        raise PaperError(f"{where}: strategy must be a repo-relative .yaml path")
    if not isinstance(sha, str) or not HEX64.fullmatch(sha):
        raise PaperError(f"{where}: strategy_sha256 must be 64 lowercase hex characters")
    if setup not in get_args(SetupName):
        raise PaperError(f"{where}: unknown setup {setup!r}")
    if not isinstance(start, date) or isinstance(start, datetime):
        raise PaperError(f"{where}: start must be a date (YYYY-MM-DD)")
    if not isinstance(review, dict) or set(review) != REVIEW_KEYS:
        raise PaperError(
            f"{where}: review needs exactly the keys {', '.join(sorted(REVIEW_KEYS))}"
        )
    months, late, drawdown = review["months"], review["max_late_days"], review["max_drawdown"]
    if not _whole(months) or months < 1:
        raise PaperError(f"{where}: review.months must be a whole number of at least 1")
    if not _whole(late) or late < 0:
        raise PaperError(f"{where}: review.max_late_days must be a whole number of at least 0")
    if not isinstance(drawdown, int | float) or isinstance(drawdown, bool) or not 0 < drawdown < 1:
        raise PaperError(f"{where}: review.max_drawdown must be between 0 and 1")
    return PaperFile(
        name=name,
        strategy=strategy,
        strategy_sha256=sha,
        setup=cast(SetupName, setup),
        start=start,
        review=ReviewRules(months=months, max_late_days=late, max_drawdown=float(drawdown)),
    )


def _whole(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_paper_file.py -q`
Expected: PASS.

- [ ] **Step 5: Full check and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
git add src/signalbench/paper/__init__.py src/signalbench/paper/paper_file.py tests/test_paper_file.py
git commit -m "feat: the pre-registered paper file loader (spec 11)"
```

---

### Task 4: Shared test fixtures and the replay

**Files:**
- Create: `tests/paper_helpers.py`
- Create: `src/signalbench/paper/replay.py`
- Create: `tests/test_paper_replay.py`

- [ ] **Step 1: Write the shared test fixtures**

Create `tests/paper_helpers.py`:

```python
"""Shared fixtures for the spec 11 paper tests: BTC breaks out and later crashes, ETH stays
flat, and every calendar day has a candle."""

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from signalbench.ingest.prices import DailyBar
from signalbench.market.bars import AdjustedBar
from signalbench.strategy.config import StrategyConfig, config_sha256, load_strategy_config
from strategy_helpers import make_bar

REPO = Path(__file__).resolve().parents[1]
STRATEGY = "tests/fixtures/strategy_crypto_test.yaml"
DAYS = [date(2014, 1, 1) + timedelta(days=i) for i in range(300)]
START = 250  # 2014-09-08: the paper start day
BREAKOUT = 260  # close 110 on double volume: an entry order at this close
CRASH = 280  # close 100, below the stop: an exit order at this close
Series = dict[str, list[AdjustedBar]]
Fetch = Callable[[str, date], list[DailyBar]]


def btc_bars() -> list[AdjustedBar]:
    bars = []
    for i, day in enumerate(DAYS):
        if i < BREAKOUT:
            bars.append(make_bar(day, 100.0, volume=1000))
        elif i == BREAKOUT:
            bars.append(make_bar(day, 110.0, volume=2000))
        elif i < CRASH:
            bars.append(make_bar(day, 110.0, volume=1000))
        else:
            bars.append(make_bar(day, 100.0, volume=1000))
    return bars


def all_bars() -> Series:
    return {"ETHUSD": [make_bar(day, 50.0, volume=1000) for day in DAYS], "XBTUSD": btc_bars()}


def up_to(series: Series, last: date) -> Series:
    return {symbol: [bar for bar in bars if bar.date <= last] for symbol, bars in series.items()}


def config() -> StrategyConfig:
    return load_strategy_config(REPO / STRATEGY)[0].with_setups(("breakout",))


def strategy_sha() -> str:
    return config_sha256((REPO / STRATEGY).read_bytes())


def write_paper_file(
    directory: Path, *, start: date = DAYS[START], sha: str | None = None
) -> Path:
    path = directory / "paper_crypto-test.yaml"
    path.write_text(
        "name: crypto-test-paper\n"
        f"strategy: {STRATEGY}\n"
        f"strategy_sha256: {sha or strategy_sha()}\n"
        "setup: breakout\n"
        f"start: {start.isoformat()}\n"
        "review:\n  months: 12\n  max_late_days: 7\n  max_drawdown: 0.35\n",
        encoding="utf-8",
    )
    return path


def daily_bar(bar: AdjustedBar) -> DailyBar:
    return DailyBar(
        date=bar.date,
        open=Decimal(str(bar.open)),
        high=Decimal(str(bar.high)),
        low=Decimal(str(bar.low)),
        close=Decimal(str(bar.close)),
        adj_close=Decimal(str(bar.close)),
        volume=bar.volume,
    )


def fake_fetch(series: Series) -> Fetch:
    """Kraken's OHLC endpoint on `series`: every finished candle before `today`."""

    def fetch(pair: str, today: date) -> list[DailyBar]:
        return [daily_bar(bar) for bar in series[pair] if bar.date < today]

    return fetch


def night(day: date) -> datetime:
    """00:30 UTC after `day`'s candle closed: the nightly run that records `day`."""
    return datetime.combine(day + timedelta(days=1), time(0, 30), tzinfo=UTC)
```

- [ ] **Step 2: Write the failing replay tests**

Create `tests/test_paper_replay.py`:

```python
import json
from datetime import date

from signalbench.backtest.crypto_runner import crypto_market
from signalbench.backtest.simulator import simulate
from signalbench.market.calendar import DailySessions
from signalbench.paper.replay import ReplayDay, replay
from signalbench.strategy.market_view import MarketView
from signalbench.strategy.readings import NullReadingsView
from paper_helpers import BREAKOUT, CRASH, DAYS, START, all_bars, config, up_to


def _market(last: date) -> MarketView:
    return crypto_market(up_to(all_bars(), last), config(), DailySessions(), DAYS[START], last)


def _replay(last: date) -> list[ReplayDay]:
    return replay(_market(last), config(), DAYS[START], last)


def test_replay_gives_what_simulate_gives() -> None:
    last = DAYS[-1]
    days = _replay(last)
    result = simulate(_market(last), NullReadingsView(), config(), DAYS[START], last)
    assert [day.point for day in days] == result.equity_curve
    assert [trade for day in days for trade in day.trades] == result.trades
    events = [e for day in days for e in day.events if not str(e["event"]).startswith("order_")]
    assert json.dumps(events) == json.dumps(result.events)


def test_the_breakout_close_orders_an_entry_filled_at_the_next_open() -> None:
    by_day = {day.day: day for day in _replay(DAYS[-1])}
    signal = by_day[DAYS[BREAKOUT]]
    orders = [e for e in signal.events if e["event"] == "order_entry"]
    assert [(o["symbol"], o["setup"], o["signal_close"]) for o in orders] == [
        ("XBTUSD", "breakout", 110.0)
    ]
    filled = by_day[DAYS[BREAKOUT + 1]]
    assert filled.filling is not None
    assert [entry.symbol for entry in filled.filling.entries] == ["XBTUSD"]
    assert [e["symbol"] for e in filled.events if e["event"] == "entry"] == ["XBTUSD"]


def test_the_crash_close_orders_the_exit() -> None:
    by_day = {day.day: day for day in _replay(DAYS[-1])}
    orders = [e for e in by_day[DAYS[CRASH]].events if e["event"] == "order_exit"]
    assert [(o["symbol"], o["reason"]) for o in orders] == [("XBTUSD", "stop")]
    trades = by_day[DAYS[CRASH + 1]].trades
    assert [(t.symbol, t.exit_date) for t in trades] == [("XBTUSD", DAYS[CRASH + 1])]


def test_the_first_day_fills_nothing() -> None:
    first = _replay(DAYS[START + 3])[0]
    assert first.day == DAYS[START]
    assert first.filling is None
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest tests/test_paper_replay.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.paper.replay'`.

- [ ] **Step 4: Write the module**

Create `src/signalbench/paper/replay.py`:

```python
"""Replay a paper portfolio from its start day with the backtest's own step() (spec 11).

Every night the whole paper period is replayed on the stored candles. The days already saved
must come out exactly as saved (store.first_difference); only the new days are appended.
"""

from dataclasses import dataclass
from datetime import date

from signalbench.backtest.simulator import (
    EquityPoint,
    Event,
    Orders,
    SimState,
    TradeRecord,
    initial_state,
    step,
)
from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.market_view import MarketView
from signalbench.strategy.readings import NullReadingsView


@dataclass(frozen=True)
class ReplayDay:
    day: date
    filling: Orders | None  # decided at the previous close, filled at this day's open
    state: SimState  # after this day's close
    events: list[Event]  # step()'s events, then this close's orders (order_exit, order_entry)
    trades: list[TradeRecord]  # closed at this day's open
    point: EquityPoint


def order_events(orders: Orders) -> list[Event]:
    """The orders decided at a close, as events, so the record shows each decision the night
    it was made."""
    day = orders.decided_on.isoformat()
    exits: list[Event] = [
        {"date": day, "event": "order_exit", "position_id": order.position_id,
         "symbol": order.symbol, "reason": order.reason}
        for order in orders.exits
    ]
    entries: list[Event] = [
        {"date": day, "event": "order_entry", "symbol": entry.symbol, "setup": entry.setup,
         "signal_close": entry.signal_close, "stop": entry.stop, "units": entry.units}
        for entry in orders.entries
    ]
    return [*exits, *entries]


def replay(market: MarketView, config: StrategyConfig, start: date, last: date) -> list[ReplayDay]:
    """Every day from `start` to `last`, from the config's start equity in cash."""
    state = initial_state(config)
    days: list[ReplayDay] = []
    for day in market.sessions_between(start, last):
        filling = state.orders
        result = step(state, market, NullReadingsView(), config, day)
        state = result.state
        decided = [] if state.orders is None else order_events(state.orders)
        days.append(
            ReplayDay(
                day=day,
                filling=filling,
                state=state,
                events=[*result.events, *decided],
                trades=result.trades,
                point=result.point,
            )
        )
    return days
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run pytest tests/test_paper_replay.py -q`
Expected: PASS. If `test_the_crash_close_orders_the_exit` fails because the exit reason or day differs, print the events around `DAYS[CRASH]` and report the actual values in the task report before changing the test: the fixture is the one spec 09's runner test uses, where the stop exit fills at `DAYS[CRASH + 1]`.

- [ ] **Step 6: Full check and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
git add tests/paper_helpers.py src/signalbench/paper/replay.py tests/test_paper_replay.py
git commit -m "feat: replay a paper portfolio with the backtest's step() (spec 11)"
```

---

### Task 5: Saved days: read back, compare, append

**Files:**
- Create: `src/signalbench/paper/store.py`
- Create: `tests/test_paper_store.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_store.py`:

```python
from datetime import UTC, date, datetime

from sqlmodel import Session, select

from signalbench.backtest.crypto_runner import crypto_market
from signalbench.db.models import PaperDay, PaperEvent, PaperPortfolio, PaperRun
from signalbench.market.calendar import DailySessions
from signalbench.paper.replay import ReplayDay, replay
from signalbench.paper.store import append_days, find_portfolio, first_difference, load_saved
from paper_helpers import BREAKOUT, DAYS, START, all_bars, config, up_to

T = datetime(2014, 9, 9, 0, 30, tzinfo=UTC)


def _ids(session: Session) -> tuple[int, int]:
    run = PaperRun(started_at=T, status="running", git_sha="abc123")
    portfolio = PaperPortfolio(
        name="crypto-test-paper", paper_file="paper.yaml", strategy_sha256="f" * 64,
        start=DAYS[START],
    )
    session.add(run)
    session.add(portfolio)
    session.commit()
    session.refresh(run)
    session.refresh(portfolio)
    assert run.id is not None and portfolio.id is not None
    return portfolio.id, run.id


def _replay(last: date) -> list[ReplayDay]:
    market = crypto_market(up_to(all_bars(), last), config(), DailySessions(), DAYS[START], last)
    return replay(market, config(), DAYS[START], last)


def _save(session: Session, days: list[ReplayDay]) -> int:
    portfolio_id, run_id = _ids(session)
    append_days(session, portfolio_id=portfolio_id, run_id=run_id, days=days, now=T)
    session.commit()
    session.expire_all()
    return portfolio_id


def test_saved_days_read_back_exactly_as_replayed(session: Session) -> None:
    days = _replay(DAYS[-1])
    portfolio_id = _save(session, days)
    assert first_difference(load_saved(session, portfolio_id), days) is None


def test_a_later_replay_extends_the_saved_days_unchanged(session: Session) -> None:
    portfolio_id = _save(session, _replay(DAYS[BREAKOUT + 5]))
    later = _replay(DAYS[-1])
    saved = load_saved(session, portfolio_id)
    assert first_difference(saved, later) is None
    assert [day.day for day in later[len(saved):]] == DAYS[BREAKOUT + 6 :]


def test_an_edited_equity_is_reported(session: Session) -> None:
    days = _replay(DAYS[START + 5])
    portfolio_id = _save(session, days)
    row = session.get(PaperDay, (portfolio_id, DAYS[START + 2]))
    assert row is not None
    row.equity += 0.01
    session.add(row)
    session.commit()
    message = first_difference(load_saved(session, portfolio_id), days)
    assert message is not None
    assert message.startswith(f"{DAYS[START + 2].isoformat()}: equity differs")


def test_a_lost_event_is_reported(session: Session) -> None:
    days = _replay(DAYS[BREAKOUT + 1])
    portfolio_id = _save(session, days)
    entry = session.exec(select(PaperEvent).where(PaperEvent.kind == "entry")).one()
    session.delete(entry)
    session.commit()
    message = first_difference(load_saved(session, portfolio_id), days)
    assert message is not None
    assert message.startswith(f"{DAYS[BREAKOUT + 1].isoformat()}: events differs")


def test_a_saved_day_the_replay_does_not_reach_is_reported(session: Session) -> None:
    portfolio_id = _save(session, _replay(DAYS[START + 5]))
    shorter = _replay(DAYS[START + 3])
    assert first_difference(load_saved(session, portfolio_id), shorter) == (
        f"{DAYS[START + 4].isoformat()}: saved but not replayed"
    )


def test_events_are_saved_with_their_kind_run_and_time(session: Session) -> None:
    _save(session, _replay(DAYS[BREAKOUT + 1]))
    entry = session.exec(select(PaperEvent).where(PaperEvent.kind == "entry")).one()
    assert (entry.day, entry.payload["symbol"], entry.recorded_at) == (
        DAYS[BREAKOUT + 1], "XBTUSD", T,
    )


def test_find_portfolio_by_name(session: Session) -> None:
    _ids(session)
    found = find_portfolio(session, "crypto-test-paper")
    assert found is not None and found.start == DAYS[START]
    assert find_portfolio(session, "other") is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_paper_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.paper.store'`.

- [ ] **Step 3: Write the module**

Create `src/signalbench/paper/store.py`:

```python
"""Saved paper days (spec 11): read them back, compare them with a replay, append new ones.

Rows are never updated or deleted. Values are compared after the same JSON round-trip they
went through when saved, so a float or a date string reads back exactly.
"""

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlmodel import Session, col, select

from signalbench.backtest.sim_state import state_to_json
from signalbench.db.models import PaperDay, PaperEvent, PaperPortfolio
from signalbench.paper.replay import ReplayDay

MAX_SHOWN = 200  # characters of each value in a mismatch message


@dataclass(frozen=True)
class SavedDay:
    day: date
    equity: float
    cash: float
    state: dict[str, Any]
    events: list[dict[str, Any]]


def as_json(value: Any) -> Any:
    """`value` as it reads back from a JSON column."""
    return json.loads(json.dumps(value))


def row_values(day: ReplayDay) -> SavedDay:
    """A replayed day as it is saved."""
    return SavedDay(
        day=day.day,
        equity=day.point.equity,
        cash=day.point.cash,
        state=as_json(state_to_json(day.state)),
        events=[as_json(event) for event in day.events],
    )


def find_portfolio(session: Session, name: str) -> PaperPortfolio | None:
    return session.exec(select(PaperPortfolio).where(PaperPortfolio.name == name)).first()


def load_saved(session: Session, portfolio_id: int) -> list[SavedDay]:
    days = session.exec(
        select(PaperDay)
        .where(PaperDay.portfolio_id == portfolio_id)
        .order_by(col(PaperDay.day))
    ).all()
    events = session.exec(
        select(PaperEvent)
        .where(PaperEvent.portfolio_id == portfolio_id)
        .order_by(col(PaperEvent.id))
    ).all()
    by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_day[event.day].append(event.payload)
    return [
        SavedDay(day.day, day.equity, day.cash, day.state, by_day.get(day.day, []))
        for day in days
    ]


def first_difference(saved: Sequence[SavedDay], replayed: Sequence[ReplayDay]) -> str | None:
    """None when every saved day comes out of the replay exactly as saved; otherwise the first
    difference, starting with its day."""
    for old, day in zip(saved, replayed, strict=False):
        new = row_values(day)
        if old.day != new.day:
            return f"{old.day.isoformat()}: replays as {new.day.isoformat()}"
        for name in ("equity", "cash", "state", "events"):
            before, after = getattr(old, name), getattr(new, name)
            if before != after:
                return (
                    f"{old.day.isoformat()}: {name} differs "
                    f"(saved {_shown(before)}, replayed {_shown(after)})"
                )
    if len(saved) > len(replayed):
        return f"{saved[len(replayed)].day.isoformat()}: saved but not replayed"
    return None


def append_days(
    session: Session,
    *,
    portfolio_id: int,
    run_id: int,
    days: Sequence[ReplayDay],
    now: datetime,
) -> None:
    """Add the rows of `days`; the caller commits them in one transaction."""
    for day in days:
        values = row_values(day)
        session.add(
            PaperDay(
                portfolio_id=portfolio_id, day=day.day, equity=values.equity,
                cash=values.cash, state=values.state, recorded_at=now, run_id=run_id,
            )
        )
        for event in values.events:
            session.add(
                PaperEvent(
                    portfolio_id=portfolio_id, day=day.day, kind=str(event["event"]),
                    payload=event, recorded_at=now, run_id=run_id,
                )
            )


def _shown(value: Any) -> str:
    text = json.dumps(value, default=str)
    return text if len(text) <= MAX_SHOWN else f"{text[: MAX_SHOWN - 3]}..."
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_paper_store.py -q`
Expected: PASS.

- [ ] **Step 5: Full check and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
git add src/signalbench/paper/store.py tests/test_paper_store.py
git commit -m "feat: save paper days append-only and compare them with a replay (spec 11)"
```

---

### Task 6: PAPER message texts

**Files:**
- Create: `src/signalbench/paper/messages.py`
- Create: `tests/test_paper_messages.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_messages.py`:

```python
from dataclasses import replace
from datetime import date

from signalbench.backtest.simulator import EquityPoint, Event, Orders, TradeRecord, initial_state
from signalbench.paper.messages import (
    alert_text,
    closed_texts,
    day_texts,
    fill_texts,
    order_texts,
)
from signalbench.paper.replay import ReplayDay
from signalbench.strategy.decision import EntryOrder, ExitOrder
from paper_helpers import config

DAY = date(2026, 10, 20)
BEFORE = date(2026, 10, 19)
ENTRY = EntryOrder(
    symbol="XBTUSD", setup="breakout", sector="crypto", signal_close=62410.0, stop=58900.0,
    target_r=None, time_limit=None, units=0.0005, risk_amount=1.755, catalyst=False, rank=1,
)
EXIT = ExitOrder(position_id="P00001", symbol="ETHUSD", reason="stop")
FILL: Event = {
    "date": DAY.isoformat(), "event": "entry", "position_id": "P00002", "symbol": "XBTUSD",
    "setup": "breakout", "units": 0.000527, "price": 62661.648, "trimmed": False,
}
TRADE = TradeRecord(
    position_id="P00001", symbol="XBTUSD", setup="breakout", sector="crypto",
    signal_date=date(2026, 10, 1), entry_date=date(2026, 10, 2), exit_signal_date=BEFORE,
    exit_date=DAY, signal_close=60000.0, entry_price=60000.0, exit_price=65040.0,
    initial_stop=58000.0, units=0.0005, r=2.31, pnl=2.52, reason="stop", sessions_held=18,
)


def _day(
    *,
    filling: Orders | None = None,
    orders: Orders | None = None,
    events: list[Event] | None = None,
    trades: list[TradeRecord] | None = None,
) -> ReplayDay:
    return ReplayDay(
        day=DAY,
        filling=filling,
        state=replace(initial_state(config()), orders=orders),
        events=events or [],
        trades=trades or [],
        point=EquityPoint(DAY, 100.0, 100.0, 0),
    )


def test_orders_decided_at_the_close() -> None:
    day = _day(orders=Orders(DAY, [EXIT], [ENTRY]))
    assert order_texts("crypto-v1", day) == [
        "PAPER crypto-v1: SELL ETHUSD at the next open (stop).",
        "PAPER crypto-v1: BUY XBTUSD at the next open (breakout). "
        "Signal close 62,410.00, stop 58,900.00.",
    ]


def test_an_entry_filled_at_the_open() -> None:
    day = _day(filling=Orders(BEFORE, [], [ENTRY]), events=[FILL])
    assert fill_texts(day, 0.004) == [
        "PAPER filled: BUY XBTUSD 0.000527 at 62,661.65 (open 62,412.00 + 0.4%), 33.02 spent."
    ]


def test_an_entry_skipped_at_the_open() -> None:
    skip: Event = {
        "date": DAY.isoformat(), "event": "skip", "symbol": "XBTUSD", "setup": "breakout",
        "reason": "gap_up",
    }
    day = _day(filling=Orders(BEFORE, [], [ENTRY]), events=[skip])
    assert fill_texts(day, 0.004) == ["PAPER not filled: BUY XBTUSD (gap_up)."]


def test_a_closed_trade() -> None:
    assert closed_texts(_day(trades=[TRADE])) == [
        "PAPER closed: XBTUSD +2.31R (+8.4%), held 18 days (stop)."
    ]


def test_a_day_reads_closed_then_filled_then_ordered() -> None:
    day = _day(
        filling=Orders(BEFORE, [], [ENTRY]), orders=Orders(DAY, [EXIT], []), events=[FILL],
        trades=[TRADE],
    )
    texts = day_texts("crypto-v1", day, 0.004)
    assert [text.split(":")[0] for text in texts] == [
        "PAPER closed", "PAPER filled", "PAPER crypto-v1",
    ]


def test_nothing_happened_sends_nothing() -> None:
    assert day_texts("crypto-v1", _day(orders=Orders(DAY, [], [])), 0.004) == []


def test_alert() -> None:
    assert alert_text("KrakenError: down") == "PAPER ALERT: KrakenError: down"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_paper_messages.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.paper.messages'`.

- [ ] **Step 3: Write the module**

Create `src/signalbench/paper/messages.py`:

```python
"""The paper job's Telegram texts (spec 11). Every one starts with PAPER, so none can be read
as a live trade alert."""

from signalbench.backtest.simulator import Event
from signalbench.paper.replay import ReplayDay


def order_texts(version: str, day: ReplayDay) -> list[str]:
    """The orders decided at this day's close, to fill at the next open."""
    orders = day.state.orders
    if orders is None:
        return []
    exits = [
        f"PAPER {version}: SELL {order.symbol} at the next open ({order.reason})."
        for order in orders.exits
    ]
    entries = [
        f"PAPER {version}: BUY {entry.symbol} at the next open ({entry.setup}). "
        f"Signal close {entry.signal_close:,.2f}, stop {entry.stop:,.2f}."
        for entry in orders.entries
    ]
    return [*exits, *entries]


def fill_texts(day: ReplayDay, cost_per_side: float) -> list[str]:
    """Each entry ordered at the previous close: filled at this day's open, or why not."""
    if day.filling is None:
        return []
    texts: list[str] = []
    for entry in day.filling.entries:
        filled = _first(day.events, "entry", entry.symbol)
        if filled is None:
            skipped = _first(day.events, "skip", entry.symbol)
            reason = "no fill" if skipped is None else str(skipped["reason"])
            texts.append(f"PAPER not filled: BUY {entry.symbol} ({reason}).")
            continue
        price, units = _number(filled, "price"), _number(filled, "units")
        texts.append(
            f"PAPER filled: BUY {entry.symbol} {units:.6f} at {price:,.2f} "
            f"(open {price / (1.0 + cost_per_side):,.2f} + {cost_per_side:.1%}), "
            f"{units * price:,.2f} spent."
        )
    return texts


def closed_texts(day: ReplayDay) -> list[str]:
    return [
        f"PAPER closed: {trade.symbol} {trade.r:+.2f}R "
        f"({trade.exit_price / trade.entry_price - 1.0:+.1%}), "
        f"held {trade.sessions_held} days ({trade.reason})."
        for trade in day.trades
    ]


def day_texts(version: str, day: ReplayDay, cost_per_side: float) -> list[str]:
    """In the order they happened: exits and entries at the open, then the close's orders."""
    return [*closed_texts(day), *fill_texts(day, cost_per_side), *order_texts(version, day)]


def alert_text(error: str) -> str:
    return f"PAPER ALERT: {error}"


def _first(events: list[Event], kind: str, symbol: str) -> Event | None:
    """The first `kind` event for `symbol`: open-time events come before the close's."""
    return next((e for e in events if e["event"] == kind and e.get("symbol") == symbol), None)


def _number(event: Event, key: str) -> float:
    value = event[key]
    if not isinstance(value, int | float):
        raise TypeError(f"{event['event']} event has a non-numeric {key}: {value!r}")
    return float(value)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_paper_messages.py -q`
Expected: PASS.

- [ ] **Step 5: Full check and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
git add src/signalbench/paper/messages.py tests/test_paper_messages.py
git commit -m "feat: PAPER texts for orders, fills, closed trades, and alerts (spec 11)"
```

---

### Task 7: Weekly summary and the 12-month review

**Files:**
- Create: `src/signalbench/paper/summary.py`
- Create: `tests/test_paper_summary.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_summary.py`:

```python
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pytest import approx
from sqlmodel import Session

from signalbench.db.models import PaperDay, PaperEvent, PaperPortfolio, PaperRun, Price
from signalbench.ingest.kraken import crypto_ticker
from signalbench.paper.paper_file import PaperError, PaperFile, ReviewRules
from signalbench.paper.summary import (
    close_deadline,
    paper_review,
    paper_summary,
    summary_text,
    worst_drawdown,
)

START = date(2026, 10, 13)
REVIEW = date(2027, 10, 13)
PAPER = PaperFile(
    name="crypto-v1-paper", strategy="data/strategy_crypto-v1.yaml", strategy_sha256="f" * 64,
    setup="breakout", start=START, review=ReviewRules(months=12, max_late_days=7, max_drawdown=0.35),
)
POSITION = {
    "symbol": "XBTUSD", "units": 0.000527, "entry_date": "2026-10-14",
    "entry_price": 62661.65, "stop": 58900.0,
}


def _setup(session: Session) -> tuple[PaperPortfolio, PaperRun]:
    run = PaperRun(
        started_at=datetime(2026, 10, 14, 0, 30, tzinfo=UTC), status="ok", git_sha="abc",
        finished_at=datetime(2026, 10, 14, 0, 31, tzinfo=UTC),
    )
    portfolio = PaperPortfolio(
        name=PAPER.name, paper_file="paper_crypto-v1.yaml", strategy_sha256="f" * 64,
        start=START,
    )
    session.add(run)
    session.add(portfolio)
    session.commit()
    session.refresh(run)
    session.refresh(portfolio)
    return portfolio, run


def _days(
    session: Session,
    portfolio: PaperPortfolio,
    run: PaperRun,
    count: int,
    equity: Callable[[int], float] = lambda _i: 100.0,
    late: set[int] | None = None,
    skip: set[int] | None = None,
) -> None:
    assert portfolio.id is not None and run.id is not None
    for i in range(count):
        if skip and i in skip:
            continue
        day = START + timedelta(days=i)
        on_time = close_deadline(day) - timedelta(hours=23)
        recorded = close_deadline(day) + timedelta(hours=1) if late and i in late else on_time
        session.add(
            PaperDay(
                portfolio_id=portfolio.id, day=day, equity=equity(i), cash=equity(i),
                state={"positions": [POSITION] if i == count - 1 else []},
                recorded_at=recorded, run_id=run.id,
            )
        )
    session.commit()


def test_close_deadline_is_24_hours_after_the_candle_closes() -> None:
    assert close_deadline(START) == datetime(2026, 10, 15, 0, 0, tzinfo=UTC)


def test_worst_drawdown() -> None:
    assert worst_drawdown([100.0, 120.0, 90.0, 130.0]) == approx(0.25)
    assert worst_drawdown([100.0]) == 0.0


def test_summary_of_a_few_days(session: Session) -> None:
    portfolio, run = _setup(session)
    _days(session, portfolio, run, 3, equity=lambda i: [100.0, 110.0, 99.0][i], late={1})
    assert portfolio.id is not None and run.id is not None
    session.add(
        PaperEvent(
            portfolio_id=portfolio.id, day=START, kind="exit", payload={"event": "exit"},
            recorded_at=run.started_at, run_id=run.id,
        )
    )
    session.add(
        PaperRun(
            started_at=datetime(2026, 10, 15, 6, 30, tzinfo=UTC), status="failed",
            git_sha="abc", error="KrakenError: down",
        )
    )
    session.commit()
    now = datetime(2026, 10, 16, 0, 40, tzinfo=UTC)
    summary = paper_summary(session, portfolio, PAPER, 100.0, now)
    assert summary.last_day == START + timedelta(days=2)
    assert summary.equity == 99.0
    assert summary.btc_equity is None  # no BTC prices stored
    assert summary.open_positions == ["XBTUSD 0.000527 since 2026-10-14 at 62,661.65, stop 58,900.00"]
    assert (summary.closed_trades, summary.late_days, summary.failed_runs_7d) == (1, 1, 1)
    assert summary.worst_drawdown == approx(0.1)
    assert summary.days_to_review == (REVIEW - now.date()).days
    assert summary.last_ok_run == datetime(2026, 10, 14, 0, 31, tzinfo=UTC)
    assert summary_text(PAPER, summary) == "\n".join(
        [
            "PAPER crypto-v1-paper: weekly summary to 2026-10-15",
            "Equity 99.00 (start 100.00); BTC buy-and-hold n/a",
            "Open: XBTUSD 0.000527 since 2026-10-14 at 62,661.65, stop 58,900.00",
            "Closed trades: 1",
            "Worst drawdown: 10.0% (limit 35%)",
            "Late days: 1 (limit 7)",
            "Failed runs in the last 7 days: 1",
            f"Review on 2027-10-13 ({(REVIEW - now.date()).days} days)",
        ]
    )


def test_summary_compares_with_btc_bought_at_the_start_close(session: Session) -> None:
    portfolio, run = _setup(session)
    _days(session, portfolio, run, 3)
    ticker = crypto_ticker(session, "XBTUSD")
    for day, close in ((START, "60000"), (START + timedelta(days=2), "66000")):
        price = Decimal(close)
        session.add(
            Price(
                ticker_id=ticker.id, date=day, open=price, high=price, low=price, close=price,
                adj_close=price, volume=1,
            )
        )
    session.commit()
    summary = paper_summary(session, portfolio, PAPER, 100.0, datetime(2026, 10, 16, tzinfo=UTC))
    assert summary.btc_equity == approx(110.0)


def test_summary_before_the_first_day(session: Session) -> None:
    portfolio, _run = _setup(session)
    summary = paper_summary(session, portfolio, PAPER, 100.0, datetime(2026, 10, 13, tzinfo=UTC))
    assert (summary.last_day, summary.equity, summary.open_positions) == (None, 100.0, [])
    assert summary_text(PAPER, summary).startswith("PAPER crypto-v1-paper: weekly summary\n")


def test_review_refuses_before_the_review_date(session: Session) -> None:
    portfolio, _run = _setup(session)
    with pytest.raises(PaperError, match="The review is on 2027-10-13"):
        paper_review(session, portfolio, PAPER, 100.0, REVIEW - timedelta(days=1))


def test_review_on_track(session: Session) -> None:
    portfolio, run = _setup(session)
    _days(session, portfolio, run, 365, late=set(range(7)))
    review = paper_review(session, portfolio, PAPER, 100.0, REVIEW)
    assert review.on_track
    assert review.lines[-1] == "ON TRACK"


@pytest.mark.parametrize(
    ("late", "skip", "equity", "failing"),
    [
        (set(range(8)), set(), lambda _i: 100.0, "1. On time: FAIL"),
        (set(), {100}, lambda _i: 100.0, "1. On time: FAIL"),
        (set(), set(), lambda i: 64.0 if i == 50 else 100.0, "3. Drawdown: FAIL"),
    ],
)
def test_review_off_track(
    session: Session,
    late: set[int],
    skip: set[int],
    equity: Callable[[int], float],
    failing: str,
) -> None:
    portfolio, run = _setup(session)
    _days(session, portfolio, run, 365, equity=equity, late=late, skip=skip)
    review = paper_review(session, portfolio, PAPER, 100.0, REVIEW)
    assert not review.on_track
    assert any(line.startswith(failing) for line in review.lines)
    assert review.lines[-1] == "OFF TRACK"


def test_review_off_track_after_a_replay_mismatch(session: Session) -> None:
    portfolio, run = _setup(session)
    _days(session, portfolio, run, 365)
    session.add(
        PaperRun(
            started_at=datetime(2027, 3, 1, 0, 30, tzinfo=UTC), status="mismatch",
            git_sha="abc", error="replay mismatch on 2027-02-01: equity differs",
        )
    )
    session.commit()
    review = paper_review(session, portfolio, PAPER, 100.0, REVIEW)
    assert not review.on_track
    assert any(line.startswith("2. Matches the backtest code: FAIL") for line in review.lines)
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_paper_summary.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.paper.summary'`.

- [ ] **Step 3: Write the module**

Create `src/signalbench/paper/summary.py`:

```python
"""The weekly summary, `crypto paper status`, and the 12-month review (spec 11).

The review gives on track or off track from three checks fixed in the spec: every day
recorded with at most `max_late_days` late, no replay mismatch, and the worst drawdown under
`max_drawdown`. Profit and the BTC comparison are information only.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlmodel import Session, col, select

from signalbench.backtest.crypto_runner import BTC
from signalbench.db.models import PaperDay, PaperEvent, PaperPortfolio, PaperRun
from signalbench.ingest.kraken import find_crypto_ticker
from signalbench.market.bars import adjusted_bars
from signalbench.paper.paper_file import PaperError, PaperFile

FAILED = ("failed", "mismatch")


@dataclass(frozen=True)
class PaperSummary:
    start_equity: float
    last_day: date | None  # the last saved day; None before the first
    equity: float  # at the last saved day
    btc_equity: float | None  # the start equity in BTC bought at the start day's close
    open_positions: list[str]
    closed_trades: int
    worst_drawdown: float
    late_days: int
    failed_runs_7d: int
    days_to_review: int
    last_ok_run: datetime | None


@dataclass(frozen=True)
class Review:
    on_track: bool
    lines: list[str]  # one per check, then ON TRACK or OFF TRACK


def close_deadline(day: date) -> datetime:
    """After this, `day`'s row is late: 24 hours after its candle closed (00:00 UTC, day + 1)."""
    return datetime.combine(day + timedelta(days=2), time(0, 0), tzinfo=UTC)


def worst_drawdown(equities: Sequence[float]) -> float:
    peak = worst = 0.0
    for equity in equities:
        peak = max(peak, equity)
        if peak > 0.0:
            worst = max(worst, 1.0 - equity / peak)
    return worst


def paper_summary(
    session: Session,
    portfolio: PaperPortfolio,
    paper: PaperFile,
    start_equity: float,
    now: datetime,
) -> PaperSummary:
    days = _days(session, portfolio)
    last = days[-1] if days else None
    closed = session.exec(
        select(PaperEvent.id).where(
            PaperEvent.portfolio_id == portfolio.id, PaperEvent.kind == "exit"
        )
    ).all()
    failed = session.exec(
        select(PaperRun.id).where(
            col(PaperRun.started_at) >= now - timedelta(days=7),
            col(PaperRun.status).in_(FAILED),
        )
    ).all()
    last_ok = session.exec(
        select(PaperRun.finished_at)
        .where(PaperRun.status == "ok")
        .order_by(col(PaperRun.id).desc())
    ).first()
    return PaperSummary(
        start_equity=start_equity,
        last_day=None if last is None else last.day,
        equity=start_equity if last is None else last.equity,
        btc_equity=None if last is None else _btc_equity(session, paper.start, last.day, start_equity),
        open_positions=[] if last is None else _positions(last.state),
        closed_trades=len(closed),
        worst_drawdown=worst_drawdown([start_equity, *(day.equity for day in days)]),
        late_days=sum(_late(day) for day in days),
        failed_runs_7d=len(failed),
        days_to_review=(paper.review_date - now.astimezone(UTC).date()).days,
        last_ok_run=last_ok,
    )


def summary_text(paper: PaperFile, summary: PaperSummary) -> str:
    to = "" if summary.last_day is None else f" to {summary.last_day.isoformat()}"
    btc = "n/a" if summary.btc_equity is None else f"{summary.btc_equity:,.2f}"
    held = "; ".join(summary.open_positions) or "none"
    return "\n".join(
        [
            f"PAPER {paper.name}: weekly summary{to}",
            f"Equity {summary.equity:,.2f} (start {summary.start_equity:,.2f}); "
            f"BTC buy-and-hold {btc}",
            f"Open: {held}",
            f"Closed trades: {summary.closed_trades}",
            f"Worst drawdown: {summary.worst_drawdown:.1%} "
            f"(limit {paper.review.max_drawdown:.0%})",
            f"Late days: {summary.late_days} (limit {paper.review.max_late_days})",
            f"Failed runs in the last 7 days: {summary.failed_runs_7d}",
            f"Review on {paper.review_date.isoformat()} ({summary.days_to_review} days)",
        ]
    )


def paper_review(
    session: Session,
    portfolio: PaperPortfolio,
    paper: PaperFile,
    start_equity: float,
    today: date,
) -> Review:
    """The three checks over every day from the start to the day before the review date."""
    if today < paper.review_date:
        raise PaperError(f"The review is on {paper.review_date.isoformat()}; today is {today}.")
    end = paper.review_date - timedelta(days=1)
    days = [day for day in _days(session, portfolio) if paper.start <= day.day <= end]
    have = {day.day for day in days}
    missing = [
        paper.start + timedelta(days=i)
        for i in range((end - paper.start).days + 1)
        if paper.start + timedelta(days=i) not in have
    ]
    late = sum(_late(day) for day in days)
    mismatches = session.exec(
        select(PaperRun.id).where(
            PaperRun.status == "mismatch", col(PaperRun.started_at) <= close_deadline(end)
        )
    ).all()
    drawdown = worst_drawdown([start_equity, *(day.equity for day in days)])
    rules = paper.review
    on_time = not missing and late <= rules.max_late_days
    matches = not mismatches
    shallow = drawdown < rules.max_drawdown
    first_missing = f", first {missing[0].isoformat()}" if missing else ""
    lines = [
        f"1. On time: {_verdict(on_time)}: {len(missing)} missing days{first_missing}, "
        f"{late} late (limit {rules.max_late_days})",
        f"2. Matches the backtest code: {_verdict(matches)}: {len(mismatches)} replay mismatches",
        f"3. Drawdown: {_verdict(shallow)}: worst {drawdown:.1%} (limit {rules.max_drawdown:.0%})",
    ]
    on_track = on_time and matches and shallow
    return Review(on_track, [*lines, "ON TRACK" if on_track else "OFF TRACK"])


def _days(session: Session, portfolio: PaperPortfolio) -> list[PaperDay]:
    return list(
        session.exec(
            select(PaperDay)
            .where(PaperDay.portfolio_id == portfolio.id)
            .order_by(col(PaperDay.day))
        ).all()
    )


def _late(day: PaperDay) -> bool:
    return day.recorded_at > close_deadline(day.day)


def _verdict(passed: bool) -> str:
    return "pass" if passed else "FAIL"


def _btc_equity(session: Session, start: date, last: date, start_equity: float) -> float | None:
    ticker = find_crypto_ticker(session, BTC)
    if ticker is None:
        return None
    closes = {bar.date: bar.close for bar in adjusted_bars(session, ticker.id, start=start, end=last)}
    if start not in closes or last not in closes:
        return None
    return start_equity * closes[last] / closes[start]


def _positions(state: dict[str, Any]) -> list[str]:
    return [
        f"{p['symbol']} {p['units']:.6f} since {p['entry_date']} at {p['entry_price']:,.2f}, "
        f"stop {p['stop']:,.2f}"
        for p in state.get("positions", [])
    ]
```

Note: check the signature of `adjusted_bars` in `src/signalbench/market/bars.py` (`adjusted_bars(session, ticker_id, start=None, end=...)`); pass `start` and `end` as keywords as shown.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_paper_summary.py -q`
Expected: PASS. If `recorded_at` comparisons fail on SQLite because the value reads back naive, check `UTCDateTime.process_result_value` in `models.py` (it makes values aware); do not change it, fix the test instead.

- [ ] **Step 5: Full check and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
git add src/signalbench/paper/summary.py tests/test_paper_summary.py
git commit -m "feat: the weekly paper summary and the 12-month review (spec 11)"
```

---

### Task 8: `start_paper` and the nightly `run_paper`

**Files:**
- Create: `src/signalbench/paper/run.py`
- Create: `tests/test_paper_run.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_run.py`:

```python
import json
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest
from sqlmodel import Session, col, select

from signalbench.backtest.crypto_runner import crypto_market
from signalbench.backtest.simulator import simulate
from signalbench.db.models import PaperDay, PaperEvent, PaperPortfolio, PaperRun, Price
from signalbench.ingest.kraken import KrakenError
from signalbench.ingest.prices import DailyBar
from signalbench.live.messenger import FakeMessenger
from signalbench.market.calendar import DailySessions
from signalbench.paper.paper_file import PaperError
from signalbench.paper.run import PaperOutcome, run_paper, start_paper
from signalbench.strategy.readings import NullReadingsView
from paper_helpers import (
    BREAKOUT,
    CRASH,
    DAYS,
    REPO,
    START,
    Fetch,
    all_bars,
    config,
    fake_fetch,
    night,
    write_paper_file,
)

SUMMARY = "PAPER crypto-test-paper: weekly summary"


def _yes(_path: Path) -> bool:
    return True


def _start(session: Session, paper: Path) -> PaperPortfolio:
    return start_paper(
        session, repo=REPO, paper_path=paper,
        now=datetime.combine(DAYS[START], time(12, 0), tzinfo=UTC),
        fetch=fake_fetch(all_bars()), committed=_yes,
    )


def _run(
    session: Session,
    paper: Path,
    day: date,
    *,
    messenger: FakeMessenger | None = None,
    fetch: Fetch | None = None,
    committed: Callable[[Path], bool] = _yes,
    held: bool = True,
) -> PaperOutcome:
    return run_paper(
        session, lock=nullcontext(held), repo=REPO, paper_path=paper,
        clock=lambda: night(day), fetch=fetch or fake_fetch(all_bars()), committed=committed,
        git_sha="abc123", messenger=messenger or FakeMessenger(), echo=lambda _text: None,
    )


@pytest.fixture
def paper(session: Session, tmp_path: Path) -> Path:
    path = write_paper_file(tmp_path)
    _start(session, path)
    return path


def test_start_creates_the_portfolio_and_loads_the_history(
    session: Session, paper: Path
) -> None:
    portfolio = session.exec(select(PaperPortfolio)).one()
    assert (portfolio.name, portfolio.start, portfolio.paper_file) == (
        "crypto-test-paper", DAYS[START], "paper_crypto-test.yaml",
    )
    assert max(price.date for price in session.exec(select(Price)).all()) == DAYS[START - 1]


def test_start_refuses_a_second_portfolio(session: Session, paper: Path) -> None:
    with pytest.raises(PaperError, match="already exists"):
        _start(session, paper)


def test_start_refuses_a_start_day_that_has_closed(session: Session, tmp_path: Path) -> None:
    with pytest.raises(PaperError, match="has already closed"):
        start_paper(
            session, repo=REPO, paper_path=write_paper_file(tmp_path), now=night(DAYS[START]),
            fetch=fake_fetch(all_bars()), committed=_yes,
        )


def test_start_refuses_an_uncommitted_paper_file(session: Session, tmp_path: Path) -> None:
    with pytest.raises(PaperError, match="must be committed"):
        start_paper(
            session, repo=REPO, paper_path=write_paper_file(tmp_path),
            now=datetime.combine(DAYS[START], time(12, 0), tzinfo=UTC),
            fetch=fake_fetch(all_bars()), committed=lambda _path: False,
        )


def test_start_refuses_a_changed_strategy(session: Session, tmp_path: Path) -> None:
    with pytest.raises(PaperError, match="the strategy changed"):
        _start(session, write_paper_file(tmp_path, sha="0" * 64))


def test_nightly_runs_store_exactly_what_one_backtest_gives(session: Session, paper: Path) -> None:
    for day in DAYS[START:]:
        assert _run(session, paper, day).status == "ok"
    last = DAYS[-1]
    market = crypto_market(all_bars(), config(), DailySessions(), DAYS[START], last)
    result = simulate(market, NullReadingsView(), config(), DAYS[START], last)
    days = session.exec(select(PaperDay).order_by(col(PaperDay.day))).all()
    assert [(d.day, d.equity, d.cash) for d in days] == [
        (p.date, p.equity, p.cash) for p in result.equity_curve
    ]
    events = session.exec(select(PaperEvent).order_by(col(PaperEvent.id))).all()
    simulated = [e.payload for e in events if not e.kind.startswith("order_")]
    assert simulated == json.loads(json.dumps(result.events))
    assert [e.payload["symbol"] for e in events if e.kind == "exit"] == ["XBTUSD"]


def test_the_trade_is_messaged_as_order_fill_and_close(session: Session, paper: Path) -> None:
    messenger = FakeMessenger()
    for day in DAYS[START:]:
        _run(session, paper, day, messenger=messenger)
    texts = messenger.texts()
    assert any(
        t.startswith("PAPER crypto-test: BUY XBTUSD at the next open (breakout). Signal close 110.00")
        for t in texts
    )
    assert any(t.startswith("PAPER filled: BUY XBTUSD") and "(open 110.00 + 0.4%)" in t for t in texts)
    assert any(t.startswith("PAPER crypto-test: SELL XBTUSD at the next open (stop)") for t in texts)
    assert any(t.startswith("PAPER closed: XBTUSD") for t in texts)


def test_a_second_run_the_same_night_adds_nothing_and_sends_nothing(
    session: Session, paper: Path
) -> None:
    messenger = FakeMessenger()
    assert _run(session, paper, DAYS[START], messenger=messenger).days_added == 1
    sent = len(messenger.sent)
    again = _run(session, paper, DAYS[START], messenger=messenger)
    assert (again.status, again.days_added) == ("ok", 0)
    assert len(messenger.sent) == sent


def test_before_the_start_day_closes_nothing_is_added(session: Session, paper: Path) -> None:
    outcome = _run(session, paper, DAYS[START - 1])
    assert (outcome.status, outcome.days_added) == ("ok", 0)


def test_an_edited_saved_row_is_a_replay_mismatch(session: Session, paper: Path) -> None:
    for day in DAYS[START : START + 3]:
        _run(session, paper, day)
    row = session.exec(select(PaperDay).order_by(col(PaperDay.day))).first()
    assert row is not None
    row.equity += 0.01
    session.add(row)
    session.commit()
    messenger = FakeMessenger()
    outcome = _run(session, paper, DAYS[START + 3], messenger=messenger)
    assert outcome.status == "mismatch"
    assert outcome.error is not None
    assert outcome.error.startswith(f"replay mismatch on {DAYS[START].isoformat()}: equity differs")
    assert messenger.texts() == [f"PAPER ALERT: {outcome.error}"]
    assert len(session.exec(select(PaperDay)).all()) == 3
    assert _run(session, paper, DAYS[START + 4]).status == "mismatch"
    run = session.get(PaperRun, outcome.run_id)
    assert run is not None and (run.status, run.days_added) == ("mismatch", 0)


def test_a_changed_strategy_fails_the_run(session: Session, paper: Path) -> None:
    write_paper_file(paper.parent, sha="0" * 64)  # the same path, now wrong
    messenger = FakeMessenger()
    outcome = _run(session, paper, DAYS[START], messenger=messenger)
    assert outcome.status == "failed"
    assert outcome.error is not None and "the strategy changed" in outcome.error
    assert messenger.texts()[0].startswith("PAPER ALERT:")


def test_an_uncommitted_paper_file_fails_the_run(session: Session, paper: Path) -> None:
    outcome = _run(session, paper, DAYS[START], committed=lambda _path: False)
    assert outcome.status == "failed"
    assert outcome.error is not None and "must be committed" in outcome.error


def test_a_kraken_error_fails_the_run_and_saves_nothing(session: Session, paper: Path) -> None:
    def broken(_pair: str, _today: date) -> list[DailyBar]:
        raise KrakenError("Kraken OHLC XBTUSD: EService:Unavailable")

    outcome = _run(session, paper, DAYS[START], fetch=broken)
    assert (outcome.status, outcome.error) == (
        "failed", "KrakenError: Kraken OHLC XBTUSD: EService:Unavailable",
    )
    assert session.exec(select(PaperDay)).all() == []
    run = session.get(PaperRun, outcome.run_id)
    assert run is not None and run.finished_at is not None


def test_a_missing_candle_fails_the_run(session: Session, paper: Path) -> None:
    series = all_bars()
    series["ETHUSD"] = series["ETHUSD"][: START + 1]  # ETH stops at the start day
    outcome = _run(session, paper, DAYS[START + 1], fetch=fake_fetch(series))
    assert outcome.status == "failed"
    assert outcome.error is not None and "do not end on" in outcome.error


def test_telegram_down_still_saves_the_days(session: Session, paper: Path) -> None:
    outcome = _run(session, paper, DAYS[START], messenger=FakeMessenger(fail=True))
    assert (outcome.status, outcome.days_added) == ("ok", 1)
    run = session.get(PaperRun, outcome.run_id)
    assert run is not None
    assert (run.messages_failed, run.summary_sent) == (1, False)  # the week's summary


def test_a_held_lock_does_nothing(session: Session, paper: Path) -> None:
    assert _run(session, paper, DAYS[START], held=False) == PaperOutcome("locked")
    assert session.exec(select(PaperRun)).all() == []


def test_the_weekly_summary_is_sent_once_per_iso_week(session: Session, paper: Path) -> None:
    messenger = FakeMessenger()
    nights = DAYS[START : START + 15]
    for day in nights:
        _run(session, paper, day, messenger=messenger)
    summaries = [t for t in messenger.texts() if t.startswith(SUMMARY)]
    assert len(summaries) == len({night(day).isocalendar()[:2] for day in nights})


def test_crash_and_breakout_days_are_inside_the_fixture() -> None:
    assert START < BREAKOUT < CRASH < len(DAYS) - 1
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_paper_run.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'signalbench.paper.run'`.

- [ ] **Step 3: Write the module**

Create `src/signalbench/paper/run.py`:

```python
"""`crypto paper start` and the nightly `crypto paper run` (spec 11).

Each night: check the pre-registered rules, fetch Kraken's finished candles, replay the paper
period with the backtest's own step(), check that every saved day comes out exactly as saved,
append the new days in one transaction, and message the owner. Any failure saves no paper
rows, is recorded on the run row, and sends a PAPER ALERT.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import yaml
from sqlmodel import Session, col, select

from signalbench.backtest.crypto_runner import (
    CRYPTO_SYMBOLS,
    check_crypto_config,
    check_crypto_series,
    crypto_market,
    last_complete_day,
    load_crypto_bars,
)
from signalbench.db.models import PaperPortfolio, PaperRun
from signalbench.ingest.kraken import crypto_ticker, find_crypto_ticker, ingest_kraken_bars
from signalbench.ingest.prices import DailyBar
from signalbench.live.messenger import Messenger
from signalbench.market.calendar import HISTORY_START, DailySessions
from signalbench.paper.messages import alert_text, day_texts
from signalbench.paper.paper_file import PaperError, PaperFile, load_paper_file
from signalbench.paper.replay import ReplayDay, replay
from signalbench.paper.store import append_days, find_portfolio, first_difference, load_saved
from signalbench.paper.summary import paper_summary, summary_text
from signalbench.strategy.config import ConfigError, StrategyConfig, load_strategy_config

PAPER_LOCK = 2026_1011_11  # an arbitrary bigint that names the paper run's advisory lock
Fetch = Callable[[str, date], list[DailyBar]]  # (Kraken pair, today in UTC) -> finished candles


class ReplayMismatch(PaperError):
    """A saved day no longer comes out of the replay as saved."""


@dataclass(frozen=True)
class PaperRules:
    paper: PaperFile
    config: StrategyConfig  # only the paper file's setup fires
    sha: str  # the strategy file's SHA-256


@dataclass(frozen=True)
class PaperOutcome:
    status: str  # locked | ok | failed | mismatch
    run_id: int | None = None
    days_added: int = 0
    error: str | None = None


@dataclass(frozen=True)
class _Night:
    rules: PaperRules
    portfolio: PaperPortfolio
    new: list[ReplayDay]


def check_rules(repo: Path, paper_path: Path, committed: Callable[[Path], bool]) -> PaperRules:
    """The paper file and its strategy are committed and unchanged, and the strategy is the
    one the paper file names (its SHA-256)."""
    if not committed(paper_path):
        raise PaperError(
            f"{paper_path.name} must be committed, unchanged (spec 11 pre-registration)."
        )
    paper = load_paper_file(paper_path)
    strategy_path = repo / paper.strategy
    if not committed(strategy_path):
        raise PaperError(f"{paper.strategy} must be committed, unchanged.")
    try:
        config, sha = load_strategy_config(strategy_path)
    except (OSError, ConfigError, yaml.YAMLError) as error:
        raise PaperError(f"{paper.strategy}: {error}") from None
    if sha != paper.strategy_sha256:
        raise PaperError(
            f"{paper.strategy} has SHA-256 {sha}, not the {paper.strategy_sha256} in "
            f"{paper_path.name}: the strategy changed."
        )
    check_crypto_config(config)
    return PaperRules(paper, config.with_setups((paper.setup,)), sha)


def start_paper(
    session: Session,
    *,
    repo: Path,
    paper_path: Path,
    now: datetime,
    fetch: Fetch,
    committed: Callable[[Path], bool],
) -> PaperPortfolio:
    """Create the paper file's portfolio and load Kraken's recent daily candles. Refuses a
    second portfolio of the same name and a start day that has already closed."""
    rules = check_rules(repo, paper_path, committed)
    paper = rules.paper
    if find_portfolio(session, paper.name) is not None:
        raise PaperError(f"{paper.name} already exists; a new idea is a new paper file.")
    last = last_complete_day(now)
    if last >= paper.start:
        raise PaperError(
            f"{paper.start} has already closed (the last complete UTC day is {last}): "
            "pre-register a later start in a new paper file."
        )
    today = now.astimezone(UTC).date()
    for pair in CRYPTO_SYMBOLS:
        ingest_kraken_bars(session, crypto_ticker(session, pair), fetch(pair, today), HISTORY_START)
    portfolio = PaperPortfolio(
        name=paper.name, paper_file=paper_path.name, strategy_sha256=rules.sha,
        start=paper.start, created_at=now,
    )
    session.add(portfolio)
    session.commit()
    session.refresh(portfolio)
    return portfolio


def run_paper(
    session: Session,
    *,
    lock: AbstractContextManager[bool],
    repo: Path,
    paper_path: Path,
    clock: Callable[[], datetime],
    fetch: Fetch,
    committed: Callable[[Path], bool],
    git_sha: str,
    messenger: Messenger,
    echo: Callable[[str], None],
) -> PaperOutcome:
    """The nightly job. `lock` yields False when another run holds it: nothing is done.
    `clock` must return timezone-aware times."""
    with lock as held:
        if not held:
            echo("Another paper run holds the lock; nothing to do.")
            return PaperOutcome("locked")
        now = clock()
        run = PaperRun(started_at=now, status="running", git_sha=git_sha)
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = _id(run.id)
        try:
            night = _night(session, run_id, repo, paper_path, now, fetch, committed)
        except Exception as error:  # noqa: BLE001  # every failure is recorded and alerted
            session.rollback()
            status = "mismatch" if isinstance(error, ReplayMismatch) else "failed"
            text = str(error) if isinstance(error, PaperError) else f"{type(error).__name__}: {error}"
            sent = _send(messenger, alert_text(text))
            _finish(session, run_id, clock(), status=status, error=text, messages_failed=int(not sent))
            echo(f"ERROR: {text}")
            return PaperOutcome(status, run_id, error=text)
        config = night.rules.config
        texts = [
            text for day in night.new for text in day_texts(config.version, day, config.cost_per_side)
        ]
        failed = sum(not _send(messenger, text) for text in texts)
        summary_sent = False
        if not _summary_sent_this_week(session, now):
            summary = paper_summary(session, night.portfolio, night.rules.paper, config.start_equity, now)
            summary_sent = _send(messenger, summary_text(night.rules.paper, summary))
            failed += int(not summary_sent)
        _finish(
            session, run_id, clock(), status="ok", days_added=len(night.new),
            messages_failed=failed, summary_sent=summary_sent,
        )
        return PaperOutcome("ok", run_id, len(night.new))


def _night(
    session: Session,
    run_id: int,
    repo: Path,
    paper_path: Path,
    now: datetime,
    fetch: Fetch,
    committed: Callable[[Path], bool],
) -> _Night:
    rules = check_rules(repo, paper_path, committed)
    paper = rules.paper
    portfolio = find_portfolio(session, paper.name)
    if portfolio is None:
        raise PaperError(f"No portfolio {paper.name}: run `signalbench crypto paper start` first.")
    if portfolio.strategy_sha256 != rules.sha or portfolio.start != paper.start:
        raise PaperError(
            f"{paper_path.name} no longer matches the portfolio {paper.name} was started with."
        )
    today = now.astimezone(UTC).date()
    for pair in CRYPTO_SYMBOLS:
        ticker = find_crypto_ticker(session, pair)
        if ticker is None:
            raise PaperError(f"No {pair} ticker: run `signalbench crypto paper start` first.")
        ingest_kraken_bars(session, ticker, fetch(pair, today), HISTORY_START)
    last = last_complete_day(now)
    if last < paper.start:
        return _Night(rules, portfolio, [])
    bars = load_crypto_bars(session, last)
    check_crypto_series(bars, paper.start, last)
    market = crypto_market(bars, rules.config, DailySessions(), paper.start, last)
    days = replay(market, rules.config, paper.start, last)
    portfolio_id = _id(portfolio.id)
    saved = load_saved(session, portfolio_id)
    difference = first_difference(saved, days)
    if difference is not None:
        raise ReplayMismatch(f"replay mismatch on {difference}")
    new = days[len(saved):]
    append_days(session, portfolio_id=portfolio_id, run_id=run_id, days=new, now=now)
    session.commit()
    return _Night(rules, portfolio, new)


def _summary_sent_this_week(session: Session, now: datetime) -> bool:
    """A run since Monday 00:00 UTC already sent the weekly summary."""
    utc = now.astimezone(UTC)
    monday = datetime.combine(utc.date() - timedelta(days=utc.weekday()), time(0, 0), tzinfo=UTC)
    found = session.exec(
        select(PaperRun.id).where(
            col(PaperRun.summary_sent).is_(True), col(PaperRun.started_at) >= monday
        )
    ).first()
    return found is not None


def _send(messenger: Messenger, text: str) -> bool:
    try:
        messenger.send(text)
    except Exception:  # noqa: BLE001  # Telegram down: the days are saved; the run counts it
        return False
    return True


def _finish(
    session: Session,
    run_id: int,
    finished_at: datetime,
    *,
    status: str,
    error: str | None = None,
    days_added: int = 0,
    messages_failed: int = 0,
    summary_sent: bool = False,
) -> None:
    run = session.get(PaperRun, run_id)
    if run is None:
        raise RuntimeError(f"paper run {run_id} disappeared")
    run.finished_at = finished_at
    run.status = status
    run.error = error
    run.days_added = days_added
    run.messages_failed = messages_failed
    run.summary_sent = summary_sent
    session.add(run)
    session.commit()


def _id(value: int | None) -> int:
    if value is None:
        raise RuntimeError("a saved row has no id")
    return value
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_paper_run.py -q`
Expected: PASS. The night-by-night tests replay up to 50 nights each; if the file takes more than about 60 seconds, report the time in the task report rather than shortening the tests.

- [ ] **Step 5: Full check and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
git add src/signalbench/paper/run.py tests/test_paper_run.py
git commit -m "feat: crypto paper start and the nightly paper run (spec 11)"
```

---

### Task 9: CLI commands `crypto paper start|run|status|review`

**Files:**
- Modify: `src/signalbench/cli.py` (add after the `crypto backtest` command, at the end of the file)
- Create: `tests/test_paper_cli.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_paper_cli.py`:

```python
from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session
from typer.testing import CliRunner

from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import PaperPortfolio
from paper_helpers import DAYS, START, strategy_sha, write_paper_file

runner = CliRunner()


def _portfolio(session: Session, start: date) -> None:
    session.add(
        PaperPortfolio(
            name="crypto-test-paper", paper_file="paper_crypto-test.yaml",
            strategy_sha256=strategy_sha(), start=start,
        )
    )
    session.commit()


def test_paper_help_lists_its_commands() -> None:
    result = runner.invoke(app, ["crypto", "paper", "--help"])
    assert result.exit_code == 0
    for command in ("start", "run", "status", "review"):
        assert command in result.stdout


def test_status_needs_a_started_portfolio(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    paper = write_paper_file(tmp_path)
    result = runner.invoke(app, ["crypto", "paper", "status", "--file", str(paper)])
    assert result.exit_code == 1
    assert "run `signalbench crypto paper start` first" in result.stderr


def test_status_prints_the_summary(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    paper = write_paper_file(tmp_path)
    _portfolio(session, DAYS[START])
    result = runner.invoke(app, ["crypto", "paper", "status", "--file", str(paper)])
    assert result.exit_code == 0, result.stderr
    assert "PAPER crypto-test-paper: weekly summary" in result.stdout
    assert "Equity 100.00 (start 100.00)" in result.stdout
    assert "Last ok run: never" in result.stdout


def test_review_refuses_before_the_review_date(
    session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    paper = write_paper_file(tmp_path, start=date(2099, 1, 1))
    _portfolio(session, date(2099, 1, 1))
    result = runner.invoke(app, ["crypto", "paper", "review", "--file", str(paper)])
    assert result.exit_code == 1
    assert "The review is on 2100-01-01" in result.stderr


def test_run_needs_the_telegram_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli.settings, "telegram_bot_token", None)
    result = runner.invoke(
        app, ["crypto", "paper", "run", "--file", str(write_paper_file(tmp_path))]
    )
    assert result.exit_code == 1
    assert "TELEGRAM_BOT_TOKEN" in result.stderr


def test_a_malformed_paper_file_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "paper.yaml"
    bad.write_text("name: x\n", encoding="utf-8")
    result = runner.invoke(app, ["crypto", "paper", "status", "--file", str(bad)])
    assert result.exit_code == 1
    assert "needs exactly the keys" in result.stderr
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_paper_cli.py -q`
Expected: FAIL (`No such command 'paper'`).

- [ ] **Step 3: Add the commands**

In `src/signalbench/cli.py`, add these imports in their sorted places among the existing `from signalbench...` imports:

```python
from signalbench.db.models import PaperPortfolio  # add to the existing models import line
from signalbench.ingest.kraken import fetch_kraken_daily  # add to the existing kraken import if missing
from signalbench.paper.paper_file import PaperError, PaperFile, load_paper_file
from signalbench.paper.run import PAPER_LOCK, Fetch, run_paper, start_paper
from signalbench.paper.store import find_portfolio
from signalbench.paper.summary import paper_review, paper_summary, summary_text
```

Add next to `CRYPTO_V1_PATH`:

```python
PAPER_CRYPTO_V1_PATH = REPO_ROOT / "data" / "paper_crypto-v1.yaml"
```

Append at the end of the file:

```python
paper_app = typer.Typer(help="Forward paper trading on Kraken daily candles (spec 11). No money.")
crypto_app.add_typer(paper_app, name="paper")


def _kraken_fetch(client: httpx.Client) -> Fetch:
    return lambda pair, today: fetch_kraken_daily(client, pair, today)


def _committed(path: Path) -> bool:
    return committed_unchanged(REPO_ROOT, path)


def _paper(file: Path) -> tuple[PaperFile, float]:
    """The paper file and its strategy's start equity; exits 1 with the reason."""
    try:
        paper = load_paper_file(file)
        strategy, _sha = load_strategy_config(REPO_ROOT / paper.strategy)
    except (OSError, ValueError, yaml.YAMLError) as error:  # PaperError, ConfigError
        typer.echo(f"ERROR: {error}", err=True)
        raise typer.Exit(1) from None
    return paper, strategy.start_equity


def _paper_portfolio(session: Session, paper: PaperFile) -> PaperPortfolio:
    portfolio = find_portfolio(session, paper.name)
    if portfolio is None:
        typer.echo(
            f"No portfolio {paper.name}: run `signalbench crypto paper start` first.", err=True
        )
        raise typer.Exit(1)
    return portfolio


@paper_app.command("start")
def paper_start(
    file: Annotated[
        Path, typer.Option("--file", help="The pre-registered paper file.")
    ] = PAPER_CRYPTO_V1_PATH,
) -> None:
    """Create the paper portfolio and load Kraken's recent daily candles (once)."""
    try:
        with get_session() as session, httpx.Client(timeout=30.0) as client:
            portfolio = start_paper(
                session, repo=REPO_ROOT, paper_path=file, now=datetime.now(UTC),
                fetch=_kraken_fetch(client), committed=_committed,
            )
    except (ValueError, httpx.HTTPError) as error:  # PaperError, KrakenError, refused data
        typer.echo(f"ERROR: {error}", err=True)
        raise typer.Exit(1) from None
    typer.echo(
        f"{portfolio.name}: starts {portfolio.start}, strategy {portfolio.strategy_sha256[:12]}"
    )


@paper_app.command("run")
def paper_run(
    file: Annotated[
        Path, typer.Option("--file", help="The pre-registered paper file.")
    ] = PAPER_CRYPTO_V1_PATH,
) -> None:
    """The nightly job: fetch, replay, compare, append, and message (spec 11)."""
    token, chat = _telegram()
    messenger = TelegramMessenger(token, chat)
    try:
        with get_session() as session, httpx.Client(timeout=30.0) as client:
            outcome = run_paper(
                session, lock=advisory_lock(engine, PAPER_LOCK), repo=REPO_ROOT,
                paper_path=file, clock=_now, fetch=_kraken_fetch(client),
                committed=_committed, git_sha=git_sha(REPO_ROOT), messenger=messenger,
                echo=typer.echo,
            )
    except Exception as error:  # noqa: BLE001  # e.g. the database is down: no run row to mark
        typer.echo(f"ERROR: {type(error).__name__}: {redact(_first_line(error), token)}", err=True)
        raise typer.Exit(1) from None
    finally:
        messenger.close()
    if outcome.status in ("failed", "mismatch"):
        raise typer.Exit(1)  # run_paper printed the ERROR line
    if outcome.status == "ok":
        typer.echo(f"paper run {outcome.run_id}: ok, {outcome.days_added} days added")


@paper_app.command("status")
def paper_status(
    file: Annotated[
        Path, typer.Option("--file", help="The pre-registered paper file.")
    ] = PAPER_CRYPTO_V1_PATH,
) -> None:
    """The weekly summary, now, and the last successful run."""
    paper, start_equity = _paper(file)
    with get_session() as session:
        portfolio = _paper_portfolio(session, paper)
        summary = paper_summary(session, portfolio, paper, start_equity, datetime.now(UTC))
    typer.echo(summary_text(paper, summary))
    last = "never" if summary.last_ok_run is None else summary.last_ok_run.isoformat()
    typer.echo(f"Last ok run: {last}")


@paper_app.command("review")
def paper_review_command(
    file: Annotated[
        Path, typer.Option("--file", help="The pre-registered paper file.")
    ] = PAPER_CRYPTO_V1_PATH,
) -> None:
    """The 12-month review: on track or off track (refused before the review date)."""
    paper, start_equity = _paper(file)
    with get_session() as session:
        portfolio = _paper_portfolio(session, paper)
        try:
            review = paper_review(session, portfolio, paper, start_equity, datetime.now(UTC).date())
        except PaperError as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(1) from None
    for line in review.lines:
        typer.echo(line)
```

`_telegram`, `_now`, `_first_line`, `redact`, `TelegramMessenger`, `advisory_lock`, `engine`, `git_sha`, `committed_unchanged`, `load_strategy_config`, `get_session`, and `Session` already exist in `cli.py`; do not redefine them.

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_paper_cli.py tests/test_crypto_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Full check and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
git add src/signalbench/cli.py tests/test_paper_cli.py
git commit -m "feat: signalbench crypto paper start, run, status, and review (spec 11)"
```

---

### Task 10: botbox units, backup script, and docs

**Files:**
- Create: `scripts/botbox/signalbench-paper.service`
- Create: `scripts/botbox/signalbench-paper.timer`
- Create: `scripts/botbox/signalbench-paper-backup.service`
- Create: `scripts/botbox/signalbench-paper-backup.timer`
- Create: `scripts/botbox/paper_backup.sh`
- Create: `scripts/botbox/paper.env.example`
- Create: `scripts/botbox/README.md`
- Modify: `README.md` (crypto CLI table and roadmap)
- Create: `tests/test_botbox_units.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_botbox_units.py`:

```python
from pathlib import Path

BOTBOX = Path(__file__).resolve().parents[1] / "scripts" / "botbox"


def test_the_paper_timer_runs_three_times_a_day_in_utc() -> None:
    text = (BOTBOX / "signalbench-paper.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 00,06,12:30:00 UTC" in text
    assert "Persistent=true" in text


def test_the_paper_service_runs_from_the_paper_checkout() -> None:
    text = (BOTBOX / "signalbench-paper.service").read_text(encoding="utf-8")
    assert "WorkingDirectory=%h/paper/macrocite" in text
    assert "EnvironmentFile=%h/paper/config/paper.env" in text
    assert "ExecStart=%h/.local/bin/uv run --frozen signalbench crypto paper run" in text


def test_the_backup_dumps_only_the_paper_database() -> None:
    text = (BOTBOX / "paper_backup.sh").read_text(encoding="utf-8")
    assert "-d signalbench_paper" in text
    assert "-mtime +14 -delete" in text


def test_the_env_example_points_at_the_paper_database() -> None:
    text = (BOTBOX / "paper.env.example").read_text(encoding="utf-8")
    assert "/signalbench_paper" in text
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run pytest tests/test_botbox_units.py -q`
Expected: FAIL with `FileNotFoundError`.

- [ ] **Step 3: Write the files**

`scripts/botbox/signalbench-paper.service`:

```ini
[Unit]
Description=SignalBench crypto paper run (spec 11)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=%h/paper/macrocite
EnvironmentFile=%h/paper/config/paper.env
ExecStart=%h/.local/bin/uv run --frozen signalbench crypto paper run
```

`scripts/botbox/signalbench-paper.timer`:

```ini
[Unit]
Description=SignalBench crypto paper run at 00:30, 06:30, 12:30 UTC (spec 11)

[Timer]
OnCalendar=*-*-* 00,06,12:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`scripts/botbox/signalbench-paper-backup.service`:

```ini
[Unit]
Description=SignalBench paper database backup (spec 11)

[Service]
Type=oneshot
ExecStart=%h/paper/macrocite/scripts/botbox/paper_backup.sh
```

`scripts/botbox/signalbench-paper-backup.timer`:

```ini
[Unit]
Description=SignalBench paper database backup at 01:15 UTC (spec 11)

[Timer]
OnCalendar=*-*-* 01:15:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`scripts/botbox/paper_backup.sh`:

```bash
#!/usr/bin/env bash
# Daily dump of the paper database (spec 11), 14 days kept. The live backup is separate.
set -euo pipefail
dir="$HOME/paper/backups"
mkdir -p "$dir"
docker exec signalbench-postgres-1 pg_dump -U signalbench -d signalbench_paper -Fc \
  > "$dir/signalbench_paper-$(date -u +%Y%m%d).dump"
find "$dir" -name 'signalbench_paper-*.dump' -mtime +14 -delete
```

Make it executable in git: `git add scripts/botbox/paper_backup.sh && git update-index --chmod=+x scripts/botbox/paper_backup.sh`.

`scripts/botbox/paper.env.example`:

```bash
# Copy to ~/paper/config/paper.env on botbox (chmod 600). Never commit the real file.
# The password is the live database's (same Postgres container, separate database).
DATABASE_URL=postgresql+psycopg://signalbench:CHANGE_ME@127.0.0.1:5433/signalbench_paper
TELEGRAM_BOT_TOKEN=CHANGE_ME
TELEGRAM_CHAT_ID=CHANGE_ME
```

`scripts/botbox/README.md`:

````markdown
# Paper trading on botbox (spec 11)

The paper job runs from its own checkout and database. The live bot (`~/macrocite` on
`live-v2`, database `signalbench`) is not touched.

## Set up (once)

```bash
git clone https://github.com/samintisar/macrocite.git ~/paper/macrocite
git -C ~/paper/macrocite checkout paper-crypto-v1
cd ~/paper/macrocite && ~/.local/bin/uv sync --frozen
docker exec signalbench-postgres-1 psql -U signalbench -d signalbench -c 'CREATE DATABASE signalbench_paper'
mkdir -p ~/paper/config && cp scripts/botbox/paper.env.example ~/paper/config/paper.env
chmod 600 ~/paper/config/paper.env   # then fill in the password, token, and chat id
set -a && . ~/paper/config/paper.env && set +a
~/.local/bin/uv run --frozen alembic upgrade head
~/.local/bin/uv run --frozen signalbench crypto paper start
mkdir -p ~/.config/systemd/user
cp scripts/botbox/signalbench-paper*.service scripts/botbox/signalbench-paper*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now signalbench-paper.timer signalbench-paper-backup.timer
```

## Check

```bash
systemctl --user list-timers 'signalbench-paper*'
journalctl --user -u signalbench-paper.service -n 50
cd ~/paper/macrocite && set -a && . ~/paper/config/paper.env && set +a && ~/.local/bin/uv run --frozen signalbench crypto paper status
```

## Update the code

Merge on `main`, tag `paper-crypto-v<n>` or move to a later commit, then
`git -C ~/paper/macrocite fetch --tags && git -C ~/paper/macrocite checkout <tag> && uv sync --frozen`.
The next run's replay check proves the saved history still comes out the same.
````

In `README.md`, add three rows after the `crypto backtest` row (line ~192) of the crypto CLI table:

```markdown
| `uv run signalbench crypto paper start [--file PATH]` | Create the spec 11 paper portfolio from `data/paper_crypto-v1.yaml` and load Kraken's recent candles (once, before the start day closes) |
| `uv run signalbench crypto paper run [--file PATH]` | The nightly paper job: fetch, replay, compare with the saved days, append, and send PAPER messages |
| `uv run signalbench crypto paper status` / `review` | The weekly summary now; the 12-month review (refused before the review date) |
```

and after the roadmap line `- 09: crypto daily trend, ...`, add:

```markdown
- 11: crypto paper trading, crypto-v1 forward on botbox (no money; see the spec)
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run pytest tests/test_botbox_units.py -q`
Expected: PASS.

- [ ] **Step 5: Full check and commit**

```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
git add scripts/botbox README.md tests/test_botbox_units.py
git commit -m "feat: botbox units, backup, and runbook for paper trading (spec 11)"
```

---

### Task 11: Pre-register, deploy, and start (controller only, after merge; each outward step needs the owner's yes)

Not for a subagent. Run after the branch is reviewed and merged to `main`.

- [ ] **Step 1: Pick the start day.** The first full UTC day after setup will be done (normally tomorrow in UTC). It must not have closed when `crypto paper start` runs.
- [ ] **Step 2: Write `data/paper_crypto-v1.yaml`** with `name: crypto-v1-paper`, `strategy: data/strategy_crypto-v1.yaml`, `setup: breakout`, the start day, the review block from the spec, and `strategy_sha256` from:

```bash
uv run python -c "from pathlib import Path; from signalbench.strategy.config import config_sha256; print(config_sha256(Path('data/strategy_crypto-v1.yaml').read_bytes()))"
```

  Add a header comment like the strategy file's (pre-registered, do not edit). Commit it alone: `data: pre-register paper_crypto-v1 (spec 11)`.
- [ ] **Step 3: Tag and push (ask first).** `git tag paper-crypto-v1` on that commit; push `main` and the tag.
- [ ] **Step 4: Set up botbox (ask first)** by following `scripts/botbox/README.md`, reading the live database password from the live checkout's config without printing it.
- [ ] **Step 5:** Confirm `systemctl --user list-timers` shows both paper timers, and that the live units (`signalbench-bot.service`, `signalbench-scan.timer`, `signalbench-backup.timer`) are unchanged.
- [ ] **Step 6:** After the first 00:30 UTC run: check the journal, `crypto paper status`, and that the PAPER weekly summary reached Telegram.
- [ ] **Step 7:** Log the start in `docs/research-log.md` (start day, review date, tag) and update the spec 11 changelog; commit and push (ask first).
