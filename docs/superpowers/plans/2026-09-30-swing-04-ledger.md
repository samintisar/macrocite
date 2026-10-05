# Swing Assistant 04 — Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record what the owner actually did on Wealthsimple (fills, cash movements, splits) and derive everything else from it: positions (managed and manual), cash, equity, peak, the pause, each managed position's US and CDR stop, trade R on planned risk, the CRA-style ACB report with superficial losses, and the scale-up check. Freeze the one live config, `data/strategy_v2-none-cash.yaml`, with `signalbench live start`. Out of scope (spec 05): the evening scan, Telegram, and scheduling; this plan gives the scan the ledger methods it calls, with tests.

**Architecture:** A new `signalbench.live` package. `acb.py` is the pure average-cost engine (Decimal in and out, superficial losses, CDR splits). The `Ledger` class is built in three layers over one SQLModel session: `book.py` (`LedgerBook`: cash, fills, voids, signals, exit alerts, positions over time), `levels.py` (`LedgerLevels`: CDR marks, US-equivalent levels, the stop rows, splits and the scale check), and `ledger.py` (`Ledger`: open positions, equity, the pause, the `PortfolioState` for `decide()`, the tax report, and the scale-up check). `review.py` runs `decide()` for one session on the ledger's portfolio and returns the exits and trailing-stop raises the scan writes. `tax.py` renders the report and CSV, `scaleup.py` holds the four checks, and `start.py` is `live start` and the scan's config check. Migration `0013_live_ledger` adds nine tables. The CLI gains `live start`, `ledger tax`, `ledger split`, and `ledger void-action`.

**Tech Stack:** Python 3.11+, SQLModel (in-memory SQLite in tests), Alembic, PyYAML, Typer, pytest, ruff 0.16, mypy strict. `decimal.Decimal` for all money. No new dependencies.

**Spec:** [`docs/superpowers/specs/2026-09-22-swing-assistant-04-ledger-design.md`](../specs/2026-09-22-swing-assistant-04-ledger-design.md) (revised 2026-09-29 for v2-none-cash). **Next:** [spec 05](../specs/2026-09-22-swing-assistant-05-bot-scan-design.md), which calls this plan's interface. **Depends on:** branch `feat/swing-08-live-v2`, on `feat/swing-07-paper-trading` (split detection, `MARK_TOLERANCE`, the Finnhub-calendar earnings dates, `code_version`), `feat/swing-06-breakout-v2`, and `feat/swing-03-jev-reader`; none is merged to main.

---

## Conventions for every task

- Run commands from the repo root with the Bash tool (Git Bash syntax). Use `uv run …` for every Python tool. Stay on branch `feat/swing-08-live-v2`; never push without asking the owner.
- **Never edit `data/strategy_*.yaml`** (the live config's sha256 is what `live start` freezes) **or any `.env*` file.** Tests read `data/strategy_v2-none-cash.yaml` and `data/cdr_spread_survey.yaml` and copy them into throwaway repos; they never write them. Stage explicit paths only; never `git add -A` or `git add .`.
- **`README.md` has an uncommitted change that belongs to the owner, and `data/paper_v1.yaml` is untracked.** Do not edit, stage, stash, or check out either.
- **No real database before Task 14.** Tasks 1–13 use only synthetic data, the in-memory SQLite `session` fixture from `tests/conftest.py`, and throwaway git repos under pytest's `tmp_path`. Do not run the migration, `signalbench live …`, `ledger …`, `paper …`, `ingest …`, or `backtest …` against the real database until the controller's go-ahead below.
- mypy runs strict on `src/` only. Test helpers are imported as `from live_helpers import …` (plus `strategy_helpers` and `paper_helpers`, unchanged).
- ruff 0.16 sorts imports (`I001`, and it wraps an import line longer than 88 characters) and flags `Decimal("2")` (`FURB157`: write `Decimal(2)`). Run `uv run ruff check --fix .` only **after** the module a test imports exists: before that, ruff files the missing `signalbench.*` name as third-party and moves the import. Every block below is already in ruff's order.
- "Append to `…py`" adds the block after the file's last line: a block that starts indented (methods of the file's last class) goes after one blank line, anything else after two.
- After each task: `uv run pytest -q`, `uv run ruff check .`, and `uv run mypy src` all pass before committing.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Every code block in this plan was replayed, task by task, in a scratch clone of this branch (with the untracked `data/paper_v1.yaml` copied in) before the plan was committed: each "Expected" failure and pass count below is from that replay. The suite starts at 605 passed and ends at 704 passed after Task 13, with ruff and mypy clean. If a number disagrees, look for a transcription slip before touching a test.

## The ledger, as built

| Rule | Behaviour |
| --- | --- |
| Cash | Σ cash movements + Σ sell proceeds − Σ buy costs − Σ fees over non-voided fills, through a date. Splits never move cash |
| Replay order | By date; on a date, a CDR split (its ex-date) before the fills, which are already post-split; fills in the order recorded (id) |
| ACB | CRA average cost per CDR symbol (`acb.py`); a CDR split multiplies units and keeps the total ACB; the superficial part of a loss (30 days either side, same CDR symbol, units on the sale's scale) is added to the ACB held, and shown provisional until day 30 has passed |
| Positions | A position (episode) runs from a buy that takes units above 0 to the sale back to 0. Managed when that opening buy is linked to a signal (id = the signal id), else manual (id `manual-<fill id>`) |
| Fill rules | Quantity > 0 with ≤ 6 decimals; price > 0 and fee ≥ 0 with ≤ 4; trade date ≤ today and not before the first cash movement; no sale beyond the units held (post-split); no buy beyond the running cash unless `force=True` (flagged `forced` and logged); a signal-linked buy opens or adds to its own signal's position only |
| Voids | A fill or a split is voided with a reason, never deleted; a void that would leave a later sale oversold is refused |
| Levels | US prices of a signal ÷ the recorded US splits after its as_of; CDR prices ÷ the recorded CDR splits. `entry_us = us_signal_close × fill / cdr_signal_close` on today's scale |
| Stops | Replayed from `stop_updates` in the order written: a `trail` row sets the stop from the next session; a `split` row rescales it by its split's ratio (or undoes a voided one). The CDR display stop of a raise is `new_us_stop ×` the last traded CDR/US close ratio |
| Marks | The latest US close × (CDR close ÷ US close) on the last date the CDR traded; the latest CDR close if it never traded; the ACB if no CDR price is stored |
| Equity and pause | Equity = cash + Σ units × mark, written nightly (a rerun replaces the night's row). Peak = max equity since `peak_reset_on`. Equity < 0.85 × peak pauses; only `resume()` clears it and resets the peak from today |
| decide() | Every open position (manual ones with stop 0) and the `sent` signals whose entry session has not closed yet (holding slots at `suggested_units × cdr_signal_close`); Breakout only, `NullReadingsView`; decisions for manual positions, positions with a `sent` exit alert, and held symbols are discarded |
| Tax report | The year's dispositions (cents, rounded half up), superficial losses, provisional flags, and CDR splits with the ACB per unit before and after, plus a CSV |
| Scale-up | Active at 10 closed managed positions; take rate ≥ 0.80, mean slippage ≤ 0.5%, no exit-alert miss (1 session; a late alert from its send date), no buy above 1.01 × the signal close. Each result appended to `risk_state.scale_up_history` |

## Decisions this plan makes where the spec is silent

Two depart from the spec's letter: three added columns (`fills.forced`, `stop_updates.corporate_action_id`, `risk_state.scale_up_history`) and integer ids in the interface. Two are marked **Confirm with the owner**; neither blocks Tasks 1–13, but both should be read before spec 05 starts.

- **Integer ids (interface change).** Every new table has an integer primary key, and `record_fill`, `void_fill`, `void_corporate_action`, and friends take `int` where the spec's interface shows `UUID`: the owner types `/void 12`, and Telegram button data is capped at 64 bytes. Record order within a trade date is the id order. `live_config` and `risk_state` have a fixed id of 1, so a second row cannot be inserted.
- **Three added columns.** `fills.forced` records a buy made with `force=True` (the spec's "an override is logged"; it is also a `logging` warning). `stop_updates.corporate_action_id` links a `split` row to its split, so voiding a split finds the rows to undo. `risk_state.scale_up_history` (JSON list) is the spec's "stored in risk_state history".
- **Enums as strings.** Statuses, reasons, kinds, and sources are `str` columns validated by the ledger (`Literal` types in `book.py`), like the paper tables; no Postgres enum types to migrate. The two "unique among" rules are partial unique indexes (`WHERE reason = 'trail'`; `WHERE kind = '…' AND NOT voided`), one per split kind because the symbol lives in two columns.
- **Precision.** Quantities are stored at 6 decimals and prices, fees, stops, and `stop_pct`-derived CDR stops at 4 (`stop_pct` itself at 8); a value with more decimals is refused, not rounded. Deposits are cents. Equity snapshot values are rounded half up to 4 decimals before the peak and pause use them; the tax report rounds to the cent and its totals add the rounded lines. Nothing else is rounded.
- **The superficial loss's timing.** The denied amount is added to the ACB right after the sale; when the sale leaves no units, it waits in the ACB (units 0) for the re-buy. The fixture shows both the spec's five-fill example (no denial: nothing is held on day 30) and a variant with a re-buy after the sale (half denied).
- **`record_split` writes the stop rows.** Recording a split also writes the `split` stop row for each open managed position it touches and, for a CDR split, withdraws the CDR's `sent` signals from before the ex-date, in one transaction, so a crash cannot leave a split without its rows. The scan calls `record_split` and reads the rows back for its ℹ️ messages. Voiding writes the undoing rows; a withdrawn signal stays withdrawn. A split recorded while a signal was only `sent` has no row; the signal's initial stop is read ÷ that split once its position opens.
- **The stop in force for a session.** `stop_in_force(signal, D)` replays the rows in the order written and leaves out raises decided at D's close or later, so catch-up and a forced rescan of D both see the stop in force during D, and a split recorded afterwards still applies. `current_stop()` is the same with no cut-off. `record_stop_update` takes no `reason` (only `trail` rows come through it) and computes the CDR display stop itself; it refuses a raise that is not above the stop in force, a second raise for a session, a closed or not-yet-open position, and a position with a `sent` exit alert.
- **`record_signal` derives `stop_pct` and `cdr_stop`** from the US close, US stop, and CDR close, so the planned risk in CAD and in US terms always agree (`signal_stop()` is the helper spec 05's sizing reuses).
- **Pending signals** hold their slot at the close of their entry session too: at that close the owner may have bought and not reported it yet, as the backtest's entry would already be a position. After it, they no longer count (the scan marks them `expired`).
- **Linking fills.** A buy may link to a `sent` or `expired` signal (reported after the scan expired it; either becomes `taken`), not to a skipped or withdrawn one. A signal opens at most one position. A sale linked to an exit alert marks it `done`. A void changes no signal or alert status.
- **The no-margin rule** is checked on the running cash from the buy's place in the timeline on (cash movements before the fills of their date), so a later withdrawal the buy would overdraw also refuses it. Voiding a buy that a later sale needs is refused with "record the corrected fill first"; the corrected buy may need `force`, since the wrong one still holds cash until it is voided.
- **Marks.** The last traded ratio needs a US bar on the CDR's trading date. A CDR that never traded is marked at its latest close, and its display ratio is that close over the US close on or before its date. A held CDR with no stored price is valued at its ACB.
- **Entry session and sessions held** use the NYSE calendar: the first session on or after the opening buy's trade date (a Saturday buy starts on Monday).
- **The scale-up windows.** An alert's deadline is the first NYSE session after its session (a late alert: after the New York date it was sent); a sale of that CDR after the alert's session and on or before the deadline matches it. An alert still inside its window is not judged yet. `scale_up_check()` only reads; `record_scale_up()` appends the result.
- **`live start`** writes `started_on` as the New York date it runs, also creates the unpaused `risk_state` row, and, like `paper start`, refuses uncommitted tracked code so `start_git_sha` names the code that ran. `verify_live_config()` is spec 05's step-1 check.
- **`ledger split SYMBOL RATIO EX_DATE`** takes a CDR symbol (a CDR split) or a US symbol (a US split); a split ratio of 1 is refused.
- **Confirm with the owner: a withdrawal looks like a drawdown.** The spec defines the peak on equity alone, so a `/withdraw` of more than 15% of equity pauses new entries, and a deposit raises the peak. Built as specified; spec 05's pause review should say when a withdrawal caused it.
- **Confirm with the owner: CDR fills on the scale of the signal.** `entry_us` and trade R use the opening buy's price and `cdr_signal_close` as stored, on the assumption that the buy is on the signal's CDR scale. A CDR split between the signal and the buy withdraws the signal first, so a linked buy cannot cross one.

## File map

| Path | Action | Responsibility |
| --- | --- | --- |
| `src/signalbench/db/models.py` | Modify | `LiveConfig`, `CashMovement`, `TradeSignal`, `ExitAlert`, `CorporateAction`, `StopUpdateRow`, `Fill`, `EquitySnapshot`, `LiveRiskState` |
| `alembic/versions/0013_live_ledger.py` | Create | The nine tables and their indexes |
| `src/signalbench/live/__init__.py`, `acb.py` | Create | The ACB engine |
| `src/signalbench/live/book.py` | Create (Tasks 3–4) | `LedgerBook`: cash, fills, voids, signals, exit alerts, positions over time, trade R |
| `src/signalbench/live/levels.py` | Create (Tasks 5–6) | `LedgerLevels`: marks, US-equivalent levels, stops, splits, the scale check |
| `src/signalbench/live/ledger.py` | Create (Task 3), replace (Task 7), modify (Tasks 9–10) | `Ledger`: positions, equity, the pause, `portfolio_state`, `tax_report`, `scale_up_check` |
| `src/signalbench/live/review.py` | Create | `live_market`, `review_session` for the scan |
| `src/signalbench/live/tax.py`, `scaleup.py`, `start.py` | Create | The tax report and CSV; the scale-up checks; `live start` and the config check |
| `src/signalbench/cli.py` | Modify | `live start`, `ledger tax`, `ledger split`, `ledger void-action` |
| `tests/fixtures/live_ledger_examples.yaml` | Create | The hand-checked ACB, CDR-split, fractional, and rebuild examples |
| `tests/live_helpers.py`, `tests/test_live_*.py` (11 files) | Create | Helpers and tests |
| `tests/test_migrations.py` | Modify | The new head and a 0013 check |
| `docs/superpowers/specs/2026-09-22-swing-assistant-04-ledger-design.md` | Modify | Implementation choices |

---

### Task 1: The nine ledger tables and migration `0013_live_ledger`

**Files:**
- Modify: `src/signalbench/db/models.py`, `tests/test_migrations.py`
- Create: `alembic/versions/0013_live_ledger.py`, `tests/test_live_schema.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_schema.py`:

```python
"""Spec 04 tables: the models, and migration 0013 that creates the same columns and indexes."""

import importlib.util
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from signalbench.db.models import (
    CorporateAction,
    EquitySnapshot,
    ExitAlert,
    Fill,
    LiveConfig,
    LiveRiskState,
    StopUpdateRow,
    Ticker,
    TickerKind,
    TradeSignal,
)

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0013_live_ledger.py"
TABLES = (
    "live_config", "cash_movements", "trade_signals", "exit_alerts", "corporate_actions",
    "stop_updates", "fills", "equity_snapshots", "risk_state",
)
PARTIAL_UNIQUE = {
    "corporate_actions": {
        "uq_corporate_actions_us_split": ["us_symbol", "ex_date"],
        "uq_corporate_actions_cdr_split": ["cdr_ticker_id", "ex_date"],
    },
    "stop_updates": {"uq_stop_updates_trail_signal_session": ["signal_id", "session"]},
}
NOW = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)


def _cdr(session: Session) -> Ticker:
    ticker = Ticker(symbol="ZNVD", company_name="NVIDIA", kind=TickerKind.cdr)
    session.add(ticker)
    session.commit()
    return ticker


def _signal(session: Session, cdr_id: uuid.UUID, as_of: date = date(2026, 10, 1)) -> TradeSignal:
    signal = TradeSignal(
        as_of=as_of, us_symbol="NVDA", cdr_ticker_id=cdr_id, us_signal_close=Decimal(200),
        us_stop=Decimal(188), stop_pct=Decimal("0.06"), cdr_signal_close=Decimal(40),
        cdr_stop=Decimal("37.6"), suggested_units=Decimal(1), order_type="limit",
        risk_amount_cad=Decimal("2.4"), explanation="why", expires_at=NOW,
    )
    session.add(signal)
    session.commit()
    return signal


def test_a_fill_keeps_its_decimals_and_defaults(session: Session) -> None:
    cdr = _cdr(session)
    session.add(
        Fill(cdr_ticker_id=cdr.id, side="buy", quantity=Decimal("0.123456"),
             price_cad=Decimal("38.5125"), trade_date=date(2026, 10, 2))
    )
    session.commit()
    session.expire_all()
    [fill] = session.exec(select(Fill)).all()
    assert (fill.quantity, fill.price_cad, fill.fee_cad) == (
        Decimal("0.123456"), Decimal("38.5125"), Decimal(0)
    )
    assert (fill.voided, fill.forced, fill.signal_id, fill.exit_alert_id) == (False, False, None, None)


def test_one_signal_per_session_symbol_and_setup(session: Session) -> None:
    cdr = _cdr(session)
    _signal(session, cdr.id)
    with pytest.raises(IntegrityError):
        _signal(session, cdr.id)


def test_one_trail_row_per_signal_and_session_but_any_number_of_split_rows(
    session: Session,
) -> None:
    cdr = _cdr(session)
    signal = _signal(session, cdr.id)
    assert signal.id is not None

    def row(reason: str) -> StopUpdateRow:
        return StopUpdateRow(
            signal_id=signal.id, session=date(2026, 10, 5), reason=reason,
            old_us_stop=Decimal(188), new_us_stop=Decimal(190),
            old_cdr_stop=Decimal("37.6"), new_cdr_stop=Decimal(38),
        )

    session.add_all([row("split"), row("split"), row("trail")])
    session.commit()
    session.add(row("trail"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_one_live_split_per_symbol_and_ex_date_but_voided_rows_do_not_count(
    session: Session,
) -> None:
    cdr = _cdr(session)
    ex = date(2026, 10, 5)

    def split(voided: bool = False, **symbol: object) -> CorporateAction:
        return CorporateAction(ex_date=ex, ratio=Decimal(2), source="owner", voided=voided,
                               **symbol)

    session.add_all([
        split(kind="us_split", us_symbol="NVDA", voided=True),
        split(kind="us_split", us_symbol="NVDA"),
        split(kind="cdr_split", cdr_ticker_id=cdr.id),
    ])
    session.commit()
    session.add(split(kind="us_split", us_symbol="NVDA"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.add(split(kind="cdr_split", cdr_ticker_id=cdr.id))
    with pytest.raises(IntegrityError):
        session.commit()


def test_single_row_tables_refuse_a_second_row(session: Session) -> None:
    session.add(LiveConfig(config_path="data/strategy_v2-none-cash.yaml", config_sha256="a" * 64,
                           started_on=date(2026, 10, 1), start_git_sha="b" * 40))
    session.add(LiveRiskState())
    session.commit()
    session.add(LiveConfig(config_path="x", config_sha256="c" * 64, started_on=date(2026, 10, 1),
                           start_git_sha="d" * 40))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    [risk] = session.exec(select(LiveRiskState)).all()
    assert (risk.id, risk.paused, risk.peak_reset_on, risk.scale_up_history) == (1, False, None, [])


def test_exit_alerts_and_snapshots_store_their_values(session: Session) -> None:
    cdr = _cdr(session)
    signal = _signal(session, cdr.id)
    assert signal.id is not None
    session.add(ExitAlert(signal_id=signal.id, cdr_ticker_id=cdr.id, as_of=date(2026, 10, 9),
                          reason="stop"))
    session.add(EquitySnapshot(date=date(2026, 10, 9), cash=Decimal("61.6"),
                               positions_value=Decimal("38.4"), equity=Decimal(100),
                               peak=Decimal(100)))
    session.commit()
    session.expire_all()
    [alert] = session.exec(select(ExitAlert)).all()
    [snapshot] = session.exec(select(EquitySnapshot)).all()
    assert (alert.status, alert.late, alert.telegram_message_id) == ("sent", False, None)
    assert (snapshot.cash, snapshot.equity) == (Decimal("61.6"), Decimal(100))


def _upgrade_and_downgrade() -> tuple[
    dict[str, list[tuple[str, bool]]], dict[str, dict[str, list[str]]], list[str]
]:
    spec = importlib.util.spec_from_file_location("migration_0013", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE tickers (id CHAR(32) PRIMARY KEY)"  # the one table 0013 points at
        )
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        inspector = inspect(connection)
        columns = {
            table: [(c["name"], bool(c["nullable"])) for c in inspector.get_columns(table)]
            for table in TABLES
        }
        unique = {
            table: {
                index["name"]: list(index["column_names"])
                for index in inspector.get_indexes(table)
                if index["unique"] and index["name"] is not None
            }
            for table in PARTIAL_UNIQUE
        }
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        left = inspect(connection).get_table_names()
    return columns, unique, left


def test_migration_0013_creates_the_model_columns_and_drops_them_again() -> None:
    created, unique, left = _upgrade_and_downgrade()
    for table in TABLES:
        model = SQLModel.metadata.tables[table]
        assert created[table] == [(c.name, bool(c.nullable)) for c in model.columns], table
    assert unique == PARTIAL_UNIQUE
    for table, indexes in PARTIAL_UNIQUE.items():
        model_indexes = {
            index.name: [c.name for c in index.columns]
            for index in SQLModel.metadata.tables[table].indexes
            if index.unique
        }
        assert model_indexes == indexes
    assert left == ["tickers"]
```

In `tests/test_migrations.py`, replace:

```python
    assert _script().get_heads() == ["0012_paper_trading"]
```

with:

```python
    assert _script().get_heads() == ["0013_live_ledger"]
```

Append to `tests/test_migrations.py`:

```python
def test_0013_creates_the_nine_ledger_tables() -> None:
    text = (VERSIONS / "0013_live_ledger.py").read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0012_paper_trading"' in text
    for table in (
        "live_config", "cash_movements", "trade_signals", "exit_alerts", "corporate_actions",
        "stop_updates", "fills", "equity_snapshots", "risk_state",
    ):
        assert f'op.create_table(\n        "{table}"' in text
    assert "postgresql_where=sa.text(\"reason = 'trail'\")" in text
    assert 'sa.Column("amount_cad", sa.Numeric(12, 2), nullable=False)' in text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_migrations.py -q`
Expected: 2 failed, 5 passed: the head is still `0012_paper_trading`, and `FileNotFoundError` for `0013_live_ledger.py`.

Run: `uv run pytest tests/test_live_schema.py -q`
Expected: collection error, `ImportError: cannot import name 'CorporateAction' from 'signalbench.db.models'`.

- [ ] **Step 3: Add the models**

In `src/signalbench/db/models.py`, replace:

```python
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Numeric,
    Text,
    UniqueConstraint,
)
```

with:

```python
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
```

Append to `src/signalbench/db/models.py`:

```python
def _money(precision: int = 12, scale: int = 4) -> Any:
    """A NOT NULL Numeric column for a Decimal field (spec 04: money is CAD, stored as Numeric)."""
    return Field(sa_column=Column(Numeric(precision, scale), nullable=False))


def _telegram_id() -> Any:
    """Telegram message ids are 64-bit integers; None until the message is sent (spec 05)."""
    return Field(default=None, sa_column=Column(BigInteger, nullable=True))


def _created_at() -> Any:
    return Field(default_factory=utcnow, sa_column=Column(UTCDateTime(), nullable=False))


class LiveConfig(SQLModel, table=True):
    """The one live strategy config (spec 04), frozen by `signalbench live start`. One row."""

    __tablename__ = "live_config"

    id: int = Field(default=1, primary_key=True, sa_column_kwargs={"autoincrement": False})
    config_path: str  # repo-relative: data/strategy_v2-none-cash.yaml
    config_sha256: str  # the scan refuses a config file whose sha256 differs (spec 05)
    started_on: date  # the New York date `live start` ran
    start_git_sha: str  # HEAD of the code that ran `live start`
    created_at: datetime = _created_at()


class CashMovement(SQLModel, table=True):
    """A deposit (+) or withdrawal (-) in CAD (spec 04). Never edited."""

    __tablename__ = "cash_movements"

    id: int | None = Field(default=None, primary_key=True)
    amount_cad: Decimal = _money(12, 2)
    occurred_on: date
    note: str = Field(sa_column=Column(Text, nullable=False))


class TradeSignal(SQLModel, table=True):
    """One entry signal sent to the owner (spec 04, written by the evening scan of spec 05)."""

    __tablename__ = "trade_signals"
    __table_args__ = (
        UniqueConstraint("as_of", "us_symbol", "setup", name="uq_trade_signals_as_of_symbol_setup"),
    )

    id: int | None = Field(default=None, primary_key=True)
    as_of: date  # the signal session
    us_symbol: str
    cdr_ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    setup: str = "breakout"  # always breakout live; the column stays for clarity
    us_signal_close: Decimal = _money()  # USD, on the scale of `as_of`
    us_stop: Decimal = _money()
    stop_pct: Decimal = _money(12, 8)  # (us_signal_close - us_stop) / us_signal_close
    cdr_signal_close: Decimal = _money()  # CAD
    cdr_stop: Decimal = _money()  # cdr_signal_close x (1 - stop_pct)
    suggested_units: Decimal = _money(18, 6)
    order_type: str  # limit | market
    risk_amount_cad: Decimal = _money()
    explanation: str = Field(sa_column=Column(Text, nullable=False))  # the template Why line
    status: str = "sent"  # sent | taken | skipped | expired | withdrawn
    skip_reason: str | None = None  # disagree | no_time | price_moved | wide_spread | other
    telegram_message_id: int | None = _telegram_id()
    expires_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    created_at: datetime = _created_at()


class ExitAlert(SQLModel, table=True):
    """A stop or earnings exit for a managed position (spec 04). One `sent` at a time."""

    __tablename__ = "exit_alerts"

    id: int | None = Field(default=None, primary_key=True)
    signal_id: int = Field(foreign_key="trade_signals.id", ondelete="RESTRICT", index=True)
    cdr_ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    as_of: date  # the session whose close triggered it
    reason: str  # stop | earnings
    late: bool = False  # sent by a catch-up run
    status: str = "sent"  # sent | done | ignored
    telegram_message_id: int | None = _telegram_id()
    created_at: datetime = _created_at()


class CorporateAction(SQLModel, table=True):
    """A US or CDR stock split (spec 04). Append-only: a wrong row is voided, never edited."""

    __tablename__ = "corporate_actions"
    __table_args__ = (
        Index(
            "uq_corporate_actions_us_split", "us_symbol", "ex_date", unique=True,
            sqlite_where=text("kind = 'us_split' AND NOT voided"),
            postgresql_where=text("kind = 'us_split' AND NOT voided"),
        ),
        Index(
            "uq_corporate_actions_cdr_split", "cdr_ticker_id", "ex_date", unique=True,
            sqlite_where=text("kind = 'cdr_split' AND NOT voided"),
            postgresql_where=text("kind = 'cdr_split' AND NOT voided"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    kind: str  # us_split | cdr_split
    us_symbol: str | None = None  # us_split only
    cdr_ticker_id: uuid.UUID | None = Field(
        default=None, foreign_key="tickers.id", ondelete="RESTRICT"
    )  # cdr_split only
    ex_date: date  # fills dated on or after it are in post-split units
    ratio: Decimal = _money(12, 6)  # new units per old unit: 2 for a 2-for-1 split
    source: str  # yfinance | owner
    voided: bool = False
    void_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    telegram_message_id: int | None = _telegram_id()
    created_at: datetime = _created_at()


class StopUpdateRow(SQLModel, table=True):
    """A managed position's stop change (spec 04): a `trail` raise or a `split` rescale.

    Append-only. `corporate_action_id` links a `split` row to the split it applies or undoes
    (an addition to the spec's columns: voiding a split finds the rows to undo through it).
    """

    __tablename__ = "stop_updates"
    __table_args__ = (
        Index(
            "uq_stop_updates_trail_signal_session", "signal_id", "session", unique=True,
            sqlite_where=text("reason = 'trail'"),
            postgresql_where=text("reason = 'trail'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    signal_id: int = Field(foreign_key="trade_signals.id", ondelete="RESTRICT", index=True)
    session: date  # trail: the session whose close raised it; split: the ex-date
    reason: str  # trail | split
    old_us_stop: Decimal = _money()
    new_us_stop: Decimal = _money()
    old_cdr_stop: Decimal = _money()
    new_cdr_stop: Decimal = _money()
    late: bool = False
    corporate_action_id: int | None = Field(
        default=None, foreign_key="corporate_actions.id", ondelete="RESTRICT"
    )
    telegram_message_id: int | None = _telegram_id()
    created_at: datetime = _created_at()


class Fill(SQLModel, table=True):
    """What the owner did on Wealthsimple (spec 04). Never deleted: a mistake is voided."""

    __tablename__ = "fills"

    id: int | None = Field(default=None, primary_key=True)
    cdr_ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT", index=True)
    side: str  # buy | sell
    quantity: Decimal = _money(18, 6)  # CDR units, post-split from the ex-date on
    price_cad: Decimal = _money()
    fee_cad: Decimal = Field(default=Decimal(0), sa_column=Column(Numeric(12, 4), nullable=False))
    trade_date: date
    signal_id: int | None = Field(default=None, foreign_key="trade_signals.id", ondelete="RESTRICT")
    exit_alert_id: int | None = Field(
        default=None, foreign_key="exit_alerts.id", ondelete="RESTRICT"
    )
    voided: bool = False
    void_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    forced: bool = False  # a buy beyond the cash on hand, recorded with force=True (logged)
    created_at: datetime = _created_at()


class EquitySnapshot(SQLModel, table=True):
    """Equity at one session's close (spec 04), written nightly by the scan."""

    __tablename__ = "equity_snapshots"
    __table_args__ = (PrimaryKeyConstraint("date"),)  # a `date` default would shadow the type

    date: date  # the session
    cash: Decimal = _money(14, 4)
    positions_value: Decimal = _money(14, 4)
    equity: Decimal = _money(14, 4)
    peak: Decimal = _money(14, 4)  # max equity since risk_state.peak_reset_on


class LiveRiskState(SQLModel, table=True):
    """The pause state (spec 04). One row. Only /resume clears a pause."""

    __tablename__ = "risk_state"

    id: int = Field(default=1, primary_key=True, sa_column_kwargs={"autoincrement": False})
    paused: bool = False
    paused_at: date | None = None  # the session whose close paused new entries
    paused_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    resumed_at: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime(), nullable=True)
    )
    peak_reset_on: date | None = None  # set by /resume: the peak counts from this date
    # Each scale-up check result and its inputs, oldest first (spec 04, "stored for the record")
    scale_up_history: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
```

- [ ] **Step 4: Add the migration**

Create `alembic/versions/0013_live_ledger.py`:

```python
"""Spec 04 live ledger

Revision ID: 0013_live_ledger
Revises: 0012_paper_trading
Create Date: 2026-09-30 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_live_ledger"
down_revision: str | None = "0012_paper_trading"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "live_config",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("config_path", sa.String(), nullable=False),
        sa.Column("config_sha256", sa.String(), nullable=False),
        sa.Column("started_on", sa.Date(), nullable=False),
        sa.Column("start_git_sha", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "cash_movements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("amount_cad", sa.Numeric(12, 2), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "trade_signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("us_symbol", sa.String(), nullable=False),
        sa.Column("cdr_ticker_id", sa.Uuid(), nullable=False),
        sa.Column("setup", sa.String(), nullable=False),
        sa.Column("us_signal_close", sa.Numeric(12, 4), nullable=False),
        sa.Column("us_stop", sa.Numeric(12, 4), nullable=False),
        sa.Column("stop_pct", sa.Numeric(12, 8), nullable=False),
        sa.Column("cdr_signal_close", sa.Numeric(12, 4), nullable=False),
        sa.Column("cdr_stop", sa.Numeric(12, 4), nullable=False),
        sa.Column("suggested_units", sa.Numeric(18, 6), nullable=False),
        sa.Column("order_type", sa.String(), nullable=False),
        sa.Column("risk_amount_cad", sa.Numeric(12, 4), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("skip_reason", sa.String(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cdr_ticker_id"], ["tickers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "as_of", "us_symbol", "setup", name="uq_trade_signals_as_of_symbol_setup"
        ),
    )
    op.create_table(
        "exit_alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("cdr_ticker_id", sa.Uuid(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("late", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["signal_id"], ["trade_signals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["cdr_ticker_id"], ["tickers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_exit_alerts_signal_id", "exit_alerts", ["signal_id"])
    op.create_table(
        "corporate_actions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("us_symbol", sa.String(), nullable=True),
        sa.Column("cdr_ticker_id", sa.Uuid(), nullable=True),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("ratio", sa.Numeric(12, 6), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("voided", sa.Boolean(), nullable=False),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cdr_ticker_id"], ["tickers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_corporate_actions_us_split", "corporate_actions", ["us_symbol", "ex_date"],
        unique=True,
        sqlite_where=sa.text("kind = 'us_split' AND NOT voided"),
        postgresql_where=sa.text("kind = 'us_split' AND NOT voided"),
    )
    op.create_index(
        "uq_corporate_actions_cdr_split", "corporate_actions", ["cdr_ticker_id", "ex_date"],
        unique=True,
        sqlite_where=sa.text("kind = 'cdr_split' AND NOT voided"),
        postgresql_where=sa.text("kind = 'cdr_split' AND NOT voided"),
    )
    op.create_table(
        "stop_updates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("session", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("old_us_stop", sa.Numeric(12, 4), nullable=False),
        sa.Column("new_us_stop", sa.Numeric(12, 4), nullable=False),
        sa.Column("old_cdr_stop", sa.Numeric(12, 4), nullable=False),
        sa.Column("new_cdr_stop", sa.Numeric(12, 4), nullable=False),
        sa.Column("late", sa.Boolean(), nullable=False),
        sa.Column("corporate_action_id", sa.Integer(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["signal_id"], ["trade_signals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["corporate_action_id"], ["corporate_actions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stop_updates_signal_id", "stop_updates", ["signal_id"])
    op.create_index(
        "uq_stop_updates_trail_signal_session", "stop_updates", ["signal_id", "session"],
        unique=True,
        sqlite_where=sa.text("reason = 'trail'"),
        postgresql_where=sa.text("reason = 'trail'"),
    )
    op.create_table(
        "fills",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cdr_ticker_id", sa.Uuid(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 6), nullable=False),
        sa.Column("price_cad", sa.Numeric(12, 4), nullable=False),
        sa.Column("fee_cad", sa.Numeric(12, 4), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=True),
        sa.Column("exit_alert_id", sa.Integer(), nullable=True),
        sa.Column("voided", sa.Boolean(), nullable=False),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("forced", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["cdr_ticker_id"], ["tickers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["signal_id"], ["trade_signals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["exit_alert_id"], ["exit_alerts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fills_cdr_ticker_id", "fills", ["cdr_ticker_id"])
    op.create_table(
        "equity_snapshots",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("cash", sa.Numeric(14, 4), nullable=False),
        sa.Column("positions_value", sa.Numeric(14, 4), nullable=False),
        sa.Column("equity", sa.Numeric(14, 4), nullable=False),
        sa.Column("peak", sa.Numeric(14, 4), nullable=False),
        sa.PrimaryKeyConstraint("date"),
    )
    op.create_table(
        "risk_state",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("paused_at", sa.Date(), nullable=True),
        sa.Column("paused_reason", sa.Text(), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("peak_reset_on", sa.Date(), nullable=True),
        sa.Column("scale_up_history", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("risk_state")
    op.drop_table("equity_snapshots")
    op.drop_index("ix_fills_cdr_ticker_id", table_name="fills")
    op.drop_table("fills")
    op.drop_index("uq_stop_updates_trail_signal_session", table_name="stop_updates")
    op.drop_index("ix_stop_updates_signal_id", table_name="stop_updates")
    op.drop_table("stop_updates")
    op.drop_index("uq_corporate_actions_cdr_split", table_name="corporate_actions")
    op.drop_index("uq_corporate_actions_us_split", table_name="corporate_actions")
    op.drop_table("corporate_actions")
    op.drop_index("ix_exit_alerts_signal_id", table_name="exit_alerts")
    op.drop_table("exit_alerts")
    op.drop_table("trade_signals")
    op.drop_table("cash_movements")
    op.drop_table("live_config")
```

- [ ] **Step 5: Run the tests and checks**

Run: `uv run pytest tests/test_live_schema.py tests/test_migrations.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 14 passed (7 new schema tests, and the 7 migration tests with the new one); the full suite 613 passed; ruff `All checks passed!`; mypy `Success`. (`test_migration_0013_creates_the_model_columns_and_drops_them_again` runs the migration's own `upgrade()` and `downgrade()` on SQLite and compares every column name, nullability, and partial unique index with the models.)

- [ ] **Step 6: Commit**

```bash
git add src/signalbench/db/models.py alembic/versions/0013_live_ledger.py tests/test_live_schema.py tests/test_migrations.py
git commit -m "feat: live ledger tables and migration 0013

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The ACB engine and the hand-checked examples

**Files:**
- Create: `tests/fixtures/live_ledger_examples.yaml`, `tests/live_helpers.py`, `tests/test_live_acb.py`, `src/signalbench/live/__init__.py`, `src/signalbench/live/acb.py`

The examples file is the spec's "checked by hand and committed as a fixture". Task 3 runs the same four examples through the ledger and the database.

- [ ] **Step 1: Write the examples, the helper, and the failing tests**

Create `tests/fixtures/live_ledger_examples.yaml`:

```yaml
# Hand-checked ledger examples (spec 04, Testing), read by tests/test_live_acb.py and
# tests/test_live_ledger.py. Every expected number was worked out by hand; the arithmetic is in
# the comments. Money is CAD. `units` and `acb` are the holding after each event. A disposition's
# `gain` is before the superficial-loss adjustment and `denied` is the part added to ACB.
# Not tax advice.

acb:  # the spec's worked example: buy 3 @ 30, buy 2 @ 33, sell 4 @ 35, buy 1 @ 31, sell 2 @ 29
  deposit: {date: 2026-01-05, amount: '300.00'}
  events:
    - {date: 2026-01-05, side: buy, quantity: '3', price: '30.00', units: '3', acb: '90.00'}
    # 90 + 2 x 33 = 156.00 for 5 units: 31.20 a unit
    - {date: 2026-01-12, side: buy, quantity: '2', price: '33.00', units: '5', acb: '156.00'}
    # ACB sold 156 x 4/5 = 124.80; gain 4 x 35 - 124.80 = 15.20; left 156 - 124.80 = 31.20
    - {date: 2026-02-02, side: sell, quantity: '4', price: '35.00', units: '1', acb: '31.20'}
    # 31.20 + 31 = 62.20 for 2 units: 31.10 a unit
    - {date: 2026-02-09, side: buy, quantity: '1', price: '31.00', units: '2', acb: '62.20'}
    # ACB sold 62.20; gain 2 x 29 - 62.20 = -4.20, a loss
    - {date: 2026-02-16, side: sell, quantity: '2', price: '29.00', units: '0', acb: '0'}
  dispositions:
    - {date: 2026-02-02, proceeds: '140.00', acb: '124.80', gain: '15.20', denied: '0'}
    # The window is 2026-01-17 to 2026-03-18. 1 unit was bought in it (the re-buy on 02-09), but
    # 0 are held at the end of 03-18: min(2, 1, 0) = 0, so none of the loss is superficial.
    - {date: 2026-02-16, proceeds: '58.00', acb: '62.20', gain: '-4.20', denied: '0'}
  cash: '311.00'  # 300 - 90 - 66 + 140 - 31 + 58

acb_rebuy:  # the same five fills, then 1 more unit bought @ 28 ten days after the loss
  deposit: {date: 2026-01-05, amount: '300.00'}
  events:
    - {date: 2026-01-05, side: buy, quantity: '3', price: '30.00', units: '3', acb: '90.00'}
    - {date: 2026-01-12, side: buy, quantity: '2', price: '33.00', units: '5', acb: '156.00'}
    - {date: 2026-02-02, side: sell, quantity: '4', price: '35.00', units: '1', acb: '31.20'}
    - {date: 2026-02-09, side: buy, quantity: '1', price: '31.00', units: '2', acb: '62.20'}
    # Bought in the window: 1 (02-09) + 1 (02-26) = 2; held at the end of 03-18: 1.
    # denied = 4.20 x min(2, 2, 1) / 2 = 2.10, added to the ACB: 0 units, ACB 2.10
    - {date: 2026-02-16, side: sell, quantity: '2', price: '29.00', units: '0', acb: '2.10'}
    # 2.10 + 28 = 30.10 for the 1 unit held
    - {date: 2026-02-26, side: buy, quantity: '1', price: '28.00', units: '1', acb: '30.10'}
  dispositions:
    - {date: 2026-02-02, proceeds: '140.00', acb: '124.80', gain: '15.20', denied: '0'}
    # allowed loss: -4.20 + 2.10 = -2.10
    - {date: 2026-02-16, proceeds: '58.00', acb: '62.20', gain: '-4.20', denied: '2.10'}
  cash: '283.00'  # 311 - 28

cdr_split:  # buy 3 @ 30, a 2-for-1 CDR split, then sell 4 @ 16
  deposit: {date: 2026-03-02, amount: '100.00'}
  events:
    - {date: 2026-03-02, side: buy, quantity: '3', price: '30.00', units: '3', acb: '90.00'}
    # 3 x 2 = 6 units; the total ACB stays 90.00, so 30.00 a unit becomes 15.00
    - {date: 2026-03-09, split: '2', units: '6', acb: '90.00'}
    # ACB sold 90 x 4/6 = 60.00; gain 4 x 16 - 60 = 4.00; left 2 units, 30.00 (15.00 a unit)
    - {date: 2026-03-16, side: sell, quantity: '4', price: '16.00', units: '2', acb: '30.00'}
  dispositions:
    - {date: 2026-03-16, proceeds: '64.00', acb: '60.00', gain: '4.00', denied: '0'}
  splits:
    - {date: 2026-03-09, ratio: '2', before: '30.00', after: '15.00'}
  cash: '74.00'  # 100 - 90 + 64; the split moves no cash

fractional:  # fractional units and fees
  deposit: {date: 2026-04-01, amount: '50.00'}
  events:
    - {date: 2026-04-01, side: buy, quantity: '0.5', price: '40.00', units: '0.5', acb: '20.00'}
    # 20 + 0.25 x 44 + 0.10 fee = 31.10
    - {date: 2026-04-02, side: buy, quantity: '0.25', price: '44.00', fee: '0.10', units: '0.75', acb: '31.10'}
    # ACB sold 31.10 x 0.3/0.75 = 12.44; gain 0.3 x 45 - 0.05 - 12.44 = 1.01; left 18.66
    - {date: 2026-04-06, side: sell, quantity: '0.3', price: '45.00', fee: '0.05', units: '0.45', acb: '18.66'}
  dispositions:
    - {date: 2026-04-06, proceeds: '13.50', acb: '12.44', gain: '1.01', denied: '0'}
  cash: '32.35'  # 50 - 20 - 11.10 + 13.45
```

Create `tests/live_helpers.py`:

```python
"""Shared by the spec 04 ledger tests: the hand-checked examples, tickers, prices, a ledger."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from signalbench.live.acb import SplitEvent, Trade

EXAMPLES = Path(__file__).parent / "fixtures" / "live_ledger_examples.yaml"


def load_example(name: str) -> dict[str, Any]:
    examples: dict[str, Any] = yaml.safe_load(EXAMPLES.read_text(encoding="utf-8"))
    return dict(examples[name])


def example_events(example: dict[str, Any]) -> tuple[list[Trade], list[SplitEvent]]:
    """An example's fills and splits, numbered in file order."""
    trades: list[Trade] = []
    splits: list[SplitEvent] = []
    for number, event in enumerate(example["events"], start=1):
        day: date = event["date"]
        if "split" in event:
            splits.append(SplitEvent(number, day, Decimal(event["split"])))
        else:
            trades.append(
                Trade(number, day, event["side"], Decimal(event["quantity"]),
                      Decimal(event["price"]), Decimal(event.get("fee", "0")))
            )
    return trades, splits
```

Create `tests/test_live_acb.py`:

```python
"""Spec 04: the ACB engine on the hand-checked examples, superficial losses, and CDR splits."""

from datetime import date
from decimal import Decimal

import pytest

from live_helpers import example_events, load_example
from signalbench.live.acb import AcbError, SplitEvent, Trade, replay

EXAMPLES = ["acb", "acb_rebuy", "cdr_split", "fractional"]
LATER = date(2026, 12, 31)


def _buy(number: int, day: date, quantity: str, price: str) -> Trade:
    return Trade(number, day, "buy", Decimal(quantity), Decimal(price))


def _sell(number: int, day: date, quantity: str, price: str) -> Trade:
    return Trade(number, day, "sell", Decimal(quantity), Decimal(price))


@pytest.mark.parametrize("name", EXAMPLES)
def test_every_hand_checked_holding_and_disposition(name: str) -> None:
    example = load_example(name)
    trades, splits = example_events(example)
    book = replay(trades, splits, LATER)
    assert [(step.units, step.acb) for step in book.steps] == [
        (Decimal(e["units"]), Decimal(e["acb"])) for e in example["events"]
    ]
    assert [(d.trade.day, d.proceeds, d.acb, d.gain, d.denied) for d in book.dispositions] == [
        (d["date"], Decimal(d["proceeds"]), Decimal(d["acb"]), Decimal(d["gain"]),
         Decimal(d["denied"]))
        for d in example["dispositions"]
    ]
    assert not any(d.provisional for d in book.dispositions)


def test_the_rebuy_turns_half_the_loss_superficial_and_the_held_unit_carries_it() -> None:
    trades, splits = example_events(load_example("acb_rebuy"))
    book = replay(trades, splits, LATER)
    loss = book.dispositions[1]
    assert (loss.gain, loss.denied, loss.allowed) == (
        Decimal("-4.20"), Decimal("2.10"), Decimal("-2.10")
    )
    assert (book.units, book.acb, book.per_unit) == (Decimal(1), Decimal("30.10"), Decimal("30.10"))


def test_a_loss_is_provisional_until_its_30th_day_has_passed() -> None:
    trades, splits = example_events(load_example("acb_rebuy"))
    on_day_30 = replay(trades, splits, date(2026, 3, 18))
    after = replay(trades, splits, date(2026, 3, 19))
    assert [d.provisional for d in on_day_30.dispositions] == [False, True]  # a gain never is
    assert [d.provisional for d in after.dispositions] == [False, False]


def test_a_cdr_split_keeps_the_total_acb_and_halves_the_acb_per_unit() -> None:
    trades, splits = example_events(load_example("cdr_split"))
    book = replay(trades, splits, LATER)
    [record] = book.splits
    assert (record.units_before, record.units_after, record.acb) == (
        Decimal(3), Decimal(6), Decimal("90.00")
    )
    assert (record.per_unit_before, record.per_unit_after) == (Decimal(30), Decimal(15))


def test_a_split_inside_the_window_counts_later_units_on_the_sale_scale() -> None:
    # Buy 2 @ 50, sell both @ 40 (a loss of 20), a 2-for-1 split, then buy 2 post-split units @ 21.
    # On the sale's scale: bought 2 + 2/2 = 3, held at day 30 = 2/2 = 1, so
    # denied = 20 x min(2, 3, 1) / 2 = 10. The 2 units held carry ACB 10 + 42 = 52.
    trades = [
        _buy(1, date(2026, 4, 1), "2", "50"),
        _sell(2, date(2026, 4, 10), "2", "40"),
        _buy(3, date(2026, 4, 20), "2", "21"),
    ]
    book = replay(trades, [SplitEvent(1, date(2026, 4, 15), Decimal(2))], LATER)
    assert (book.dispositions[0].gain, book.dispositions[0].denied) == (Decimal(-20), Decimal(10))
    assert (book.units, book.acb) == (Decimal(2), Decimal(52))


def test_a_fill_on_the_ex_date_is_already_in_post_split_units() -> None:
    trades = [_buy(1, date(2026, 3, 2), "3", "30"), _buy(2, date(2026, 3, 9), "1", "15")]
    book = replay(trades, [SplitEvent(1, date(2026, 3, 9), Decimal(2))], LATER)
    assert (book.units, book.acb) == (Decimal(7), Decimal(105))  # 3 x 2 + 1, not (3 + 1) x 2


def test_fills_on_one_date_replay_in_the_order_they_were_recorded() -> None:
    day1, day2 = date(2026, 5, 4), date(2026, 5, 5)
    sell_first = replay(
        [_buy(1, day1, "2", "10"), _sell(2, day2, "2", "12"), _buy(3, day2, "2", "11")], [], LATER
    )
    buy_first = replay(
        [_buy(1, day1, "2", "10"), _sell(3, day2, "2", "12"), _buy(2, day2, "2", "11")], [], LATER
    )
    assert (sell_first.dispositions[0].gain, sell_first.acb) == (Decimal(4), Decimal(22))
    assert (buy_first.dispositions[0].gain, buy_first.acb) == (Decimal(3), Decimal(21))


def test_a_sale_of_more_than_is_held_is_refused_including_across_a_split() -> None:
    split = [SplitEvent(1, date(2026, 3, 9), Decimal(2))]
    buy = _buy(1, date(2026, 3, 2), "3", "30")
    assert replay([buy, _sell(2, date(2026, 3, 16), "6", "16")], split, LATER).units == 0
    with pytest.raises(AcbError, match="a sale of 7 on 2026-03-16 is more than the 6 units held"):
        replay([buy, _sell(2, date(2026, 3, 16), "7", "16")], split, LATER)
    with pytest.raises(AcbError, match="more than the 3 units held"):
        replay([buy, _sell(2, date(2026, 3, 6), "4", "31")], split, LATER)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_acb.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.live'`.

- [ ] **Step 3: Write the engine**

Create `src/signalbench/live/__init__.py`:

```python
"""The live ledger (spec 04): what the owner did on Wealthsimple, and what follows from it."""
```

Create `src/signalbench/live/acb.py`:

```python
"""Average cost (ACB), superficial losses, and CDR splits for one CDR symbol (spec 04).

Pure: Decimal in, Decimal out, no database and no clock. `replay()` walks one symbol's fills
and CDR splits in order (by date; on a date, a split before the fills, which are already in
post-split units; fills in the order they were recorded) and keeps the CRA average cost:

- buy: acb += quantity x price + fee; units += quantity
- sell: acb_sold = acb x quantity / units; gain = quantity x price - fee - acb_sold
- split with ratio r: units x= r; the total ACB is unchanged, so the ACB per unit is / r

A sale at a loss is a superficial loss in part when the same CDR symbol is bought from 30 days
before to 30 days after it and still held at the end of the 30th day:
denied = loss x min(quantity sold, bought in the window, held at day 30) / quantity sold, all
counted on the sale's scale. The denied amount is added to the ACB of the units held (when the
sale leaves none, it waits in the ACB for the re-buy). Nothing is rounded here.

Not tax advice: a record-keeping aid that follows the CRA's published method.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

Side = Literal["buy", "sell"]
WINDOW = timedelta(days=30)
ZERO = Decimal(0)


class AcbError(ValueError):
    """The fills cannot be replayed (a sale of more units than are held)."""


@dataclass(frozen=True)
class Trade:
    """A fill as the ACB sees it. `id` orders fills recorded on the same date."""

    id: int
    day: date
    side: Side
    quantity: Decimal
    price: Decimal
    fee: Decimal = ZERO


@dataclass(frozen=True)
class SplitEvent:
    """A CDR split: units held at the start of `day` (the ex-date) are multiplied by `ratio`."""

    id: int
    day: date
    ratio: Decimal


@dataclass(frozen=True)
class Holding:
    """Units and total ACB after one event."""

    event: Trade | SplitEvent
    units: Decimal
    acb: Decimal


@dataclass(frozen=True)
class Disposition:
    trade: Trade
    proceeds: Decimal  # quantity x price
    acb: Decimal  # the ACB of the units sold
    gain: Decimal  # proceeds - fee - acb, before any superficial-loss adjustment
    denied: Decimal  # the superficial loss added to ACB (0 when none)
    provisional: bool  # a loss whose 30-day window had not passed on `today`

    @property
    def allowed(self) -> Decimal:
        """The gain, or the loss that remains once the superficial part is denied."""
        return self.gain + self.denied


@dataclass(frozen=True)
class SplitRecord:
    split: SplitEvent
    units_before: Decimal
    acb: Decimal  # unchanged by the split
    units_after: Decimal

    @property
    def per_unit_before(self) -> Decimal | None:
        return self.acb / self.units_before if self.units_before else None

    @property
    def per_unit_after(self) -> Decimal | None:
        return self.acb / self.units_after if self.units_after else None


@dataclass(frozen=True)
class AcbBook:
    units: Decimal
    acb: Decimal
    steps: tuple[Holding, ...]  # one per event, in replay order
    dispositions: tuple[Disposition, ...]
    splits: tuple[SplitRecord, ...]

    @property
    def per_unit(self) -> Decimal | None:
        return self.acb / self.units if self.units else None


def ordered(trades: Sequence[Trade], splits: Sequence[SplitEvent]) -> list[Trade | SplitEvent]:
    """Replay order: by date; on a date, splits first (a fill on the ex-date is post-split),
    then fills by id."""
    events: list[Trade | SplitEvent] = [*trades, *splits]
    return sorted(events, key=lambda e: (e.day, isinstance(e, Trade), e.id))


def units_through(events: Sequence[Trade | SplitEvent], day: date) -> Decimal:
    """Units held at the end of `day`, on that day's scale."""
    units = ZERO
    for event in events:
        if event.day > day:
            break
        if isinstance(event, SplitEvent):
            units *= event.ratio
        elif event.side == "buy":
            units += event.quantity
        else:
            units -= event.quantity
    return units


def split_factor(splits: Sequence[SplitEvent], start: date, end: date) -> Decimal:
    """Units on `start`'s scale times this are on `end`'s scale (start <= end): the product of
    the ratios with an ex-date after `start`, on or before `end`."""
    factor = Decimal(1)
    for split in splits:
        if start < split.day <= end:
            factor *= split.ratio
    return factor


def _on_scale_of(quantity: Decimal, day: date, target: date, splits: Sequence[SplitEvent]) -> Decimal:
    if day <= target:
        return quantity * split_factor(splits, day, target)
    return quantity / split_factor(splits, target, day)


def denied_loss(
    sale: Trade, loss: Decimal, events: Sequence[Trade | SplitEvent], splits: Sequence[SplitEvent]
) -> Decimal:
    """The superficial part of `loss` (> 0) on `sale` (spec 04, ACB and superficial losses)."""
    first, last = sale.day - WINDOW, sale.day + WINDOW
    bought = sum(
        (
            _on_scale_of(e.quantity, e.day, sale.day, splits)
            for e in events
            if isinstance(e, Trade) and e.side == "buy" and first <= e.day <= last
        ),
        ZERO,
    )
    held = units_through(events, last) / split_factor(splits, sale.day, last)
    counted = min(sale.quantity, bought, held)
    return loss * counted / sale.quantity if counted > 0 else ZERO


def replay(trades: Sequence[Trade], splits: Sequence[SplitEvent], today: date) -> AcbBook:
    """Every event in order, with the ACB, the dispositions, and the splits' ACB per unit.
    Raises AcbError when a sale is larger than the units held."""
    events = ordered(trades, splits)
    units = acb = ZERO
    steps: list[Holding] = []
    dispositions: list[Disposition] = []
    records: list[SplitRecord] = []
    for event in events:
        if isinstance(event, SplitEvent):
            records.append(SplitRecord(event, units, acb, units * event.ratio))
            units *= event.ratio
        elif event.side == "buy":
            acb += event.quantity * event.price + event.fee
            units += event.quantity
        else:
            if event.quantity > units:
                raise AcbError(
                    f"a sale of {event.quantity.normalize():f} on {event.day} is more than the "
                    f"{units.normalize():f} units held"
                )
            sold = acb * event.quantity / units
            proceeds = event.quantity * event.price
            gain = proceeds - event.fee - sold
            acb -= sold
            units -= event.quantity
            denied = denied_loss(event, -gain, events, splits) if gain < 0 else ZERO
            acb += denied
            provisional = gain < 0 and today <= event.day + WINDOW
            dispositions.append(Disposition(event, proceeds, sold, gain, denied, provisional))
        steps.append(Holding(event, units, acb))
    return AcbBook(units, acb, tuple(steps), tuple(dispositions), tuple(records))
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_acb.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 11 passed; the full suite 624 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/live_ledger_examples.yaml tests/live_helpers.py tests/test_live_acb.py src/signalbench/live/__init__.py src/signalbench/live/acb.py
git commit -m "feat: ACB engine with superficial losses and CDR splits, on hand-checked examples

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The ledger's first layer: cash, fills, voids, and the rules

**Files:**
- Create: `src/signalbench/live/book.py`, `src/signalbench/live/ledger.py`, `tests/test_live_ledger.py`
- Modify: `tests/live_helpers.py`

`Ledger` is built in three layers so each task adds one file: `book.py` (`LedgerBook`: cash, fills, voids; Task 4 adds signals and exit alerts), `levels.py` (`LedgerLevels`: prices, stops, splits; Tasks 5–6), and `ledger.py` (`Ledger`: positions, equity, the pause, the tax report, the scale-up check; Tasks 7, 9, 10). From this task on, tests and callers use `Ledger` only; this task's `ledger.py` is a two-line subclass that Task 5 rebases and Task 7 replaces.

- [ ] **Step 1: Write the helpers and the failing tests**

In `tests/live_helpers.py`, replace:

```python
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from signalbench.live.acb import SplitEvent, Trade
```

with:

```python
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlmodel import Session

from signalbench.db.models import Ticker, TickerKind
from signalbench.live.acb import SplitEvent, Trade
from signalbench.live.ledger import Ledger
from strategy_helpers import WeekdaySessions
```

Append to `tests/live_helpers.py`:

```python
TODAY = date(2026, 12, 31)  # the owner's date in these tests, after every fill


def add_pair(
    session: Session, us: str = "NVDA", cdr: str = "ZNVD", sector: str = "Information Technology"
) -> tuple[Ticker, Ticker]:
    """A US stock and its CDR, as `signalbench seed` stores them."""
    stock = Ticker(symbol=us, company_name=us, sector=sector, kind=TickerKind.us_stock,
                   price_symbol=us)
    session.add(stock)
    session.flush()
    receipt = Ticker(symbol=cdr, company_name=us, sector=sector, kind=TickerKind.cdr,
                     price_symbol=f"{cdr}.NE", us_ticker_id=stock.id)
    session.add(receipt)
    session.commit()
    return stock, receipt


def make_ledger(session: Session, today: date = TODAY) -> Ledger:
    """A ledger on the weekday calendar of strategy_helpers (no holidays)."""
    return Ledger(session, calendar=WeekdaySessions(), today=today)


def record_example(ledger: Ledger, example: dict[str, Any], symbol: str = "ZTST") -> None:
    """An example's deposit and fills (splits are recorded by the caller)."""
    deposit = example["deposit"]
    ledger.record_cash(Decimal(deposit["amount"]), deposit["date"], "deposit")
    for event in example["events"]:
        if "split" in event:
            continue
        ledger.record_fill(
            cdr_symbol=symbol, side=event["side"], quantity=Decimal(event["quantity"]),
            price_cad=Decimal(event["price"]), trade_date=event["date"],
            fee_cad=Decimal(event.get("fee", "0")),
        )
```

Create `tests/test_live_ledger.py`:

```python
"""Spec 04: fills, cash, and voids in the ledger, on the hand-checked examples and the rules."""

import logging
from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from live_helpers import add_pair, load_example, make_ledger, record_example
from signalbench.db.models import Fill
from signalbench.live.ledger import Ledger, LedgerError

DAY = date(2026, 5, 4)


@pytest.fixture
def ledger(session: Session) -> Ledger:
    add_pair(session, "TST", "ZTST")
    add_pair(session, "OTH", "ZOTH")
    return make_ledger(session)


def _buy(ledger: Ledger, quantity: str, price: str, day: date = DAY, **extra: object) -> Fill:
    return ledger.record_fill(cdr_symbol="ZTST", side="buy", quantity=Decimal(quantity),
                              price_cad=Decimal(price), trade_date=day, **extra)


def _sell(ledger: Ledger, quantity: str, price: str, day: date = DAY) -> Fill:
    return ledger.record_fill(cdr_symbol="ZTST", side="sell", quantity=Decimal(quantity),
                              price_cad=Decimal(price), trade_date=day)


@pytest.mark.parametrize("name", ["acb", "acb_rebuy", "fractional"])
def test_the_hand_checked_examples_through_the_database(ledger: Ledger, name: str) -> None:
    example = load_example(name)
    record_example(ledger, example)
    book = ledger.books()["ZTST"]
    assert [(step.units, step.acb) for step in book.steps] == [
        (Decimal(e["units"]), Decimal(e["acb"])) for e in example["events"]
    ]
    assert [(d.gain, d.denied) for d in book.dispositions] == [
        (Decimal(d["gain"]), Decimal(d["denied"])) for d in example["dispositions"]
    ]
    assert ledger.cash() == Decimal(example["cash"])


def test_cash_counts_movements_and_fills_through_a_date(ledger: Ledger) -> None:
    ledger.record_cash(Decimal("100.00"), DAY, "deposit")
    _buy(ledger, "2", "30.00")
    _sell(ledger, "1", "31.50", date(2026, 5, 6))
    ledger.record_cash(Decimal("-20.00"), date(2026, 5, 7), "withdrawal")
    assert ledger.cash(DAY) == Decimal("40.00")
    assert ledger.cash(date(2026, 5, 6)) == Decimal("71.50")
    assert ledger.cash() == Decimal("51.50")


def test_a_void_reverses_cash_and_units_and_keeps_the_row(ledger: Ledger, session: Session) -> None:
    ledger.record_cash(Decimal("100.00"), DAY, "deposit")
    fill = _buy(ledger, "2", "30.00")
    assert fill.id is not None
    ledger.void_fill(fill.id, "typed the wrong price")
    assert ledger.cash() == Decimal("100.00")
    assert "ZTST" not in ledger.books()
    [row] = session.exec(select(Fill)).all()
    assert (row.voided, row.void_reason) == (True, "typed the wrong price")
    with pytest.raises(LedgerError, match=f"no fill {fill.id} to void"):
        ledger.void_fill(fill.id, "again")


def test_a_void_that_would_leave_an_oversell_is_refused(ledger: Ledger) -> None:
    ledger.record_cash(Decimal("100.00"), DAY, "deposit")
    buy = _buy(ledger, "2", "30.00")
    _sell(ledger, "2", "31.00", date(2026, 5, 5))
    assert buy.id is not None
    with pytest.raises(LedgerError, match="record the corrected fill first"):
        ledger.void_fill(buy.id, "wrong quantity")
    _buy(ledger, "2", "30.00", force=True)  # the corrected buy first (cash is short until the void)
    ledger.void_fill(buy.id, "wrong quantity")
    assert ledger.books()["ZTST"].units == 0


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"quantity": Decimal(0)}, "quantity must be above 0"),
        ({"quantity": Decimal("0.0000001")}, "quantity 1E-7 has more than 6 decimals"),
        ({"price_cad": Decimal(-1)}, "price must be above 0"),
        ({"price_cad": Decimal("30.00001")}, "price 30.00001 has more than 4 decimals"),
        ({"fee_cad": Decimal("-0.01")}, "fee cannot be negative"),
        ({"trade_date": date(2027, 1, 1)}, "trade date 2027-01-01 is after today"),
        ({"trade_date": date(2026, 5, 1)}, "before the first cash movement"),
        ({"cdr_symbol": "ZZZZ"}, "ZZZZ is not a known CDR"),
        ({"side": "short"}, "side must be one of buy, sell"),
    ],
)
def test_a_bad_fill_is_refused(ledger: Ledger, changes: dict[str, object], message: str) -> None:
    ledger.record_cash(Decimal("100.00"), DAY, "deposit")
    fill: dict[str, object] = {
        "cdr_symbol": "ZTST", "side": "buy", "quantity": Decimal(1), "price_cad": Decimal(30),
        "trade_date": DAY,
    }
    with pytest.raises(LedgerError, match=message):
        ledger.record_fill(**{**fill, **changes})


def test_a_fill_needs_a_deposit_first(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="record the first deposit before any fill"):
        _buy(ledger, "1", "30.00")


def test_a_sale_of_more_than_is_held_is_refused(ledger: Ledger, session: Session) -> None:
    ledger.record_cash(Decimal("100.00"), DAY, "deposit")
    _buy(ledger, "2", "30.00")
    with pytest.raises(LedgerError, match="ZTST: a sale of 3 on 2026-05-04 is more than the 2"):
        _sell(ledger, "3", "31.00")
    with pytest.raises(LedgerError, match="more than the 0 units held"):
        ledger.record_fill(cdr_symbol="ZOTH", side="sell", quantity=Decimal(1),
                           price_cad=Decimal(10), trade_date=DAY)
    assert len(session.exec(select(Fill)).all()) == 1  # nothing half-written


def test_a_buy_beyond_the_cash_is_refused_unless_forced_and_then_logged(
    ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    ledger.record_cash(Decimal("50.00"), DAY, "deposit")
    with pytest.raises(LedgerError, match="takes cash to C\\$-10.00; there is no margin"):
        _buy(ledger, "2", "30.00")
    with caplog.at_level(logging.WARNING, logger="signalbench.live.book"):
        fill = _buy(ledger, "2", "30.00", force=True)
    assert fill.forced
    assert "forced buy: ZTST 2 x 30.00 on 2026-05-04 takes cash to C$-10.00" in caplog.text
    assert ledger.cash() == Decimal("-10.00")


def test_a_deposit_on_the_trade_date_pays_for_that_day_s_buy(ledger: Ledger) -> None:
    ledger.record_cash(Decimal("10.00"), DAY, "deposit")
    first = _buy(ledger, "1", "10.00")
    ledger.record_cash(Decimal("50.00"), date(2026, 5, 5), "deposit")
    second = _buy(ledger, "1", "50.00", date(2026, 5, 5))
    assert (first.forced, second.forced, ledger.cash()) == (False, False, Decimal(0))


def test_a_later_withdrawal_that_the_buy_would_overdraw_refuses_the_buy(ledger: Ledger) -> None:
    ledger.record_cash(Decimal("100.00"), DAY, "deposit")
    ledger.record_cash(Decimal("-60.00"), date(2026, 5, 6), "withdrawal")
    with pytest.raises(LedgerError, match="takes cash to C\\$-20.00"):
        _buy(ledger, "2", "30.00", date(2026, 5, 5))


@pytest.mark.parametrize(
    ("amount", "day", "message"),
    [
        (Decimal(0), DAY, "cannot be 0"),
        (Decimal("10.001"), DAY, "amount 10.001 has more than 2 decimals"),
        (Decimal(10), date(2027, 1, 1), "after today"),
    ],
)
def test_a_bad_cash_movement_is_refused(
    ledger: Ledger, amount: Decimal, day: date, message: str
) -> None:
    with pytest.raises(LedgerError, match=message):
        ledger.record_cash(amount, day, "note")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_ledger.py tests/test_live_acb.py -q`
Expected: 2 collection errors, `ModuleNotFoundError: No module named 'signalbench.live.ledger'` (`tests/test_live_acb.py` too: the helper module now imports `Ledger`).

- [ ] **Step 3: Write the first layer**

Create `src/signalbench/live/book.py`:

```python
"""Fills, cash, voids, signals, and exit alerts: the ledger's first layer (spec 04).

Nothing is typed in as a total: cash and each CDR's units and ACB are replayed from fills,
cash movements, and CDR splits. Fills are never deleted (a mistake is voided). Money is CAD
and Decimal throughout.

Ids are integers: the owner types `/void 12`, and Telegram button data is capped at 64 bytes.
"""


import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from uuid import UUID

from sqlmodel import Session, col, select

from signalbench.db.models import (
    CashMovement,
    CorporateAction,
    ExitAlert,
    Fill,
    Ticker,
    TickerKind,
    TradeSignal,
)
from signalbench.live.acb import (
    AcbBook,
    AcbError,
    Side,
    SplitEvent,
    Trade,
    ordered,
    replay,
)
from signalbench.market.calendar import Sessions

SignalStatus = Literal["sent", "taken", "skipped", "expired", "withdrawn"]
SignalSkip = Literal["disagree", "no_time", "price_moved", "wide_spread", "other"]
AlertReason = Literal["stop", "earnings"]
AlertStatus = Literal["sent", "done", "ignored"]
SplitKind = Literal["us_split", "cdr_split"]
SplitSource = Literal["yfinance", "owner"]
OrderType = Literal["limit", "market"]

QUANTITY_STEP = Decimal("0.000001")  # units: 6 decimals, as stored
PRICE_STEP = Decimal("0.0001")  # prices, fees, and stops: 4 decimals, as stored
CENT = Decimal("0.01")
ZERO = Decimal(0)

log = logging.getLogger(__name__)


class LedgerError(ValueError):
    """A ledger write or read was refused. The message says why, for the owner."""


def q4(value: Decimal) -> Decimal:
    """A price or stop on the stored 4-decimal scale."""
    return value.quantize(PRICE_STEP, rounding=ROUND_HALF_UP)


def cad(value: Decimal) -> str:
    return f"C${value.quantize(CENT, rounding=ROUND_HALF_UP)}"


def check_places(value: Decimal, places: int, name: str) -> None:
    if value != value.quantize(Decimal(1).scaleb(-places)):
        raise LedgerError(f"{name} {value} has more than {places} decimals")


def check_choice(value: str, choices: tuple[str, ...], name: str) -> None:
    if value not in choices:
        raise LedgerError(f"{name} must be one of {', '.join(choices)}, not {value!r}")


@dataclass(frozen=True)
class Episode:
    """One position in one CDR: from the buy that took the units above 0 to the sale that took
    them back to 0. Managed when that opening buy is linked to a signal."""

    cdr_ticker_id: UUID
    fills: tuple[Fill, ...]  # non-voided, in replay order
    closed_on: date | None  # None while open

    @property
    def opening(self) -> Fill:
        return self.fills[0]

    @property
    def signal_id(self) -> int | None:
        return self.opening.signal_id

    @property
    def managed(self) -> bool:
        return self.signal_id is not None

    @property
    def key(self) -> str:
        """The position id decide() sees: the signal id, or manual-<opening fill id>."""
        return str(self.signal_id) if self.managed else f"manual-{self.opening.id}"

    def pnl(self) -> Decimal:
        """Cash in minus cash out over the episode, fees included (the realized P&L once
        closed). Splits move no cash, so they never change it."""
        return sum((_cash_effect(fill) for fill in self.fills), ZERO)


def episodes(fills: Sequence[Fill], splits: Sequence[CorporateAction]) -> list[Episode]:
    """One CDR's fills cut into positions: each starts at a buy from 0 units and ends at the
    sale back to 0. `fills` are non-voided; `splits` non-voided CDR splits."""
    by_id = {fill.id: fill for fill in fills}
    found: list[Episode] = []
    current: list[Fill] = []
    units = ZERO
    for event in ordered([_trade(f) for f in fills], [_split_event(a) for a in splits]):
        if isinstance(event, SplitEvent):
            units *= event.ratio
            continue
        fill = by_id[event.id]
        current.append(fill)
        units += fill.quantity if fill.side == "buy" else -fill.quantity
        if units <= 0:
            found.append(Episode(fill.cdr_ticker_id, tuple(current), fill.trade_date))
            current, units = [], ZERO
    if current:
        found.append(Episode(current[0].cdr_ticker_id, tuple(current), None))
    return found


def _trade(fill: Fill) -> Trade:
    assert fill.id is not None
    side: Side = "buy" if fill.side == "buy" else "sell"
    return Trade(fill.id, fill.trade_date, side, fill.quantity, fill.price_cad, fill.fee_cad)


def _split_event(action: CorporateAction) -> SplitEvent:
    assert action.id is not None
    return SplitEvent(action.id, action.ex_date, action.ratio)


def _cash_effect(fill: Fill) -> Decimal:
    amount = fill.quantity * fill.price_cad
    return amount - fill.fee_cad if fill.side == "sell" else -(amount + fill.fee_cad)


class LedgerBook:
    """Fills, cash, voids, signals, and exit alerts. `today` is the owner's date (New York).
    `Ledger` (live/ledger.py) is the class to use: it adds stops, splits, and equity."""

    def __init__(self, session: Session, *, calendar: Sessions, today: date) -> None:
        self._session = session
        self._calendar = calendar
        self.today = today

    # --- Lookups ---------------------------------------------------------------------------

    def _ticker(self, symbol: str, kind: TickerKind) -> Ticker:
        ticker = self._session.exec(
            select(Ticker).where(Ticker.symbol == symbol, Ticker.kind == kind)
        ).first()
        if ticker is None:
            what = "CDR" if kind == TickerKind.cdr else "US stock"
            raise LedgerError(f"{symbol} is not a known {what}")
        return ticker

    def _cdr(self, symbol: str) -> Ticker:
        return self._ticker(symbol, TickerKind.cdr)

    def _us_of(self, cdr: Ticker) -> Ticker:
        us = None if cdr.us_ticker_id is None else self._session.get(Ticker, cdr.us_ticker_id)
        if us is None:
            raise LedgerError(f"{cdr.symbol} has no US stock in the universe")
        return us

    def _by_id(self, ticker_id: UUID) -> Ticker:
        ticker = self._session.get(Ticker, ticker_id)
        if ticker is None:
            raise LedgerError(f"no ticker {ticker_id}")
        return ticker

    def signal(self, signal_id: int) -> TradeSignal:
        signal = self._session.get(TradeSignal, signal_id)
        if signal is None:
            raise LedgerError(f"no signal {signal_id}")
        return signal

    def exit_alert(self, alert_id: int) -> ExitAlert:
        alert = self._session.get(ExitAlert, alert_id)
        if alert is None:
            raise LedgerError(f"no exit alert {alert_id}")
        return alert

    def _fills(self, cdr_id: UUID | None = None, through: date | None = None) -> list[Fill]:
        """Non-voided fills in replay order (trade date, then id)."""
        query = select(Fill).where(col(Fill.voided).is_(False))
        if cdr_id is not None:
            query = query.where(Fill.cdr_ticker_id == cdr_id)
        if through is not None:
            query = query.where(col(Fill.trade_date) <= through)
        return list(self._session.exec(query.order_by(col(Fill.trade_date), col(Fill.id))).all())

    def _actions(
        self, kind: SplitKind, *, us_symbol: str | None = None, cdr_id: UUID | None = None
    ) -> list[CorporateAction]:
        """Non-voided splits of one symbol, oldest ex-date first."""
        query = select(CorporateAction).where(
            CorporateAction.kind == kind, col(CorporateAction.voided).is_(False)
        )
        if us_symbol is not None:
            query = query.where(CorporateAction.us_symbol == us_symbol)
        if cdr_id is not None:
            query = query.where(CorporateAction.cdr_ticker_id == cdr_id)
        return list(
            self._session.exec(
                query.order_by(col(CorporateAction.ex_date), col(CorporateAction.id))
            ).all()
        )

    # --- Cash ------------------------------------------------------------------------------

    def record_cash(self, amount_cad: Decimal, occurred_on: date, note: str) -> CashMovement:
        """A deposit (+) or a withdrawal (-)."""
        if amount_cad == 0:
            raise LedgerError("a cash movement cannot be 0")
        check_places(amount_cad, 2, "amount")
        if occurred_on > self.today:
            raise LedgerError(f"{occurred_on} is after today ({self.today})")
        movement = CashMovement(amount_cad=amount_cad, occurred_on=occurred_on, note=note)
        self._session.add(movement)
        self._session.commit()
        self._session.refresh(movement)
        return movement

    def _movements(self, through: date | None = None) -> list[CashMovement]:
        query = select(CashMovement)
        if through is not None:
            query = query.where(col(CashMovement.occurred_on) <= through)
        return list(
            self._session.exec(
                query.order_by(col(CashMovement.occurred_on), col(CashMovement.id))
            ).all()
        )

    def cash(self, as_of: date | None = None) -> Decimal:
        """Deposits - withdrawals + sell proceeds - buy costs - fees, through `as_of`."""
        total = sum((m.amount_cad for m in self._movements(as_of)), ZERO)
        return total + sum((_cash_effect(f) for f in self._fills(through=as_of)), ZERO)

    def _lowest_cash_from(self, fill: Fill) -> Decimal:
        """The lowest running cash from `fill` on, over every movement and fill in date order
        (a movement before the fills of its date)."""
        events: list[tuple[date, int, int, Decimal]] = [
            (m.occurred_on, 0, m.id or 0, m.amount_cad) for m in self._movements()
        ]
        events += [(f.trade_date, 1, f.id or 0, _cash_effect(f)) for f in self._fills()]
        running, lowest, seen = ZERO, None, False
        for day, kind, key, amount in sorted(events):
            running += amount
            seen = seen or (kind == 1 and key == fill.id)
            if seen:
                lowest = running if lowest is None else min(lowest, running)
        return ZERO if lowest is None else lowest

    # --- Fills -----------------------------------------------------------------------------

    def record_fill(
        self,
        *,
        cdr_symbol: str,
        side: Side,
        quantity: Decimal,
        price_cad: Decimal,
        trade_date: date,
        signal_id: int | None = None,
        exit_alert_id: int | None = None,
        fee_cad: Decimal = ZERO,
        force: bool = False,
    ) -> Fill:
        """One buy or sell, validated (spec 04, Validation). A buy linked to a signal marks it
        taken; a sale linked to an exit alert marks it done. `force` records a buy the cash on
        hand does not cover, because Wealthsimple is the source of truth; it is logged."""
        cdr = self._cdr(cdr_symbol)
        check_choice(side, ("buy", "sell"), "side")
        for value, places, name in (
            (quantity, 6, "quantity"), (price_cad, 4, "price"),
        ):
            if value <= 0:
                raise LedgerError(f"{name} must be above 0, not {value}")
            check_places(value, places, name)
        if fee_cad < 0:
            raise LedgerError(f"fee cannot be negative, not {fee_cad}")
        check_places(fee_cad, 4, "fee")
        if trade_date > self.today:
            raise LedgerError(f"trade date {trade_date} is after today ({self.today})")
        movements = self._movements()
        if not movements:
            raise LedgerError("record the first deposit before any fill")
        if trade_date < movements[0].occurred_on:
            raise LedgerError(
                f"trade date {trade_date} is before the first cash movement "
                f"({movements[0].occurred_on})"
            )
        signal = None if signal_id is None else self._linkable_signal(signal_id, cdr, side)
        alert = None if exit_alert_id is None else self._linkable_alert(exit_alert_id, cdr, side)
        fill = Fill(
            cdr_ticker_id=cdr.id, side=side, quantity=quantity, price_cad=price_cad,
            fee_cad=fee_cad, trade_date=trade_date, signal_id=signal_id,
            exit_alert_id=exit_alert_id,
        )
        self._session.add(fill)
        self._session.flush()
        try:
            self._check_book(cdr)
            if side == "buy":
                lowest = self._lowest_cash_from(fill)
                if lowest < 0 and not force:
                    raise LedgerError(
                        f"a buy of {cad(quantity * price_cad + fee_cad)} on {trade_date} "
                        f"takes cash to {cad(lowest)}; there is no margin. If Wealthsimple "
                        "shows the fill, record it with force"
                    )
                if lowest < 0:
                    fill.forced = True
                    log.warning(
                        "forced buy: %s %s x %s on %s takes cash to %s",
                        cdr.symbol, quantity, price_cad, trade_date, cad(lowest),
                    )
        except LedgerError:
            self._session.rollback()
            raise
        if signal is not None and signal.status in ("sent", "expired"):
            signal.status = "taken"
            self._session.add(signal)
        if alert is not None:
            alert.status = "done"
            self._session.add(alert)
        self._session.commit()
        self._session.refresh(fill)
        return fill

    def _linkable_signal(self, signal_id: int, cdr: Ticker, side: str) -> TradeSignal:
        signal = self.signal(signal_id)
        if side != "buy":
            raise LedgerError("only a buy can be linked to a signal")
        if signal.cdr_ticker_id != cdr.id:
            raise LedgerError(f"signal {signal_id} is for another CDR, not {cdr.symbol}")
        if signal.status in ("skipped", "withdrawn"):
            raise LedgerError(
                f"signal {signal_id} is {signal.status}; record the buy without the signal"
            )
        return signal

    def _linkable_alert(self, alert_id: int, cdr: Ticker, side: str) -> ExitAlert:
        alert = self.exit_alert(alert_id)
        if side != "sell":
            raise LedgerError("only a sale can be linked to an exit alert")
        if alert.cdr_ticker_id != cdr.id:
            raise LedgerError(f"exit alert {alert_id} is for another CDR, not {cdr.symbol}")
        return alert

    def void_fill(self, fill_id: int, reason: str) -> None:
        """Void a fill: its cash and position effects are gone from every derivation."""
        fill = self._session.get(Fill, fill_id)
        if fill is None or fill.voided:
            raise LedgerError(f"no fill {fill_id} to void")
        if not reason.strip():
            raise LedgerError("a void needs a reason")
        fill.voided = True
        fill.void_reason = reason.strip()
        self._session.add(fill)
        self._session.flush()
        try:
            self._check_book(self._by_id(fill.cdr_ticker_id))
        except LedgerError as error:
            self._session.rollback()
            raise LedgerError(
                f"voiding fill {fill_id} breaks the ledger ({error}); record the corrected "
                "fill first, then void this one"
            ) from None
        self._session.commit()

    def _check_book(self, cdr: Ticker) -> None:
        """No sale of more than is held, and every signal-linked buy opens its signal's position
        or adds to it (a signal opens at most one position)."""
        fills = self._fills(cdr.id)
        splits = self._actions("cdr_split", cdr_id=cdr.id)
        try:
            self._book(fills, splits)
        except AcbError as error:
            raise LedgerError(f"{cdr.symbol}: {error}") from None
        opened: set[int] = set()
        for episode in episodes(fills, splits):
            for fill in episode.fills:
                if fill.signal_id is not None and fill.signal_id != episode.signal_id:
                    raise LedgerError(
                        f"{cdr.symbol}: the buy linked to signal {fill.signal_id} adds to a "
                        f"position it did not open ({episode.key}); record it without the signal"
                    )
            if episode.signal_id is not None:
                if episode.signal_id in opened:
                    raise LedgerError(
                        f"{cdr.symbol}: signal {episode.signal_id} already opened a position "
                        "that has closed; record the buy without the signal"
                    )
                opened.add(episode.signal_id)

    def _book(self, fills: Sequence[Fill], splits: Sequence[CorporateAction]) -> AcbBook:
        return replay([_trade(f) for f in fills], [_split_event(a) for a in splits], self.today)

    def _held_cdr_ids(self) -> list[UUID]:
        return sorted({f.cdr_ticker_id for f in self._fills()}, key=str)

    def books(self) -> dict[str, AcbBook]:
        """Each CDR's whole ACB replay, by CDR symbol."""
        return {
            self._by_id(cdr_id).symbol: self._book(
                self._fills(cdr_id), self._actions("cdr_split", cdr_id=cdr_id)
            )
            for cdr_id in self._held_cdr_ids()
        }
```

Create `src/signalbench/live/ledger.py`:

```python
"""The ledger (spec 04, Interface): the one class the scan, the bot, and the CLI use.

Built in layers: live/book.py (fills, cash, voids, signals, exit alerts), then live/levels.py
(prices, stops, splits), then this module (positions, equity, the pause, the tax report, and
the scale-up check).
"""

from signalbench.live.book import LedgerBook, LedgerError

__all__ = ["Ledger", "LedgerError"]


class Ledger(LedgerBook):
    """Everything derived from what the owner did on Wealthsimple."""
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_ledger.py tests/test_live_acb.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 34 passed (23 new, and the 11 of Task 2); the full suite 647 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/book.py src/signalbench/live/ledger.py tests/live_helpers.py tests/test_live_ledger.py
git commit -m "feat: live ledger cash, fills, and voids with the no-short and no-margin rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Signals, exit alerts, managed and manual positions, and trade R

**Files:**
- Modify: `src/signalbench/live/book.py`, `tests/live_helpers.py`
- Create: `tests/test_live_signals.py`

The evening scan (spec 05) writes `trade_signals` and `exit_alerts`; this task gives it the ledger methods to do so, with the rules the ledger owns: one open exit alert per position, a signal opens at most one position, and a signal-linked buy only opens or adds to its own signal's position.

- [ ] **Step 1: Write the helper and the failing tests**

In `tests/live_helpers.py`, replace:

```python
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlmodel import Session

from signalbench.db.models import Ticker, TickerKind
from signalbench.live.acb import SplitEvent, Trade
from signalbench.live.ledger import Ledger
from strategy_helpers import WeekdaySessions
```

with:

```python
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlmodel import Session

from paper_helpers import evening
from signalbench.db.models import Ticker, TickerKind, TradeSignal
from signalbench.live.acb import SplitEvent, Trade
from signalbench.live.ledger import Ledger
from strategy_helpers import WeekdaySessions
```

Append to `tests/live_helpers.py`:

```python
def send_signal(
    ledger: Ledger,
    as_of: date,
    *,
    us: str = "NVDA",
    cdr: str = "ZNVD",
    us_close: str = "200",
    us_stop: str = "188",
    cdr_close: str = "40",
    units: str = "1",
) -> TradeSignal:
    """A sent signal: stop_pct 6%, so cdr_stop 37.60 with the defaults. It expires at 16:00
    New York on the next weekday (its entry session)."""
    entry = WeekdaySessions().next_sessions(as_of, 1)[0]
    return ledger.record_signal(
        as_of=as_of, us_symbol=us, cdr_symbol=cdr, us_signal_close=Decimal(us_close),
        us_stop=Decimal(us_stop), cdr_signal_close=Decimal(cdr_close),
        suggested_units=Decimal(units), order_type="limit", risk_amount_cad=Decimal("2.40"),
        explanation="Closed at a 20-session high.", expires_at=evening(entry, 16),
    )
```

Create `tests/test_live_signals.py`:

```python
"""Spec 04: signals and exit alerts (written by the scan), managed vs manual, and trade R."""

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session

from live_helpers import add_pair, make_ledger, send_signal
from signalbench.live.ledger import Ledger, LedgerError

D = date(2026, 6, 1)  # a Monday: the signal session
E = date(2026, 6, 2)  # its entry session


@pytest.fixture
def ledger(session: Session) -> Ledger:
    add_pair(session)
    add_pair(session, "AMD", "ZAMD")
    ledger = make_ledger(session)
    ledger.record_cash(Decimal("1000.00"), D, "deposit")
    return ledger


def _fill(ledger: Ledger, side: str, quantity: str, price: str, day: date = E,
          cdr: str = "ZNVD", **links: int) -> None:
    ledger.record_fill(cdr_symbol=cdr, side=side, quantity=Decimal(quantity),
                       price_cad=Decimal(price), trade_date=day, **links)


def test_a_signal_derives_its_stop_pct_and_cdr_stop(ledger: Ledger) -> None:
    signal = send_signal(ledger, D)
    assert (signal.stop_pct, signal.cdr_stop, signal.status) == (
        Decimal("0.06"), Decimal("37.60"), "sent"
    )
    with pytest.raises(LedgerError, match="a NVDA signal for 2026-06-01 is already recorded"):
        send_signal(ledger, D)
    with pytest.raises(LedgerError, match="ZAMD is not the CDR of NVDA"):
        send_signal(ledger, E, cdr="ZAMD")
    with pytest.raises(LedgerError, match="0 < stop < close"):
        send_signal(ledger, E, us_stop="200")


def test_a_linked_buy_takes_the_signal_and_opens_a_managed_position(ledger: Ledger) -> None:
    signal = send_signal(ledger, D)
    assert signal.id is not None
    _fill(ledger, "buy", "1", "40.20", signal_id=signal.id)
    _fill(ledger, "buy", "1", "40.30", signal_id=signal.id)  # a second lot of the same signal
    assert signal.status == "taken"
    _fill(ledger, "buy", "2", "12.00", cdr="ZAMD")
    [manual, managed] = ledger.open_episodes()  # by CDR symbol: ZAMD, ZNVD
    assert (managed.managed, managed.key, len(managed.fills)) == (True, str(signal.id), 2)
    assert (manual.managed, manual.key) == (False, f"manual-{manual.opening.id}")


def test_links_that_do_not_fit_are_refused(ledger: Ledger) -> None:
    signal = send_signal(ledger, D)
    other = send_signal(ledger, D, us="AMD", cdr="ZAMD")
    assert signal.id is not None and other.id is not None
    with pytest.raises(LedgerError, match="only a buy can be linked to a signal"):
        _fill(ledger, "sell", "1", "40", signal_id=signal.id)
    with pytest.raises(LedgerError, match="is for another CDR, not ZAMD"):
        _fill(ledger, "buy", "1", "12", cdr="ZAMD", signal_id=signal.id)
    _fill(ledger, "buy", "1", "40.00")  # manual first
    with pytest.raises(LedgerError, match="adds to a position it did not open"):
        _fill(ledger, "buy", "1", "40.10", signal_id=signal.id)
    ledger.mark_signal(other.id, "skipped", "disagree")
    with pytest.raises(LedgerError, match="is skipped; record the buy without the signal"):
        _fill(ledger, "buy", "1", "12", cdr="ZAMD", signal_id=other.id)


def test_a_signal_opens_at_most_one_position(ledger: Ledger) -> None:
    signal = send_signal(ledger, D)
    assert signal.id is not None
    _fill(ledger, "buy", "1", "40.20", signal_id=signal.id)
    _fill(ledger, "sell", "1", "41.00", date(2026, 6, 3))
    with pytest.raises(LedgerError, match="already opened a position that has closed"):
        _fill(ledger, "buy", "1", "40.50", date(2026, 6, 4), signal_id=signal.id)


def test_an_expired_signal_can_still_be_taken_but_a_marked_one_cannot_change(
    ledger: Ledger,
) -> None:
    signal = send_signal(ledger, D)
    assert signal.id is not None
    ledger.mark_signal(signal.id, "expired")
    with pytest.raises(LedgerError, match="already expired"):
        ledger.mark_signal(signal.id, "withdrawn")
    _fill(ledger, "buy", "1", "40.20", signal_id=signal.id)  # reported after the scan
    assert ledger.signal(signal.id).status == "taken"
    other = send_signal(ledger, E)
    assert other.id is not None
    with pytest.raises(LedgerError, match="a skip, and only a skip, needs a skip reason"):
        ledger.mark_signal(other.id, "skipped")
    with pytest.raises(LedgerError, match="skip reason must be one of"):
        ledger.mark_signal(other.id, "skipped", "bored")  # type: ignore[arg-type]


def test_a_sent_signal_holds_its_slot_through_its_entry_session_s_close(
    ledger: Ledger,
) -> None:
    signal = send_signal(ledger, D)  # expires at 16:00 New York on E
    assert signal.id is not None
    assert ledger.pending_signals(D) == []  # written after D's decision
    assert [s.id for s in ledger.pending_signals(E)] == [signal.id]  # maybe bought, unreported
    assert ledger.pending_signals(date(2026, 6, 3)) == []
    ledger.mark_signal(signal.id, "expired")
    assert ledger.pending_signals(E) == []
    taken = send_signal(ledger, E)
    assert taken.id is not None
    _fill(ledger, "buy", "1", "40.10", date(2026, 6, 3), signal_id=taken.id)
    assert ledger.pending_signals(date(2026, 6, 3)) == []  # a position now, not a pending entry


def test_one_open_exit_alert_per_position_and_a_linked_sale_closes_it(ledger: Ledger) -> None:
    signal = send_signal(ledger, D)
    assert signal.id is not None
    with pytest.raises(LedgerError, match="has no open position"):
        ledger.record_exit_alert(signal_id=signal.id, as_of=E, reason="stop")
    _fill(ledger, "buy", "2", "40.20", signal_id=signal.id)
    alert = ledger.record_exit_alert(signal_id=signal.id, as_of=E, reason="stop")
    with pytest.raises(LedgerError, match="already has an open exit alert"):
        ledger.record_exit_alert(signal_id=signal.id, as_of=E, reason="earnings")
    ledger.mark_exit_alert(alert.id or 0, "ignored")
    again = ledger.record_exit_alert(signal_id=signal.id, as_of=date(2026, 6, 3), reason="stop")
    assert again.id is not None
    _fill(ledger, "sell", "2", "37.00", date(2026, 6, 4), exit_alert_id=again.id)
    assert ledger.exit_alert(again.id).status == "done"
    with pytest.raises(LedgerError, match="already done"):
        ledger.mark_exit_alert(again.id, "ignored")


def test_trade_r_is_pnl_over_planned_risk_in_cad_and_in_us_terms(ledger: Ledger) -> None:
    # Signal: US 200 / stop 188 (6%), CDR 40 / stop 37.60. Buy 2 @ 40.20, sell 2 @ 43.00.
    # P&L 86.00 - 80.40 = 5.60; planned risk 2 x (40 - 37.60) = 4.80; R = 5.60 / 4.80.
    signal = send_signal(ledger, D)
    assert signal.id is not None
    _fill(ledger, "buy", "2", "40.20", signal_id=signal.id)
    _fill(ledger, "sell", "2", "43.00", date(2026, 6, 5))
    _fill(ledger, "buy", "1", "12.00", cdr="ZAMD")
    _fill(ledger, "sell", "1", "11.00", date(2026, 6, 5), cdr="ZAMD")
    managed, manual = ledger.closed_trades()
    assert (managed.pnl, managed.planned_risk, managed.r) == (
        Decimal("5.60"), Decimal("4.80"), Decimal("5.60") / Decimal("4.80")
    )
    # US-equivalent: P&L x (200 / 40) over units x (200 - 188).
    us_r = managed.pnl * Decimal(200) / Decimal(40) / (2 * (Decimal(200) - Decimal(188)))
    assert us_r == managed.r
    assert (manual.episode.managed, manual.pnl, manual.r) == (False, Decimal("-1.00"), None)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_signals.py -q`
Expected: 8 failed, `AttributeError: 'Ledger' object has no attribute 'record_signal'`.

- [ ] **Step 3: Add signals, exit alerts, and closed trades**

In `src/signalbench/live/book.py`, replace:

```python
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal
from uuid import UUID

from sqlmodel import Session, col, select

from signalbench.db.models import (
    CashMovement,
    CorporateAction,
    ExitAlert,
    Fill,
    Ticker,
    TickerKind,
    TradeSignal,
)
from signalbench.live.acb import (
    AcbBook,
    AcbError,
    Side,
    SplitEvent,
    Trade,
    ordered,
    replay,
)
from signalbench.market.calendar import Sessions

SignalStatus = Literal["sent", "taken", "skipped", "expired", "withdrawn"]
SignalSkip = Literal["disagree", "no_time", "price_moved", "wide_spread", "other"]
AlertReason = Literal["stop", "earnings"]
AlertStatus = Literal["sent", "done", "ignored"]
SplitKind = Literal["us_split", "cdr_split"]
SplitSource = Literal["yfinance", "owner"]
OrderType = Literal["limit", "market"]

QUANTITY_STEP = Decimal("0.000001")  # units: 6 decimals, as stored
PRICE_STEP = Decimal("0.0001")  # prices, fees, and stops: 4 decimals, as stored
CENT = Decimal("0.01")
ZERO = Decimal(0)

log = logging.getLogger(__name__)
```

with:

```python
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, get_args
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from signalbench.db.models import (
    CashMovement,
    CorporateAction,
    ExitAlert,
    Fill,
    Ticker,
    TickerKind,
    TradeSignal,
)
from signalbench.live.acb import (
    AcbBook,
    AcbError,
    Side,
    SplitEvent,
    Trade,
    ordered,
    replay,
)
from signalbench.market.calendar import Sessions

SignalStatus = Literal["sent", "taken", "skipped", "expired", "withdrawn"]
SignalSkip = Literal["disagree", "no_time", "price_moved", "wide_spread", "other"]
AlertReason = Literal["stop", "earnings"]
AlertStatus = Literal["sent", "done", "ignored"]
SplitKind = Literal["us_split", "cdr_split"]
SplitSource = Literal["yfinance", "owner"]
OrderType = Literal["limit", "market"]

NEW_YORK = ZoneInfo("America/New_York")
MARKET_CLOSE = time(16, 0)  # a signal expires at the close of its entry session
QUANTITY_STEP = Decimal("0.000001")  # units: 6 decimals, as stored
PRICE_STEP = Decimal("0.0001")  # prices, fees, and stops: 4 decimals, as stored
CENT = Decimal("0.01")
STOP_PCT_STEP = Decimal("0.00000001")
ZERO = Decimal(0)

log = logging.getLogger(__name__)
```

In `src/signalbench/live/book.py`, replace:

```python
def episodes(fills: Sequence[Fill], splits: Sequence[CorporateAction]) -> list[Episode]:
```

with:

```python
@dataclass(frozen=True)
class ClosedTrade:
    """A position that went back to 0 units. R only for managed ones."""

    episode: Episode
    cdr_symbol: str
    opened_on: date
    closed_on: date
    pnl: Decimal  # CAD, after fees
    planned_risk: Decimal | None  # CAD: signal-linked buy units x (cdr_signal_close - cdr_stop)
    r: Decimal | None  # pnl / planned_risk: R on planned risk (spec 02)


def signal_stop(
    us_signal_close: Decimal, us_stop: Decimal, cdr_signal_close: Decimal
) -> tuple[Decimal, Decimal]:
    """(stop_pct, cdr_stop): the US stop's distance, applied to the CDR close (spec 05)."""
    stop_pct = ((us_signal_close - us_stop) / us_signal_close).quantize(STOP_PCT_STEP)
    return stop_pct, q4(cdr_signal_close * (1 - stop_pct))


def episodes(fills: Sequence[Fill], splits: Sequence[CorporateAction]) -> list[Episode]:
```

Append to `src/signalbench/live/book.py` (the end of `LedgerBook`):

```python
    # --- Signals and exit alerts (written by the evening scan, spec 05) --------------------

    def record_signal(
        self,
        *,
        as_of: date,
        us_symbol: str,
        cdr_symbol: str,
        us_signal_close: Decimal,
        us_stop: Decimal,
        cdr_signal_close: Decimal,
        suggested_units: Decimal,
        order_type: OrderType,
        risk_amount_cad: Decimal,
        explanation: str,
        expires_at: datetime,
    ) -> TradeSignal:
        """A sent entry signal. stop_pct and cdr_stop are derived here (signal_levels())."""
        us = self._ticker(us_symbol, TickerKind.us_stock)
        cdr = self._cdr(cdr_symbol)
        if cdr.us_ticker_id != us.id:
            raise LedgerError(f"{cdr_symbol} is not the CDR of {us_symbol}")
        check_choice(order_type, get_args(OrderType), "order type")
        if not 0 < us_stop < us_signal_close or cdr_signal_close <= 0 or suggested_units <= 0:
            raise LedgerError("a signal needs 0 < stop < close, a CDR close, and units above 0")
        taken = self._session.exec(
            select(TradeSignal).where(
                TradeSignal.as_of == as_of, TradeSignal.us_symbol == us_symbol,
                TradeSignal.setup == "breakout",
            )
        ).first()
        if taken is not None:
            raise LedgerError(f"a {us_symbol} signal for {as_of} is already recorded ({taken.id})")
        stop_pct, cdr_stop = signal_stop(us_signal_close, us_stop, cdr_signal_close)
        signal = TradeSignal(
            as_of=as_of, us_symbol=us_symbol, cdr_ticker_id=cdr.id,
            us_signal_close=q4(us_signal_close), us_stop=q4(us_stop), stop_pct=stop_pct,
            cdr_signal_close=q4(cdr_signal_close), cdr_stop=cdr_stop,
            suggested_units=suggested_units.quantize(QUANTITY_STEP), order_type=order_type,
            risk_amount_cad=q4(risk_amount_cad), explanation=explanation, expires_at=expires_at,
        )
        self._session.add(signal)
        self._session.commit()
        self._session.refresh(signal)
        return signal

    def mark_signal(
        self, signal_id: int, status: SignalStatus, skip_reason: SignalSkip | None = None
    ) -> TradeSignal:
        """A sent signal becomes skipped (with a reason), expired, or withdrawn. `taken` is
        set by record_fill."""
        signal = self.signal(signal_id)
        check_choice(status, ("skipped", "expired", "withdrawn"), "status")
        if signal.status != "sent":
            raise LedgerError(f"signal {signal_id} is already {signal.status}")
        if (status == "skipped") != (skip_reason is not None):
            raise LedgerError("a skip, and only a skip, needs a skip reason")
        if skip_reason is not None:
            check_choice(skip_reason, get_args(SignalSkip), "skip reason")
        signal.status = status
        signal.skip_reason = skip_reason
        self._session.add(signal)
        self._session.commit()
        self._session.refresh(signal)
        return signal

    def pending_signals(self, as_of: date) -> list[TradeSignal]:
        """Signals sent before `as_of` that are still `sent` and expire at or after its close:
        they hold slots (spec 04). A signal expires at the close of its entry session, so on
        that session's evening it still holds its slot: the owner may have bought without
        reporting it yet, as the backtest's entry would already be a position."""
        close = datetime.combine(as_of, MARKET_CLOSE, tzinfo=NEW_YORK)
        rows = self._session.exec(
            select(TradeSignal)
            .where(TradeSignal.status == "sent", col(TradeSignal.as_of) < as_of)
            .order_by(col(TradeSignal.id))
        ).all()
        return [s for s in rows if s.expires_at >= close]

    def record_exit_alert(
        self, *, signal_id: int, as_of: date, reason: AlertReason, late: bool = False
    ) -> ExitAlert:
        """A stop or earnings exit for the open managed position of `signal_id`. One `sent`
        alert per position at a time."""
        check_choice(reason, get_args(AlertReason), "reason")
        signal = self.signal(signal_id)
        if self._open_episode(signal) is None:
            raise LedgerError(f"signal {signal_id} has no open position")
        if self.open_exit_alert(signal_id) is not None:
            raise LedgerError(f"signal {signal_id} already has an open exit alert")
        alert = ExitAlert(
            signal_id=signal_id, cdr_ticker_id=signal.cdr_ticker_id, as_of=as_of, reason=reason,
            late=late,
        )
        self._session.add(alert)
        self._session.commit()
        self._session.refresh(alert)
        return alert

    def open_exit_alert(self, signal_id: int) -> ExitAlert | None:
        return self._session.exec(
            select(ExitAlert).where(ExitAlert.signal_id == signal_id, ExitAlert.status == "sent")
        ).first()

    def mark_exit_alert(self, alert_id: int, status: AlertStatus) -> ExitAlert:
        """A sent alert becomes done or ignored. After `ignored`, the position is managed
        again from the next session."""
        alert = self.exit_alert(alert_id)
        check_choice(status, ("done", "ignored"), "status")
        if alert.status != "sent":
            raise LedgerError(f"exit alert {alert_id} is already {alert.status}")
        alert.status = status
        self._session.add(alert)
        self._session.commit()
        self._session.refresh(alert)
        return alert

    # --- Positions over time ----------------------------------------------------------------

    def _episodes(self, cdr_id: UUID, through: date | None = None) -> list[Episode]:
        splits = self._actions("cdr_split", cdr_id=cdr_id)
        if through is not None:
            splits = [s for s in splits if s.ex_date <= through]
        return episodes(self._fills(cdr_id, through), splits)

    def _open_episode(self, signal: TradeSignal) -> Episode | None:
        for episode in self._episodes(signal.cdr_ticker_id):
            if episode.signal_id == signal.id and episode.closed_on is None:
                return episode
        return None

    def open_episodes(self) -> list[Episode]:
        """Every open position today, managed and manual, by CDR symbol."""
        found: list[tuple[str, Episode]] = []
        for cdr_id in self._held_cdr_ids():
            episodes_ = self._episodes(cdr_id)
            if episodes_ and episodes_[-1].closed_on is None:
                found.append((self._by_id(cdr_id).symbol, episodes_[-1]))
        return [episode for _, episode in sorted(found, key=lambda pair: pair[0])]

    def closed_trades(self) -> list[ClosedTrade]:
        """Every position that went back to 0 units, oldest close first."""
        trades: list[ClosedTrade] = []
        for cdr_id in self._held_cdr_ids():
            cdr = self._by_id(cdr_id)
            for episode in self._episodes(cdr_id):
                if episode.closed_on is None:
                    continue
                planned = r = None
                if episode.signal_id is not None:
                    signal = self.signal(episode.signal_id)
                    units = sum(
                        (f.quantity for f in episode.fills
                         if f.side == "buy" and f.signal_id == signal.id),
                        ZERO,
                    )
                    planned = units * (signal.cdr_signal_close - signal.cdr_stop)
                    r = episode.pnl() / planned
                trades.append(
                    ClosedTrade(
                        episode=episode, cdr_symbol=cdr.symbol,
                        opened_on=episode.opening.trade_date, closed_on=episode.closed_on,
                        pnl=episode.pnl(), planned_risk=planned, r=r,
                    )
                )
        return sorted(trades, key=lambda t: (t.closed_on, t.episode.fills[-1].id or 0))
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_signals.py tests/test_live_ledger.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 31 passed (8 new, and the 23 of Task 3); the full suite 655 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/book.py tests/live_helpers.py tests/test_live_signals.py
git commit -m "feat: live signals, exit alerts, managed and manual positions, and trade R

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: CDR marks, US-equivalent levels, and the trailing stop's rows

**Files:**
- Create: `src/signalbench/live/levels.py`, `tests/test_live_levels.py`
- Modify: `src/signalbench/live/ledger.py`, `tests/live_helpers.py`

`stop_in_force()` already replays `split` rows and splits recorded before a position opened; Task 6 writes them and tests that part.

- [ ] **Step 1: Write the helper and the failing tests**

In `tests/live_helpers.py`, replace:

```python
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlmodel import Session

from paper_helpers import evening
from signalbench.db.models import Ticker, TickerKind, TradeSignal
from signalbench.live.acb import SplitEvent, Trade
from signalbench.live.ledger import Ledger
from strategy_helpers import WeekdaySessions
```

with:

```python
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlmodel import Session

from paper_helpers import evening
from signalbench.db.models import Price, Ticker, TickerKind, TradeSignal
from signalbench.live.acb import SplitEvent, Trade
from signalbench.live.ledger import Ledger
from strategy_helpers import WeekdaySessions
```

Append to `tests/live_helpers.py`:

```python
def add_prices(
    session: Session,
    ticker: Ticker,
    days: Sequence[date],
    closes: Sequence[float],
    *,
    volume: int = 1_000_000,
    volumes: Sequence[int] | None = None,
) -> None:
    """Stored bars: open = close, high = close + 1, low = close - 1, adj_close = close."""
    for index, (day, close) in enumerate(zip(days, closes, strict=True)):
        value = Decimal(str(close))
        session.add(
            Price(ticker_id=ticker.id, date=day, open=value, high=value + 1, low=value - 1,
                  close=value, adj_close=value,
                  volume=volume if volumes is None else volumes[index])
        )
    session.commit()
```

Create `tests/test_live_levels.py`:

```python
"""Spec 04: CDR marks, US-equivalent levels, and the trailing stop's rows and timing."""

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session

from live_helpers import add_pair, add_prices, make_ledger, send_signal
from signalbench.db.models import TradeSignal
from signalbench.live.ledger import Ledger, LedgerError
from signalbench.live.levels import SignalLevels, StopLevels

D, E, F, G = date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3), date(2026, 6, 4)


@pytest.fixture
def ledger(session: Session) -> Ledger:
    stock, receipt = add_pair(session)
    add_prices(session, stock, [D, E, F, G], [200, 201, 202, 203])
    # The CDR trades on D and E, then only quotes: the last traded ratio is 40.20 / 201 = 0.2.
    add_prices(session, receipt, [D, E, F, G], [40, 40.2, 40.5, 40.5], volumes=[100, 100, 0, 0])
    ledger = make_ledger(session)
    ledger.record_cash(Decimal("100.00"), D, "deposit")
    return ledger


def _open(ledger: Ledger) -> TradeSignal:
    signal = send_signal(ledger, D)  # US 200 / stop 188, CDR 40 / stop 37.60
    assert signal.id is not None
    ledger.record_fill(cdr_symbol="ZNVD", side="buy", quantity=Decimal(1),
                       price_cad=Decimal("40.20"), trade_date=E, signal_id=signal.id)
    return signal


def test_the_cdr_mark_uses_the_last_traded_ratio_not_the_stale_quote(
    ledger: Ledger, session: Session
) -> None:
    cdr = ledger._cdr("ZNVD")
    assert ledger.cdr_ratio(cdr, G) == Decimal("0.2")
    assert ledger.cdr_mark(cdr, G) == Decimal("40.60")  # 203 x 0.2, not the stale 40.50
    assert ledger.cdr_mark(cdr, D) == Decimal(40)


def test_a_cdr_that_never_traded_is_marked_at_its_latest_close(session: Session) -> None:
    stock, receipt = add_pair(session, "AMD", "ZAMD")
    add_prices(session, stock, [D, E], [100, 110])
    add_prices(session, receipt, [D, E], [10, 10.5], volume=0)
    ledger = make_ledger(session)
    cdr = ledger._cdr("ZAMD")
    assert ledger.cdr_mark(cdr, E) == Decimal("10.5")
    assert ledger.cdr_ratio(cdr, E) == Decimal("10.5") / Decimal(110)
    assert ledger.cdr_mark(ledger._cdr("ZAMD"), date(2026, 5, 29)) is None


def test_levels_without_splits_are_the_signal_s_and_entry_us_scales_the_fill(
    ledger: Ledger,
) -> None:
    signal = _open(ledger)
    assert signal.id is not None
    assert ledger.signal_levels(signal.id) == SignalLevels(
        Decimal(200), Decimal(188), Decimal(40), Decimal("37.60")
    )
    assert ledger.entry_us(signal.id) == Decimal("201.00")  # 200 x 40.20 / 40
    assert ledger.current_stop(signal.id) == StopLevels(Decimal(188), Decimal("37.60"))


def test_a_raise_applies_from_the_next_session_with_the_cdr_stop_at_the_traded_ratio(
    ledger: Ledger,
) -> None:
    signal = _open(ledger)
    assert signal.id is not None
    row = ledger.record_stop_update(signal_id=signal.id, session=F, new_us_stop=Decimal(190))
    assert (row.reason, row.old_us_stop, row.new_us_stop, row.old_cdr_stop, row.new_cdr_stop) == (
        "trail", Decimal(188), Decimal(190), Decimal("37.60"), Decimal("38.00")  # 190 x 0.2
    )
    assert ledger.stop_in_force(signal.id, F) == StopLevels(Decimal(188), Decimal("37.60"))
    assert ledger.stop_in_force(signal.id, G) == StopLevels(Decimal(190), Decimal("38.00"))
    assert ledger.current_stop(signal.id).us == Decimal(190)


def test_a_trailing_stop_only_rises_once_per_session_on_an_open_managed_position(
    ledger: Ledger,
) -> None:
    signal = _open(ledger)
    assert signal.id is not None
    ledger.record_stop_update(signal_id=signal.id, session=F, new_us_stop=Decimal(190))
    for session_, stop, message in [
        (G, "189.5", "only rises: 189.5000 is not above the stop 190"),
        (G, "190", "is not above the stop 190"),
        (F, "191", "already has a raise for 2026-06-03"),
        (D, "189", "has no open position on 2026-06-01"),
    ]:
        with pytest.raises(LedgerError, match=message):
            ledger.record_stop_update(
                signal_id=signal.id, session=session_, new_us_stop=Decimal(stop)
            )
    ledger.record_exit_alert(signal_id=signal.id, as_of=G, reason="stop")
    with pytest.raises(LedgerError, match="has an open exit alert: no stop raise"):
        ledger.record_stop_update(signal_id=signal.id, session=G, new_us_stop=Decimal(195))
    unfilled = send_signal(ledger, E)
    assert unfilled.id is not None
    with pytest.raises(LedgerError, match="has no open position"):
        ledger.record_stop_update(signal_id=unfilled.id, session=F, new_us_stop=Decimal(195))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_levels.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.live.levels'`.

- [ ] **Step 3: Write the second layer and rebase `Ledger` on it**

Create `src/signalbench/live/levels.py`:

```python
"""Prices, US-equivalent levels, stops, and splits: the ledger's second layer (spec 04).

Decisions use the US levels, as in the backtest; the CDR levels are for display. A managed
position's stop is its signal's `us_stop` until `stop_updates` rows move it: a `trail` row only
raises it, and a `split` row rescales it. The tool never rescales the owner's fills or ACB: a
US split rescales only its own levels, and a CDR split multiplies the units held.
"""


from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlmodel import col, select

from signalbench.db.models import (
    CorporateAction,
    Price,
    StopUpdateRow,
    Ticker,
    TradeSignal,
)
from signalbench.live.book import LedgerBook, LedgerError, q4


@dataclass(frozen=True)
class StopLevels:
    """A managed position's stop: the US level decides, the CDR level is for display."""

    us: Decimal
    cdr: Decimal


@dataclass(frozen=True)
class SignalLevels:
    """A signal's prices on today's scale: US prices / the recorded US splits after its as_of,
    CDR prices / the recorded CDR splits after it. The stored row is never rewritten."""

    us_signal_close: Decimal
    us_stop: Decimal
    cdr_signal_close: Decimal
    cdr_stop: Decimal


def _split_step(ratio: Decimal, old: Decimal, new: Decimal) -> Decimal:
    """The factor a `split` row applies: 1/ratio, or ratio for the row that undoes a voided
    split (the row moves the level the other way)."""
    applies = (new < old) == (ratio > 1)
    return 1 / ratio if applies else ratio


class LedgerLevels(LedgerBook):
    """Prices, levels, stops, and splits, on top of the fills and cash of LedgerBook."""

    # --- Prices ----------------------------------------------------------------------------

    def _prices(self, ticker_id: UUID, through: date) -> list[Price]:
        return list(
            self._session.exec(
                select(Price)
                .where(Price.ticker_id == ticker_id, col(Price.date) <= through)
                .order_by(col(Price.date))
            ).all()
        )

    def _closes(self, cdr: Ticker, through: date) -> tuple[list[Price], dict[date, Decimal]]:
        """The CDR's stored bars and its US stock's raw closes by date, through `through`."""
        us = self._us_of(cdr)
        return self._prices(cdr.id, through), {
            p.date: p.close for p in self._prices(us.id, through)
        }

    @staticmethod
    def _traded(cdr_rows: Sequence[Price], us_closes: dict[date, Decimal]) -> Price | None:
        """The latest CDR bar with volume on a date the US stock has a bar too."""
        return next(
            (r for r in reversed(cdr_rows) if r.volume > 0 and r.date in us_closes), None
        )

    def cdr_ratio(self, cdr: Ticker, through: date) -> Decimal | None:
        """CDR close / US close on the latest date on or before `through` when the CDR traded
        (volume > 0): CAD per US dollar of the stock. A CDR that has never traded uses its
        latest close, over the US close on or before that date. None without prices."""
        cdr_rows, us_closes = self._closes(cdr, through)
        traded = self._traded(cdr_rows, us_closes)
        if traded is not None:
            return traded.close / us_closes[traded.date]
        before = [day for day in us_closes if cdr_rows and day <= cdr_rows[-1].date]
        return cdr_rows[-1].close / us_closes[max(before)] if before else None

    def cdr_mark(self, cdr: Ticker, as_of: date) -> Decimal | None:
        """The latest US close x (CDR close / US close) on the last date the CDR traded; the
        latest CDR close when it has never traded; None when no CDR price is stored (spec 04,
        Equity). This avoids stale marks on the many zero-volume days."""
        cdr_rows, us_closes = self._closes(cdr, as_of)
        traded = self._traded(cdr_rows, us_closes)
        if traded is None:
            return cdr_rows[-1].close if cdr_rows else None
        return us_closes[max(us_closes)] * traded.close / us_closes[traded.date]

    # --- Levels and stops ------------------------------------------------------------------

    def _factors(
        self, signal: TradeSignal, skip: Collection[int | None] = ()
    ) -> tuple[Decimal, Decimal]:
        """The products of the recorded US and of the recorded CDR split ratios with an ex-date
        after the signal's as_of, leaving out the actions in `skip`."""
        us = cdr = Decimal(1)
        for action in self._actions("us_split", us_symbol=signal.us_symbol):
            if action.ex_date > signal.as_of and action.id not in skip:
                us *= action.ratio
        for action in self._actions("cdr_split", cdr_id=signal.cdr_ticker_id):
            if action.ex_date > signal.as_of and action.id not in skip:
                cdr *= action.ratio
        return us, cdr

    def signal_levels(self, signal_id: int) -> SignalLevels:
        signal = self.signal(signal_id)
        us, cdr = self._factors(signal)
        return SignalLevels(
            us_signal_close=q4(signal.us_signal_close / us), us_stop=q4(signal.us_stop / us),
            cdr_signal_close=q4(signal.cdr_signal_close / cdr), cdr_stop=q4(signal.cdr_stop / cdr),
        )

    def entry_us(self, signal_id: int) -> Decimal | None:
        """us_signal_close x (the opening buy's price / cdr_signal_close), on today's US scale.
        Display only. None before the signal's position opens."""
        signal = self.signal(signal_id)
        opening = next(
            (e.opening for e in self._episodes(signal.cdr_ticker_id) if e.signal_id == signal.id),
            None,
        )
        if opening is None:
            return None
        ratio = opening.price_cad / signal.cdr_signal_close
        return q4(signal.us_signal_close * ratio / self._factors(signal)[0])

    def _stop_rows(self, signal_id: int) -> list[StopUpdateRow]:
        return list(
            self._session.exec(
                select(StopUpdateRow)
                .where(StopUpdateRow.signal_id == signal_id)
                .order_by(col(StopUpdateRow.id))
            ).all()
        )

    def stop_in_force(self, signal_id: int, session: date) -> StopLevels:
        """The stop in force during `session`, on today's scale: raises from earlier sessions
        count; a raise decided at `session`'s own close applies from the next one.

        The rows are replayed in the order they were written. A `split` row rescales the
        running stop by its split's ratio (or undoes it, for a voided split), and a recorded
        split with no row for this signal (recorded before its position opened) rescales the
        signal's initial stop."""
        signal = self.signal(signal_id)
        rows = self._stop_rows(signal_id)
        us_factor, cdr_factor = self._factors(signal, {row.corporate_action_id for row in rows})
        us, cdr = q4(signal.us_stop / us_factor), q4(signal.cdr_stop / cdr_factor)
        for row in rows:
            if row.reason == "trail":
                if row.session < session:
                    us, cdr = row.new_us_stop, row.new_cdr_stop
                continue
            action = self._session.get(CorporateAction, row.corporate_action_id)
            if action is None:
                continue
            if action.kind == "us_split":
                us = q4(us * _split_step(action.ratio, row.old_us_stop, row.new_us_stop))
            else:
                cdr = q4(cdr * _split_step(action.ratio, row.old_cdr_stop, row.new_cdr_stop))
        return StopLevels(us, cdr)

    def current_stop(self, signal_id: int) -> StopLevels:
        """The stop after every recorded raise and split: in force from the next session."""
        return self.stop_in_force(signal_id, date.max)

    def record_stop_update(
        self, *, signal_id: int, session: date, new_us_stop: Decimal, late: bool = False
    ) -> StopUpdateRow:
        """A `trail` raise decided at `session`'s close, in force from the next session. The
        CDR display stop is new_us_stop x the last traded ratio on or before `session`.
        (`split` rows are written by record_split and void_corporate_action.)"""
        signal = self.signal(signal_id)
        episode = self._open_episode(signal)
        if episode is None or episode.opening.trade_date > session:
            raise LedgerError(f"signal {signal_id} has no open position on {session}")
        if self.open_exit_alert(signal_id) is not None:
            raise LedgerError(f"signal {signal_id} has an open exit alert: no stop raise")
        old = self.stop_in_force(signal_id, session)
        new_us = q4(new_us_stop)
        if new_us <= old.us:
            raise LedgerError(
                f"a trailing stop only rises: {new_us} is not above the stop {old.us}"
            )
        taken = self._session.exec(
            select(StopUpdateRow).where(
                StopUpdateRow.signal_id == signal_id, StopUpdateRow.session == session,
                StopUpdateRow.reason == "trail",
            )
        ).first()
        if taken is not None:
            raise LedgerError(f"signal {signal_id} already has a raise for {session}")
        ratio = self.cdr_ratio(self._by_id(signal.cdr_ticker_id), session)
        new_cdr = old.cdr if ratio is None else q4(new_us * ratio)
        row = StopUpdateRow(
            signal_id=signal_id, session=session, reason="trail", old_us_stop=old.us,
            new_us_stop=new_us, old_cdr_stop=old.cdr, new_cdr_stop=new_cdr, late=late,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row
```

In `src/signalbench/live/ledger.py`, replace:

```python
from signalbench.live.book import LedgerBook, LedgerError
```

with:

```python
from signalbench.live.book import LedgerError
from signalbench.live.levels import LedgerLevels
```

In `src/signalbench/live/ledger.py`, replace:

```python
class Ledger(LedgerBook):
```

with:

```python
class Ledger(LedgerLevels):
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_levels.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 5 passed; the full suite 660 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/levels.py src/signalbench/live/ledger.py tests/live_helpers.py tests/test_live_levels.py
git commit -m "feat: live CDR marks, US-equivalent levels, and trailing-stop rows

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Splits: the hand-checked CDR split, US splits, voids, and the scale check

**Files:**
- Modify: `src/signalbench/live/levels.py`
- Create: `tests/test_live_splits.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_splits.py`:

```python
"""Spec 04 splits: the hand-checked CDR split, US splits on the tool's levels, voids, the
withdrawal of a sent signal, and the scale check."""

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session, col, select

from live_helpers import add_pair, add_prices, load_example, make_ledger, send_signal
from signalbench.db.models import Price, StopUpdateRow, TradeSignal
from signalbench.live.ledger import Ledger, LedgerError
from signalbench.live.levels import SignalLevels, StopLevels

D, E, F, G = date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3), date(2026, 6, 4)
EX = date(2026, 6, 8)  # a Monday ex-date


@pytest.fixture
def ledger(session: Session) -> Ledger:
    add_pair(session)
    add_pair(session, "TST", "ZTST")
    ledger = make_ledger(session)
    ledger.record_cash(Decimal("1000.00"), date(2026, 3, 2), "deposit")
    return ledger


def _split(ledger: Ledger, kind: str, symbol: str, ratio: str, ex: date = EX) -> int:
    action = ledger.record_split(kind=kind, symbol=symbol, ex_date=ex, ratio=Decimal(ratio),
                                 source="owner")
    assert action.id is not None
    return action.id


def _fill(ledger: Ledger, side: str, quantity: str, price: str, day: date,
          cdr: str = "ZTST", **links: int) -> None:
    ledger.record_fill(cdr_symbol=cdr, side=side, quantity=Decimal(quantity),
                       price_cad=Decimal(price), trade_date=day, **links)


def _open(ledger: Ledger, units: str = "2") -> TradeSignal:
    signal = send_signal(ledger, D)  # US 200 / stop 188, CDR 40 / stop 37.60
    assert signal.id is not None
    _fill(ledger, "buy", units, "40.20", E, cdr="ZNVD", signal_id=signal.id)
    return signal


def test_the_hand_checked_cdr_split_through_the_ledger(ledger: Ledger) -> None:
    example = load_example("cdr_split")  # buy 3 @ 30, 2-for-1, sell 4 @ 16
    buy, split, sell = example["events"]
    cash = ledger.cash()
    _fill(ledger, "buy", buy["quantity"], buy["price"], buy["date"])
    action = _split(ledger, "cdr_split", "ZTST", split["split"], split["date"])
    book = ledger.books()["ZTST"]
    assert (book.units, book.acb, book.per_unit) == (Decimal(6), Decimal("90.00"), Decimal(15))
    assert ledger.cash() == cash - Decimal("90.00")  # the split moves no cash
    ledger.void_corporate_action(action, "recorded by mistake")
    assert ledger.books()["ZTST"].units == Decimal(3)  # the void restores the pre-split units
    second = _split(ledger, "cdr_split", "ZTST", split["split"], split["date"])
    with pytest.raises(LedgerError, match="a sale of 7 on 2026-03-16 is more than the 6 units"):
        _fill(ledger, "sell", "7", sell["price"], sell["date"])
    _fill(ledger, "sell", sell["quantity"], sell["price"], sell["date"])
    book = ledger.books()["ZTST"]
    [disposition] = book.dispositions
    assert (disposition.acb, disposition.gain) == (Decimal("60.00"), Decimal("4.00"))
    assert (book.units, book.acb) == (Decimal(2), Decimal("30.00"))
    assert ledger.cash() == cash - Decimal("90.00") + Decimal("64.00")
    with pytest.raises(LedgerError, match="voiding corporate action"):  # 4 sold of 3 held
        ledger.void_corporate_action(second, "no")


def test_a_bad_split_is_refused(ledger: Ledger) -> None:
    for kind, symbol, ratio, message in [
        ("us_split", "NVDA", "1", "above 0 and not 1"),
        ("us_split", "NVDA", "-2", "above 0 and not 1"),
        ("us_split", "NVDA", "1.0000001", "more than 6 decimals"),
        ("us_split", "ZNVD", "2", "ZNVD is not a known US stock"),
        ("cdr_split", "ZTST", "2", "no open position or sent signal on 2026-06-08"),
        ("reverse", "NVDA", "2", "kind must be one of us_split, cdr_split"),
    ]:
        with pytest.raises(LedgerError, match=message):
            _split(ledger, kind, symbol, ratio)
    _split(ledger, "us_split", "NVDA", "2")
    with pytest.raises(LedgerError, match="NVDA us_split on 2026-06-08 is already recorded"):
        _split(ledger, "us_split", "NVDA", "2")


def test_a_us_split_rescales_the_tool_s_levels_and_never_r(ledger: Ledger, session: Session) -> None:
    signal = _open(ledger)
    assert signal.id is not None
    ledger.record_stop_update(signal_id=signal.id, session=F, new_us_stop=Decimal(190))
    action = _split(ledger, "us_split", "NVDA", "2")
    assert ledger.signal_levels(signal.id) == SignalLevels(
        Decimal(100), Decimal(94), Decimal(40), Decimal("37.60")
    )
    assert ledger.entry_us(signal.id) == Decimal("100.50")  # 201.00 / 2
    assert ledger.current_stop(signal.id).us == Decimal(95)  # the raised 190 / 2
    [row] = session.exec(select(StopUpdateRow).where(StopUpdateRow.reason == "split")).all()
    assert (row.session, row.old_us_stop, row.new_us_stop, row.corporate_action_id) == (
        EX, Decimal(190), Decimal(95), action
    )
    assert row.old_cdr_stop == row.new_cdr_stop  # a US split leaves the CDR stop alone
    # A forced rescan of F sees the stop in force during F: the initial 188, on today's scale.
    assert ledger.stop_in_force(signal.id, F).us == Decimal(94)
    _fill(ledger, "sell", "2", "43.00", date(2026, 6, 9), cdr="ZNVD")
    [trade] = ledger.closed_trades()
    assert trade.r == Decimal("5.60") / Decimal("4.80")  # the R of the unsplit fixture trade


def test_voiding_a_us_split_writes_a_row_that_undoes_it(ledger: Ledger, session: Session) -> None:
    signal = _open(ledger)
    assert signal.id is not None
    action = _split(ledger, "us_split", "NVDA", "2")
    assert ledger.current_stop(signal.id).us == Decimal(94)
    ledger.void_corporate_action(action, "yfinance listed a split that did not happen")
    assert ledger.current_stop(signal.id) == StopLevels(Decimal(188), Decimal("37.60"))
    assert ledger.signal_levels(signal.id).us_signal_close == Decimal(200)
    rows = session.exec(select(StopUpdateRow).order_by(col(StopUpdateRow.id))).all()
    assert [(r.old_us_stop, r.new_us_stop) for r in rows] == [
        (Decimal(188), Decimal(94)), (Decimal(94), Decimal(188))
    ]
    with pytest.raises(LedgerError, match=f"no corporate action {action} to void"):
        ledger.void_corporate_action(action, "twice")


def test_a_split_recorded_before_the_position_opened_still_rescales_its_stop(
    ledger: Ledger, session: Session
) -> None:
    signal = send_signal(ledger, D)
    assert signal.id is not None
    _split(ledger, "us_split", "NVDA", "4", E)  # while the signal was only sent
    _fill(ledger, "buy", "1", "40.20", E, cdr="ZNVD", signal_id=signal.id)
    assert ledger.current_stop(signal.id).us == Decimal(47)  # 188 / 4, with no split row
    assert session.exec(select(StopUpdateRow)).all() == []


def test_a_cdr_split_multiplies_units_rescales_the_cdr_stop_and_keeps_r(
    ledger: Ledger, session: Session
) -> None:
    signal = _open(ledger)
    assert signal.id is not None
    _split(ledger, "cdr_split", "ZNVD", "2")
    assert ledger.books()["ZNVD"].units == Decimal(4)
    assert ledger.current_stop(signal.id) == StopLevels(Decimal(188), Decimal("18.80"))
    assert ledger.signal_levels(signal.id).cdr_signal_close == Decimal(20)
    _fill(ledger, "sell", "4", "21.50", date(2026, 6, 9), cdr="ZNVD")  # post-split units
    [trade] = ledger.closed_trades()
    assert (trade.pnl, trade.r) == (Decimal("5.60"), Decimal("5.60") / Decimal("4.80"))


def test_a_cdr_split_withdraws_a_sent_signal_from_before_its_ex_date(ledger: Ledger) -> None:
    signal = send_signal(ledger, date(2026, 6, 5))
    assert signal.id is not None
    _split(ledger, "cdr_split", "ZNVD", "2")  # allowed: a sent signal, no position
    assert ledger.signal(signal.id).status == "withdrawn"


def _dividend(session: Session, symbol_id: object, day: date, adj: str) -> None:
    [row] = session.exec(select(Price).where(Price.ticker_id == symbol_id, Price.date == day)).all()
    row.adj_close = Decimal(adj)
    session.add(row)
    session.commit()


def test_the_scale_check_holds_an_unrecorded_split_and_passes_dividends(session: Session) -> None:
    stock, receipt = add_pair(session)
    add_prices(session, stock, [D, E], [200, 201])
    add_prices(session, receipt, [D, E], [40, 40.2])
    ledger = make_ledger(session)
    signal = send_signal(ledger, D)
    assert signal.id is not None
    assert ledger.scale_check(signal.id) is None
    _dividend(session, stock.id, D, "198.50")  # a later dividend lowers the adjusted close only
    assert ledger.scale_check(signal.id) is None
    for row in session.exec(select(Price).where(Price.ticker_id == stock.id)).all():
        row.close = row.close / 2  # yfinance rescaled the history for a split we have not recorded
        session.add(row)
    session.commit()
    problem = ledger.scale_check(signal.id)
    assert problem is not None and "the stored US close 100.0000 on 2026-06-01" in problem
    ledger.record_split(kind="us_split", symbol="NVDA", ex_date=E, ratio=Decimal(2),
                        source="yfinance")
    assert ledger.scale_check(signal.id) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_splits.py -q`
Expected: 8 failed: `AttributeError: 'Ledger' object has no attribute 'record_split'` (7) and `… 'scale_check'` (1).

- [ ] **Step 3: Add the splits**

In `src/signalbench/live/levels.py`, replace:

```python
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlmodel import col, select

from signalbench.db.models import (
    CorporateAction,
    Price,
    StopUpdateRow,
    Ticker,
    TradeSignal,
)
from signalbench.live.book import LedgerBook, LedgerError, q4
```

with:

```python
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import get_args
from uuid import UUID

from sqlmodel import col, select

from signalbench.db.models import (
    CorporateAction,
    Price,
    StopUpdateRow,
    Ticker,
    TickerKind,
    TradeSignal,
)
from signalbench.live.book import (
    LedgerBook,
    LedgerError,
    SplitKind,
    SplitSource,
    check_choice,
    check_places,
    q4,
)
from signalbench.paper.splits import MARK_TOLERANCE

TOLERANCE = Decimal(str(MARK_TOLERANCE))  # spec 07's 3%: a larger gap is an unrecorded split
```

In `src/signalbench/live/levels.py`, replace:

```python
class LedgerLevels(LedgerBook):
```

with:

```python
def _split_row(
    signal_id: int, action: CorporateAction, old: StopLevels, new: StopLevels
) -> StopUpdateRow:
    return StopUpdateRow(
        signal_id=signal_id, session=action.ex_date, reason="split", old_us_stop=old.us,
        new_us_stop=new.us, old_cdr_stop=old.cdr, new_cdr_stop=new.cdr,
        corporate_action_id=action.id,
    )


class LedgerLevels(LedgerBook):
```

Append to `src/signalbench/live/levels.py` (the end of `LedgerLevels`):

```python
    # --- Splits ----------------------------------------------------------------------------

    def scale_check(self, signal_id: int) -> str | None:
        """Why a signal's prices and the stored closes of its as_of disagree by more than 3%
        once the recorded splits are applied (an unrecorded split or bad data), or None.
        Compares the raw close (split-adjusted, not dividend-adjusted), so dividends pass."""
        signal = self.signal(signal_id)
        levels = self.signal_levels(signal_id)
        us = self._ticker(signal.us_symbol, TickerKind.us_stock)
        checks = [
            ("US", us.id, levels.us_signal_close, True),
            ("CDR", signal.cdr_ticker_id, levels.cdr_signal_close, False),
        ]
        for name, ticker_id, expected, exact_day in checks:
            rows = self._prices(ticker_id, signal.as_of)
            if exact_day:
                rows = [r for r in rows if r.date == signal.as_of]
            if not rows:
                return f"no stored {name} close for {signal.as_of}"
            stored = rows[-1].close
            if abs(stored / expected - 1) > TOLERANCE:
                return (
                    f"the stored {name} close {stored} on {rows[-1].date} is "
                    f"{stored / expected:.4f}x the signal's {expected} after recorded splits"
                )
        return None

    def record_split(
        self, *, kind: SplitKind, symbol: str, ex_date: date, ratio: Decimal, source: SplitSource
    ) -> CorporateAction:
        """A US split (the tool's US levels rescale) or a CDR split (units x ratio, the total
        ACB unchanged). Writes a `split` stop row for each open managed position it touches,
        and a CDR split withdraws the CDR's `sent` signals from before its ex-date."""
        check_choice(kind, get_args(SplitKind), "kind")
        check_choice(source, get_args(SplitSource), "source")
        if ratio <= 0 or ratio == 1:
            raise LedgerError(f"a split ratio must be above 0 and not 1, not {ratio}")
        check_places(ratio, 6, "ratio")
        if kind == "us_split":
            us_symbol, cdr_id = self._ticker(symbol, TickerKind.us_stock).symbol, None
            signals = self._session.exec(
                select(TradeSignal).where(TradeSignal.us_symbol == us_symbol)
            ).all()
        else:
            cdr = self._cdr(symbol)
            us_symbol, cdr_id = None, cdr.id
            signals = self._session.exec(
                select(TradeSignal).where(TradeSignal.cdr_ticker_id == cdr.id)
            ).all()
            held = self._book(
                self._fills(cdr.id, ex_date - timedelta(days=1)),
                [s for s in self._actions("cdr_split", cdr_id=cdr.id) if s.ex_date < ex_date],
            ).units
            sent = [s for s in signals if s.status == "sent" and s.as_of < ex_date]
            if held == 0 and not sent:
                raise LedgerError(
                    f"{symbol} had no open position or sent signal on {ex_date}: "
                    "a CDR split only matters for those"
                )
        duplicate = self._session.exec(
            select(CorporateAction).where(
                CorporateAction.kind == kind, CorporateAction.us_symbol == us_symbol,
                CorporateAction.cdr_ticker_id == cdr_id, CorporateAction.ex_date == ex_date,
                col(CorporateAction.voided).is_(False),
            )
        ).first()
        if duplicate is not None:
            raise LedgerError(f"{symbol} {kind} on {ex_date} is already recorded ({duplicate.id})")
        affected = [
            (s, self.current_stop(s.id))
            for s in signals
            if s.id is not None and s.as_of < ex_date and self._open_episode(s) is not None
        ]
        action = CorporateAction(
            kind=kind, us_symbol=us_symbol, cdr_ticker_id=cdr_id, ex_date=ex_date,
            ratio=ratio, source=source,
        )
        self._session.add(action)
        self._session.flush()
        if cdr_id is not None:
            try:
                self._check_book(self._by_id(cdr_id))
            except LedgerError:
                self._session.rollback()
                raise
            for signal in signals:
                if signal.status == "sent" and signal.as_of < ex_date:
                    signal.status = "withdrawn"
                    self._session.add(signal)
        for signal, old in affected:
            assert signal.id is not None
            new = StopLevels(
                q4(old.us / ratio) if kind == "us_split" else old.us,
                q4(old.cdr / ratio) if kind == "cdr_split" else old.cdr,
            )
            self._session.add(_split_row(signal.id, action, old, new))
        self._session.commit()
        self._session.refresh(action)
        return action

    def void_corporate_action(self, action_id: int, reason: str) -> None:
        """Void a wrong split. Its effects are recomputed, and each `split` stop row written
        from it gets a new `split` row that undoes it. Withdrawn signals stay withdrawn."""
        action = self._session.get(CorporateAction, action_id)
        if action is None or action.voided:
            raise LedgerError(f"no corporate action {action_id} to void")
        if not reason.strip():
            raise LedgerError("a void needs a reason")
        undo: list[tuple[int, StopLevels]] = []
        rows = self._session.exec(
            select(StopUpdateRow).where(StopUpdateRow.corporate_action_id == action_id)
        ).all()
        for signal_id in sorted({row.signal_id for row in rows}):
            if sum(1 for row in rows if row.signal_id == signal_id) % 2 == 1:
                undo.append((signal_id, self.current_stop(signal_id)))
        action.voided = True
        action.void_reason = reason.strip()
        self._session.add(action)
        self._session.flush()
        if action.cdr_ticker_id is not None:
            try:
                self._check_book(self._by_id(action.cdr_ticker_id))
            except LedgerError as error:
                self._session.rollback()
                raise LedgerError(f"voiding corporate action {action_id}: {error}") from None
        for signal_id, old in undo:
            new = StopLevels(
                q4(old.us * action.ratio) if action.kind == "us_split" else old.us,
                q4(old.cdr * action.ratio) if action.kind == "cdr_split" else old.cdr,
            )
            self._session.add(_split_row(signal_id, action, old, new))
        self._session.commit()
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_splits.py tests/test_live_levels.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 13 passed (8 new, and the 5 of Task 5); the full suite 668 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/levels.py tests/test_live_splits.py
git commit -m "feat: live US and CDR splits, their voids, and the scale check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Positions, equity, the peak, the pause, and the `PortfolioState` for `decide()`

**Files:**
- Replace: `src/signalbench/live/ledger.py`
- Modify: `tests/fixtures/live_ledger_examples.yaml`
- Create: `tests/test_live_equity.py`

The last test is the spec's gate check: on fixture data, positions and cash rebuilt from fills and corporate actions alone match the expected snapshot exactly.

- [ ] **Step 1: Write the rebuild example and the failing tests**

Append to `tests/fixtures/live_ledger_examples.yaml`:

```yaml
rebuild:  # spec 04 gate: positions and cash rebuilt from fills and corporate actions alone
  cash:
    - {date: 2026-05-01, amount: '500.00'}
    - {date: 2026-06-15, amount: '-50.00'}
  fills:  # in the order recorded; `void` voids the fill after it is recorded
    - {date: 2026-05-04, symbol: ZAAA, side: buy, quantity: '4', price: '25.00'}
    - {date: 2026-05-04, symbol: ZBBB, side: buy, quantity: '10', price: '12.00'}
    - {date: 2026-05-05, symbol: ZBBB, side: buy, quantity: '10', price: '12.50', void: wrong fill}
    - {date: 2026-05-05, symbol: ZBBB, side: buy, quantity: '2.5', price: '12.40'}
    - {date: 2026-05-20, symbol: ZAAA, side: sell, quantity: '1', price: '27.00'}
  splits:
    - {date: 2026-06-01, symbol: ZBBB, ratio: '3'}
  after_splits:
    - {date: 2026-06-10, symbol: ZBBB, side: sell, quantity: '7.5', price: '4.30'}
  expect:
    # 500 - 100 - 120 - 31.00 + 27.00 + 32.25 - 50 (the voided 125.00 counts nowhere)
    cash: '258.25'
    positions:
      ZAAA: {units: '3', acb: '75.00'}  # 100 x 3/4
      # 120 + 31 = 151.00 for 12.5 units; x3 = 37.5 units; sold 151 x 7.5/37.5 = 30.20
      ZBBB: {units: '30', acb: '120.80'}
```

Create `tests/test_live_equity.py`:

```python
"""Spec 04: positions (managed and manual), equity, the peak, the pause, and what decide() sees.
The gate's rebuild of positions and cash from fills and corporate actions alone is here too."""

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from live_helpers import add_pair, add_prices, load_example, make_ledger, send_signal
from signalbench.db.models import EquitySnapshot, LiveRiskState
from signalbench.live.ledger import Ledger, LedgerError
from signalbench.live.levels import StopLevels
from strategy_helpers import weekdays

DAYS = weekdays(date(2026, 6, 1), 16)  # 2026-06-01 (Monday) to 2026-06-22
D, E, F, G, H = DAYS[:5]
US = [200, 201, 204, 202, 160, 204] + [205] * 10  # a fall to 160 on H, then a recovery
CDR = [40, 40.2, 40.8, 40.4] + [40.4] * 12
CDR_VOLUME = [100, 100, 100] + [0] * 13  # traded up to F: the ratio stays 40.8 / 204 = 0.2
DRAWDOWN = Decimal("0.15")


@pytest.fixture
def ledger(session: Session) -> Ledger:
    stock, receipt = add_pair(session)
    add_prices(session, stock, DAYS, US)
    add_prices(session, receipt, DAYS, CDR, volumes=CDR_VOLUME)
    other, other_cdr = add_pair(session, "XOM", "ZXOM", "Energy")
    add_prices(session, other, DAYS, [100] * 16)
    add_prices(session, other_cdr, DAYS, [10] * 16)
    ledger = make_ledger(session, today=DAYS[-1])
    ledger.record_cash(Decimal("100.00"), D, "deposit")
    return ledger


def _managed(ledger: Ledger) -> int:
    signal = send_signal(ledger, D)  # US 200 / stop 188, CDR 40 / stop 37.60
    assert signal.id is not None
    ledger.record_fill(cdr_symbol="ZNVD", side="buy", quantity=Decimal(2),
                       price_cad=Decimal("40.20"), trade_date=E, signal_id=signal.id)
    return signal.id


def test_a_managed_position_carries_its_levels_and_history(ledger: Ledger) -> None:
    signal_id = _managed(ledger)
    [position] = ledger.positions(G)
    assert (position.cdr_symbol, position.us_symbol, position.units, position.acb) == (
        "ZNVD", "NVDA", Decimal(2), Decimal("80.40")
    )
    assert position.mark == Decimal("40.40")  # 202 x 0.2
    assert (position.entry_session, position.sessions_held) == (E, 3)
    assert (position.stop, position.entry_us, position.highest_close) == (
        StopLevels(Decimal(188), Decimal("37.60")), Decimal("201.00"), 204.0
    )
    strategy = position.strategy_position()
    assert (strategy.id, strategy.symbol, strategy.setup, strategy.stop, strategy.entry_price) == (
        str(signal_id), "NVDA", "breakout", 188.0, 201.0
    )
    assert (strategy.target, strategy.time_limit, strategy.sessions_held) == (None, None, 3)
    assert ledger.positions(D) == []  # bought on E


def test_a_manual_position_has_no_stop_but_holds_a_slot_and_its_sector(ledger: Ledger) -> None:
    fill = ledger.record_fill(cdr_symbol="ZXOM", side="buy", quantity=Decimal(3),
                              price_cad=Decimal(10), trade_date=date(2026, 6, 6))  # a Saturday
    [position] = ledger.positions(DAYS[5])
    assert (position.managed, position.stop, position.entry_us) == (False, None, None)
    assert position.entry_session == date(2026, 6, 8)  # the first NYSE session on or after it
    strategy = position.strategy_position()
    assert (strategy.id, strategy.stop, strategy.entry_price, strategy.highest_close) == (
        f"manual-{fill.id}", 0.0, 0.0, 0.0
    )
    state = ledger.portfolio_state(DAYS[5])
    assert (state.slots_used(), state.sector_count("Energy")) == (1, 1)


def test_equity_is_cash_plus_units_at_the_cdr_mark(ledger: Ledger) -> None:
    _managed(ledger)
    snapshot = ledger.equity(G)
    assert (snapshot.cash, snapshot.positions_value, snapshot.equity, snapshot.peak) == (
        Decimal("19.60"), Decimal("80.80"), Decimal("100.40"), Decimal("100.40")
    )


def test_a_drawdown_pauses_until_resume_whatever_auto_resume_sessions_says(
    ledger: Ledger, session: Session
) -> None:
    _managed(ledger)
    paused_on = []
    for day in DAYS:
        snapshot, paused_now = ledger.record_equity(day, pause_drawdown=DRAWDOWN)
        if paused_now:
            paused_on.append((day, snapshot.equity, snapshot.peak))
    # H: 19.60 + 2 x (160 x 0.2) = 83.60 < 0.85 x the peak 101.20 (F: 19.60 + 2 x 40.80)
    assert paused_on == [(H, Decimal("83.60"), Decimal("101.20"))]
    risk = ledger.risk_state()
    assert (risk.paused, risk.paused_at) == (True, H)  # 11 recovered sessions later: still paused
    assert ledger.portfolio_state(DAYS[-1]).paused
    assert len(session.exec(select(EquitySnapshot)).all()) == 16
    ledger.resume()
    risk = ledger.risk_state()
    assert (risk.paused, risk.peak_reset_on) == (False, DAYS[-1])
    snapshot, paused_now = ledger.record_equity(DAYS[-1], pause_drawdown=DRAWDOWN)
    assert (snapshot.peak, paused_now) == (snapshot.equity, False)  # the peak starts again
    with pytest.raises(LedgerError, match="not paused"):
        ledger.resume()


def test_a_rerun_of_a_night_replaces_its_snapshot(ledger: Ledger, session: Session) -> None:
    ledger.record_equity(D, pause_drawdown=DRAWDOWN)
    ledger.record_cash(Decimal("5.00"), D, "a late deposit")
    ledger.record_equity(D, pause_drawdown=DRAWDOWN)
    [snapshot] = session.exec(select(EquitySnapshot)).all()
    assert snapshot.equity == Decimal("105.00")
    assert session.get(LiveRiskState, 1) is not None


def test_decide_sees_positions_pending_signals_cash_and_the_pause(ledger: Ledger) -> None:
    _managed(ledger)
    pending = send_signal(ledger, F, us="XOM", cdr="ZXOM", us_close="100", us_stop="95",
                          cdr_close="10", units="3")
    state = ledger.portfolio_state(G)  # the XOM signal from F expires at G's close
    assert [p.symbol for p in state.positions] == ["NVDA"]
    assert [(p.symbol, p.sector, p.planned_cost) for p in state.pending] == [
        ("XOM", "Energy", 30.0)
    ]
    assert (state.slots_used(), state.cash, state.equity, state.paused) == (2, 19.6, 100.4, False)
    assert pending.id is not None
    ledger.mark_signal(pending.id, "expired")
    assert ledger.portfolio_state(G).slots_used() == 1


def test_positions_and_cash_rebuild_from_fills_and_corporate_actions_alone(
    session: Session,
) -> None:
    example = load_example("rebuild")
    add_pair(session, "AAA", "ZAAA")
    add_pair(session, "BBB", "ZBBB")
    ledger = make_ledger(session)
    for movement in example["cash"]:
        ledger.record_cash(Decimal(movement["amount"]), movement["date"], "movement")

    def fill(row: dict[str, str]) -> None:
        made = ledger.record_fill(
            cdr_symbol=row["symbol"], side=row["side"], quantity=Decimal(row["quantity"]),
            price_cad=Decimal(row["price"]), trade_date=row["date"],
        )
        if "void" in row:
            assert made.id is not None
            ledger.void_fill(made.id, row["void"])

    for row in example["fills"]:
        fill(row)
    for split in example["splits"]:
        ledger.record_split(kind="cdr_split", symbol=split["symbol"], ex_date=split["date"],
                            ratio=Decimal(split["ratio"]), source="owner")
    for row in example["after_splits"]:
        fill(row)
    expect = example["expect"]
    assert ledger.cash() == Decimal(expect["cash"])
    books = ledger.books()
    assert {symbol: (book.units, book.acb) for symbol, book in books.items()} == {
        symbol: (Decimal(p["units"]), Decimal(p["acb"])) for symbol, p in expect["positions"].items()
    }
    assert [p.cdr_symbol for p in ledger.positions(date(2026, 6, 30))] == ["ZAAA", "ZBBB"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_equity.py -q`
Expected: 7 failed, `AttributeError`: `Ledger` has no `positions`, `equity`, `record_equity`, or `portfolio_state` yet.

- [ ] **Step 3: Write the top layer**

Replace the whole of `src/signalbench/live/ledger.py` with:

```python
"""The ledger (spec 04, Interface): the one class the scan, the bot, and the CLI use.

Built in layers: live/book.py (fills, cash, voids, signals, exit alerts), then live/levels.py
(prices, stops, splits), then this module (positions, equity, the pause, the tax report, and
the scale-up check).
"""


from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlmodel import col, select

from signalbench.db.models import EquitySnapshot, LiveRiskState, TickerKind, utcnow
from signalbench.live.book import ZERO, Episode, LedgerError, cad, q4
from signalbench.live.levels import LedgerLevels, StopLevels
from signalbench.strategy.portfolio import PendingEntry, PortfolioState, Position


@dataclass(frozen=True)
class LivePosition:
    """An open position at one session's close."""

    episode: Episode
    cdr_symbol: str
    us_symbol: str
    sector: str
    units: Decimal  # open CDR units, post-split
    acb: Decimal  # total ACB
    mark: Decimal | None  # the CDR mark; None when no CDR price is stored
    entry_session: date  # the first NYSE session on or after the opening buy's trade date
    sessions_held: int  # the entry session counts as 1
    stop: StopLevels | None  # managed: the stop in force during this session; manual: None
    entry_us: Decimal | None  # managed: the US-equivalent entry (display only)
    highest_close: float  # managed: highest stored adjusted US close since the entry session

    @property
    def managed(self) -> bool:
        return self.episode.managed

    @property
    def value(self) -> Decimal:
        """Units x the CDR mark, or the ACB when no CDR price is stored."""
        return self.acb if self.mark is None else self.units * self.mark

    def strategy_position(self) -> Position:
        """What decide() sees. A manual position has stop 0: it holds a slot and counts toward
        its sector, and whatever decide() returns for it is discarded (live/review.py)."""
        entry = stop = highest = 0.0
        if self.stop is not None and self.entry_us is not None:
            entry, stop, highest = float(self.entry_us), float(self.stop.us), self.highest_close
        return Position(
            id=self.episode.key,
            symbol=self.us_symbol,
            setup="breakout",
            sector=self.sector,
            units=float(self.units),
            entry_price=entry,
            entry_date=self.entry_session,
            stop=stop,
            target=None,
            time_limit=None,
            sessions_held=self.sessions_held,
            highest_close=highest,
        )


class Ledger(LedgerLevels):
    """Everything derived from what the owner did on Wealthsimple."""

    # --- Positions -------------------------------------------------------------------------

    def positions(self, as_of: date) -> list[LivePosition]:
        """Every position open at the close of `as_of` (fills and splits dated on or before it),
        managed and manual, by CDR symbol."""
        found: list[LivePosition] = []
        for cdr_id in self._held_cdr_ids():
            episodes_ = self._episodes(cdr_id, as_of)
            if not episodes_ or episodes_[-1].closed_on is not None:
                continue
            found.append(self._position(episodes_[-1], as_of))
        return sorted(found, key=lambda p: p.cdr_symbol)

    def _position(self, episode: Episode, as_of: date) -> LivePosition:
        cdr = self._by_id(episode.cdr_ticker_id)
        us = self._us_of(cdr)
        fills = self._fills(cdr.id, as_of)
        splits = [s for s in self._actions("cdr_split", cdr_id=cdr.id) if s.ex_date <= as_of]
        book = self._book(fills, splits)
        entry = self._entry_session(episode.opening.trade_date)
        stop = entry_us = None
        highest = 0.0
        if episode.signal_id is not None:
            stop = self.stop_in_force(episode.signal_id, as_of)
            entry_us = self.entry_us(episode.signal_id)
            closes = [
                float(p.adj_close) for p in self._prices(us.id, as_of) if p.date >= entry
            ]
            highest = max(closes, default=0.0)
        return LivePosition(
            episode=episode, cdr_symbol=cdr.symbol, us_symbol=us.symbol,
            sector=us.sector or "Unknown", units=book.units, acb=book.acb,
            mark=self.cdr_mark(cdr, as_of), entry_session=entry,
            sessions_held=len(self._calendar.sessions_between(entry, as_of)),
            stop=stop, entry_us=entry_us, highest_close=highest,
        )

    def _entry_session(self, trade_date: date) -> date:
        """The first NYSE session on or after the opening buy's trade date."""
        if self._calendar.is_session(trade_date):
            return trade_date
        return self._calendar.next_sessions(trade_date, 1)[0]

    # --- Equity, peak, and the pause -------------------------------------------------------

    def risk_state(self) -> LiveRiskState:
        risk = self._session.get(LiveRiskState, 1)
        if risk is None:
            risk = LiveRiskState()
            self._session.add(risk)
            self._session.commit()
            self._session.refresh(risk)
        return risk

    def equity(self, as_of: date) -> EquitySnapshot:
        """Equity at the close of `as_of`, not saved. Peak = the max equity since
        risk_state.peak_reset_on, this one included."""
        cash = self.cash(as_of)
        value = sum((p.value for p in self.positions(as_of)), ZERO)
        equity = q4(cash + value)
        reset = self.risk_state().peak_reset_on
        query = select(col(EquitySnapshot.equity)).where(col(EquitySnapshot.date) < as_of)
        if reset is not None:
            query = query.where(col(EquitySnapshot.date) >= reset)
        peak = max([equity, *self._session.exec(query).all()])
        return EquitySnapshot(
            date=as_of, cash=q4(cash), positions_value=q4(value), equity=equity, peak=peak
        )

    def record_equity(
        self, as_of: date, *, pause_drawdown: Decimal
    ) -> tuple[EquitySnapshot, bool]:
        """Write the night's snapshot (replacing one for the same date) and pause new entries
        when equity < (1 - pause_drawdown) x peak. Returns the snapshot and whether this call
        paused. Only resume() clears a pause; the backtest's auto_resume_sessions is ignored."""
        snapshot = self.equity(as_of)
        self._session.merge(snapshot)
        risk = self.risk_state()
        paused_now = not risk.paused and snapshot.equity < (1 - pause_drawdown) * snapshot.peak
        if paused_now:
            risk.paused = True
            risk.paused_at = as_of
            risk.paused_reason = (
                f"equity {cad(snapshot.equity)} is below {1 - pause_drawdown} x the peak "
                f"{cad(snapshot.peak)}"
            )
            self._session.add(risk)
        self._session.commit()
        return snapshot, paused_now

    def resume(self) -> LiveRiskState:
        """/resume: clear the pause; the peak counts again from today."""
        risk = self.risk_state()
        if not risk.paused:
            raise LedgerError("new entries are not paused")
        risk.paused = False
        risk.resumed_at = utcnow()
        risk.peak_reset_on = self.today
        self._session.add(risk)
        self._session.commit()
        self._session.refresh(risk)
        return risk

    def portfolio_state(self, as_of: date) -> PortfolioState:
        """What decide() needs at `as_of`'s close: every open position (managed and manual),
        the pending signals holding slots, cash, equity, peak, and the pause."""
        snapshot = self.equity(as_of)
        risk = self.risk_state()
        pending = []
        for signal in self.pending_signals(as_of):
            us = self._ticker(signal.us_symbol, TickerKind.us_stock)
            pending.append(
                PendingEntry(
                    symbol=signal.us_symbol, setup="breakout", sector=us.sector or "Unknown",
                    planned_cost=float(signal.suggested_units * signal.cdr_signal_close),
                )
            )
        return PortfolioState(
            cash=float(snapshot.cash),
            positions=tuple(p.strategy_position() for p in self.positions(as_of)),
            pending=tuple(pending),
            equity=float(snapshot.equity),
            peak=float(snapshot.peak),
            paused=risk.paused,
            paused_since=risk.paused_at if risk.paused else None,
        )
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_equity.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 7 passed; the full suite 675 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/ledger.py tests/fixtures/live_ledger_examples.yaml tests/test_live_equity.py
git commit -m "feat: live positions, equity, peak, pause and resume, and the decide() portfolio

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: One session's exits and stop raises (`review_session`)

**Files:**
- Create: `src/signalbench/live/review.py`, `tests/test_live_review.py`
- Modify: `tests/live_helpers.py`

`review_session()` is what the evening scan (spec 05, steps 5 and 6) calls for each session; the tests' `_scan()` shows the scan's use of it. The tests load bars from the database through `live_market()`, with the committed `data/strategy_v2-none-cash.yaml` (read only).

- [ ] **Step 1: Write the helpers and the failing tests**

In `tests/live_helpers.py`, replace:

```python
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlmodel import Session

from paper_helpers import evening
from signalbench.db.models import Price, Ticker, TickerKind, TradeSignal
from signalbench.live.acb import SplitEvent, Trade
from signalbench.live.ledger import Ledger
from strategy_helpers import WeekdaySessions
```

with:

```python
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlmodel import Session

from paper_helpers import evening
from signalbench.db.models import Price, Ticker, TickerKind, TradeSignal
from signalbench.ingest.cdr import CdrEntry
from signalbench.live.acb import SplitEvent, Trade
from signalbench.live.ledger import Ledger
from strategy_helpers import WeekdaySessions
```

Append to `tests/live_helpers.py`:

```python
def add_benchmark(session: Session, days: Sequence[date], closes: Sequence[float]) -> Ticker:
    """QQQ, the regime symbol every live market loads."""
    qqq = Ticker(symbol="QQQ", company_name="Invesco QQQ Trust", kind=TickerKind.benchmark,
                 price_symbol="QQQ")
    session.add(qqq)
    session.commit()
    add_prices(session, qqq, days, closes)
    return qqq


def universe(*pairs: tuple[str, str, str]) -> list[CdrEntry]:
    """(US symbol, CDR symbol, sector) as the universe file lists them."""
    return [CdrEntry(us, cdr, f"{cdr}.NE", us, sector) for us, cdr, sector in pairs]
```

Create `tests/test_live_review.py`:

```python
"""Spec 04: the live session review on stored bars, with the real v2-none-cash config: the
trailing stop on a hand-worked series, its timing, exits, and what is discarded."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, select

from live_helpers import (
    add_benchmark,
    add_pair,
    add_prices,
    make_ledger,
    send_signal,
    universe,
)
from signalbench.db.models import EarningsEvent, Ticker
from signalbench.ingest.earnings import FINNHUB_EARNINGS_SOURCE
from signalbench.live.ledger import Ledger
from signalbench.live.review import SessionReview, live_market, review_session
from signalbench.strategy.config import load_strategy_config
from strategy_helpers import WeekdaySessions, weekdays

CONFIG = load_strategy_config(Path(__file__).parents[1] / "data" / "strategy_v2-none-cash.yaml")[0]
DAYS = weekdays(date(2026, 3, 2), 70)
SIGNAL, ENTRY = 59, 60  # indexes into DAYS
# 60 flat closes, so ATR(14) is exactly 2 (high/low are close +/- 1), then up by 1 a session:
# the trailing stop is the highest close - 3 x 2. 97.2 on 64 is below the stop raised on 63.
US = [100.0] * 60 + [100.5, 101.5, 102.5, 103.5, 97.2, 110.0, 111.0] + [111.0] * 3
UNIVERSE = universe(("NVDA", "ZNVD", "Information Technology"), ("XOM", "ZXOM", "Energy"))


@pytest.fixture
def ledger(session: Session) -> Ledger:
    nvda, znvd = add_pair(session)
    xom, zxom = add_pair(session, "XOM", "ZXOM", "Energy")
    add_prices(session, nvda, DAYS, US)
    add_prices(session, znvd, DAYS, [close / 5 for close in US], volume=100)  # ratio 0.2
    add_prices(session, xom, DAYS, [50.0] * 70)
    add_prices(session, zxom, DAYS, [10.0] * 70, volume=100)
    add_benchmark(session, DAYS, [300.0] * 70)
    ledger = make_ledger(session, today=DAYS[-1])
    ledger.record_cash(Decimal("100.00"), DAYS[0], "deposit")
    return ledger


def _open(ledger: Ledger) -> int:
    signal = send_signal(ledger, DAYS[SIGNAL], us_close="100", us_stop="96", cdr_close="20")
    assert signal.id is not None
    ledger.record_fill(cdr_symbol="ZNVD", side="buy", quantity=Decimal(1),
                       price_cad=Decimal("20.10"), trade_date=DAYS[ENTRY], signal_id=signal.id)
    return signal.id


def _review(ledger: Ledger, session: Session, index: int, **extra: object) -> SessionReview:
    day = DAYS[index]
    market = live_market(session, UNIVERSE, CONFIG, WeekdaySessions(), day)
    return review_session(ledger, market, CONFIG, day, **extra)


def _scan(ledger: Ledger, session: Session, indexes: range) -> dict[int, SessionReview]:
    """What the evening scan does with each review: write the alerts and the raises."""
    reviews = {}
    for index in indexes:
        review = reviews[index] = _review(ledger, session, index)
        for call in review.exits:
            ledger.record_exit_alert(signal_id=call.signal_id, as_of=DAYS[index],
                                     reason=call.reason)
        for up in review.raises:
            ledger.record_stop_update(signal_id=up.signal_id, session=up.session,
                                      new_us_stop=up.new_us_stop)
    return reviews


def _earnings(session: Session, symbol: str, index: int) -> None:
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
    session.add(EarningsEvent(ticker_id=ticker.id, event_date=DAYS[index],
                              source=FINNHUB_EARNINGS_SOURCE))
    session.commit()


def test_the_trailing_stop_on_a_hand_worked_series(ledger: Ledger, session: Session) -> None:
    signal_id = _open(ledger)
    reviews = _scan(ledger, session, range(ENTRY, 65))
    # highest - 6: 94.5 and 95.5 stay below the initial 96; 96.5 on 62; 97.5 on 63.
    raises = [(r.session, r.old_us_stop, r.new_us_stop) for v in reviews.values() for r in v.raises]
    assert raises == [
        (DAYS[62], Decimal(96), Decimal("96.5")), (DAYS[63], Decimal("96.5"), Decimal("97.5")),
    ]
    assert ledger.current_stop(signal_id).cdr == Decimal("19.50")  # 97.5 x 0.2
    # 97.2 on 64 is below 97.5, raised at 63's close and in force from 64: a stop exit, and
    # no raise on the exit day. The stop in force during 63 was 96.5.
    assert [(e.signal_id, e.reason) for e in reviews[64].exits] == [(signal_id, "stop")]
    assert reviews[64].raises == ()
    assert ledger.stop_in_force(signal_id, DAYS[63]).us == Decimal("96.5")
    assert ledger.open_exit_alert(signal_id) is not None


def test_no_raise_or_second_alert_while_an_exit_alert_is_sent(
    ledger: Ledger, session: Session
) -> None:
    signal_id = _open(ledger)
    _scan(ledger, session, range(ENTRY, 65))  # the stop alert on 64 stays sent
    on_65 = _review(ledger, session, 65)  # 110: decide() raises the stop, which is dropped
    assert (on_65.exits, on_65.raises, on_65.discarded) == ((), (), (str(signal_id),))
    alert = ledger.open_exit_alert(signal_id)
    assert alert is not None and alert.id is not None
    ledger.mark_exit_alert(alert.id, "ignored")  # managed again from the next session
    [up] = _review(ledger, session, 66).raises
    assert up.new_us_stop > Decimal("97.5")


def test_a_stop_exit_comes_before_an_earnings_exit(ledger: Ledger, session: Session) -> None:
    _open(ledger)
    _earnings(session, "NVDA", 66)  # within 2 sessions of 64
    reviews = _scan(ledger, session, range(ENTRY, 65))
    assert [e.reason for v in reviews.values() for e in v.exits] == ["stop"]


def test_the_earnings_exit_fires_from_a_finnhub_calendar_date(
    ledger: Ledger, session: Session
) -> None:
    signal_id = _open(ledger)
    _earnings(session, "NVDA", 63)  # an upcoming calendar date, no SEC 2.02 filing
    reviews = _scan(ledger, session, range(ENTRY, 63))
    assert [(i, e.reason) for i, v in reviews.items() for e in v.exits] == [(61, "earnings")]
    assert ledger.open_exit_alert(signal_id) is not None


def test_a_position_is_never_closed_for_time_and_has_no_target(
    ledger: Ledger, session: Session
) -> None:
    signal_id = _open(ledger)
    _scan(ledger, session, range(ENTRY, 64))
    [position] = ledger.portfolio_state(DAYS[63]).positions
    assert (position.target, position.time_limit, position.sessions_held) == (None, None, 4)
    assert ledger.open_exit_alert(signal_id) is None


def test_a_manual_position_holds_its_slot_and_its_decisions_are_discarded(
    ledger: Ledger, session: Session
) -> None:
    fill = ledger.record_fill(cdr_symbol="ZXOM", side="buy", quantity=Decimal(2),
                              price_cad=Decimal(10), trade_date=DAYS[ENTRY])
    _earnings(session, "XOM", 62)  # decide() returns an earnings exit for it on 61
    review = _review(ledger, session, 61)
    assert review.exits == ()
    assert review.discarded == (f"manual-{fill.id}",)
    assert review.state.slots_used() == 1


def test_a_held_symbol_gets_no_decision(ledger: Ledger, session: Session) -> None:
    signal_id = _open(ledger)
    _scan(ledger, session, range(ENTRY, 64))
    review = _review(ledger, session, 64, hold={"NVDA"})  # the scale check held NVDA tonight
    assert (review.exits, review.discarded) == ((), (str(signal_id),))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_review.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.live.review'`.

- [ ] **Step 3: Write the review**

Create `src/signalbench/live/review.py`:

```python
"""One session's exits and stop raises for the live ledger (spec 04, Strategy levels).

The evening scan (spec 05) calls `review_session()` for each session in order (catch-up
sessions first) and writes what it returns: an exit alert per `ExitCall`
(`Ledger.record_exit_alert`) and a `trail` row per `Raise` (`Ledger.record_stop_update`).
decide() sees every open position, managed and manual, and the pending signals; what it
returns for a manual position, for a position with a `sent` exit alert, or for a symbol the
scale check holds is discarded. Entries and skips are passed through for the scan to size.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlmodel import Session

from signalbench.backtest.runner import load_market_inputs
from signalbench.ingest.cdr import CdrEntry
from signalbench.live.book import AlertReason, q4
from signalbench.live.ledger import Ledger
from signalbench.market.calendar import HISTORY_START, Sessions
from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.decide import decide
from signalbench.strategy.decision import Decision
from signalbench.strategy.market_view import MarketView
from signalbench.strategy.portfolio import PortfolioState
from signalbench.strategy.readings import NullReadingsView


@dataclass(frozen=True)
class ExitCall:
    """Sell a managed position at the next open."""

    signal_id: int
    us_symbol: str
    reason: AlertReason


@dataclass(frozen=True)
class Raise:
    """A trailing-stop raise decided at `session`'s close, in force from the next session."""

    signal_id: int
    session: date
    old_us_stop: Decimal
    new_us_stop: Decimal


@dataclass(frozen=True)
class SessionReview:
    as_of: date
    state: PortfolioState  # what decide() saw
    decision: Decision  # as decide() returned it: the scan sizes its entries
    exits: tuple[ExitCall, ...]
    raises: tuple[Raise, ...]
    discarded: tuple[str, ...]  # position ids whose exit or raise was dropped


def live_market(
    session: Session,
    universe: list[CdrEntry],
    config: StrategyConfig,
    calendar: Sessions,
    as_of: date,
) -> MarketView:
    """Stored US bars up to `as_of`, earnings dates from SEC 2.02 plus the Finnhub calendar
    (upcoming ones included), and sessions running past `as_of` for the earnings look-ahead."""
    inputs = load_market_inputs(
        session, universe, config.regime_symbol, as_of, calendar_earnings=True
    )
    lookahead = max(config.earnings_blackout_sessions, config.earnings_exit_sessions)
    sessions = calendar.sessions_between(HISTORY_START, as_of)
    sessions += calendar.next_sessions(as_of, lookahead)
    return MarketView(inputs.symbols, inputs.benchmark, sessions, config)


def _signal_id(position_id: str) -> int | None:
    """The signal id of a managed position; None for a manual one (manual-<fill id>)."""
    return int(position_id) if position_id.isdigit() else None


def review_session(
    ledger: Ledger,
    market: MarketView,
    config: StrategyConfig,
    as_of: date,
    *,
    hold: Collection[str] = (),
) -> SessionReview:
    """decide() at `as_of`'s close on the ledger's portfolio, Breakout only and without Jev
    readings (spec 04, Live config). `hold` names US symbols that get no decision tonight."""
    live = config.with_setups(("breakout",))
    state = ledger.portfolio_state(as_of)
    decision = decide(as_of, market, NullReadingsView(), state, live)
    symbols = {p.id: p.symbol for p in state.positions}
    exits: list[ExitCall] = []
    raises: list[Raise] = []
    discarded: list[str] = []

    def managed(position_id: str) -> int | None:
        signal_id = _signal_id(position_id)
        if (
            signal_id is None
            or symbols[position_id] in hold
            or ledger.open_exit_alert(signal_id) is not None
        ):
            discarded.append(position_id)
            return None
        return signal_id

    for order in decision.exits:
        signal_id = managed(order.position_id)
        if signal_id is None:
            continue
        if order.reason not in ("stop", "earnings"):
            raise ValueError(f"the live config has no {order.reason} exit ({order.symbol})")
        reason: AlertReason = "stop" if order.reason == "stop" else "earnings"
        exits.append(ExitCall(signal_id, order.symbol, reason))
    for update in decision.stop_updates:
        signal_id = managed(update.position_id)
        if signal_id is None:
            continue
        old = ledger.stop_in_force(signal_id, as_of).us
        new = q4(Decimal(repr(update.new_stop)))
        if new > old:
            raises.append(Raise(signal_id, as_of, old, new))
    return SessionReview(
        as_of=as_of, state=state, decision=decision, exits=tuple(exits),
        raises=tuple(raises), discarded=tuple(discarded),
    )
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_review.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 7 passed; the full suite 682 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/review.py tests/live_helpers.py tests/test_live_review.py
git commit -m "feat: live session review: trailing-stop raises and exits for managed positions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The tax-year ACB report and its CSV

**Files:**
- Create: `src/signalbench/live/tax.py`, `tests/test_live_tax.py`
- Modify: `src/signalbench/live/ledger.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_tax.py`:

```python
"""Spec 04: the tax-year ACB report, its CSV, and its notes."""

from datetime import date
from decimal import Decimal

from sqlmodel import Session

from live_helpers import add_pair, load_example, make_ledger, record_example
from signalbench.live.tax import NOTES, tax_csv, tax_text


def test_the_rebuy_example_s_report_with_the_superficial_loss(session: Session) -> None:
    add_pair(session, "TST", "ZTST")
    ledger = make_ledger(session)
    record_example(ledger, load_example("acb_rebuy"))
    report = ledger.tax_report(2026)
    assert tax_csv(report) == (
        "date,symbol,quantity,proceeds,acb,fees,gain_loss,superficial_loss_added_to_acb,"
        "allowed_gain_loss,provisional\n"
        "2026-02-02,ZTST,4,140.00,124.80,0.00,15.20,0.00,15.20,no\n"
        "2026-02-16,ZTST,2,58.00,62.20,0.00,-4.20,2.10,-2.10,no\n"
        "total,,,198.00,187.00,0.00,11.00,2.10,13.10,\n"
    )
    assert tax_text(report)[2] == (
        "2026-02-16 ZTST sold 2 | proceeds C$58.00 | ACB C$62.20 | fees C$0.00 | gain/loss "
        "C$-4.20 | superficial loss added to ACB C$2.10 | allowed C$-2.10"
    )
    assert tax_text(report)[-2:] == list(NOTES)
    assert ledger.tax_report(2025).lines == ()


def test_a_loss_inside_its_30_days_is_provisional(session: Session) -> None:
    add_pair(session, "TST", "ZTST")
    ledger = make_ledger(session, today=date(2026, 3, 10))
    record_example(ledger, load_example("acb_rebuy"))
    [gain, loss] = ledger.tax_report(2026).lines
    assert (gain.provisional, loss.provisional) == (False, True)
    assert tax_text(ledger.tax_report(2026))[2].endswith("allowed C$-2.10 (provisional)")


def test_the_year_s_cdr_splits_show_the_acb_per_unit_before_and_after(session: Session) -> None:
    add_pair(session, "TST", "ZTST")
    ledger = make_ledger(session)
    ledger.record_cash(Decimal("100.00"), date(2026, 3, 2), "deposit")
    ledger.record_fill(cdr_symbol="ZTST", side="buy", quantity=Decimal(3),
                       price_cad=Decimal("30.00"), trade_date=date(2026, 3, 2))
    ledger.record_split(kind="cdr_split", symbol="ZTST", ex_date=date(2026, 3, 9),
                        ratio=Decimal(2), source="owner")
    report = ledger.tax_report(2026)
    [split] = report.splits
    assert (split.ratio, split.per_unit_before, split.per_unit_after) == (
        Decimal(2), Decimal("30.00"), Decimal("15.00")
    )
    assert "split 2026-03-09 ZTST 2-for-1 | ACB per unit C$30.00 -> C$15.00" in tax_text(report)
    assert report.lines == ()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_tax.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.live.tax'`.

- [ ] **Step 3: Write the report and `Ledger.tax_report`**

Create `src/signalbench/live/tax.py`:

```python
"""The ACB report for one tax year (spec 04, Tax report): `/tax YEAR` and `ledger tax YEAR`.

Each disposition in the year with its proceeds, ACB, fees, gain or loss, the superficial loss
added to ACB, and whether that is still provisional; the year's CDR splits with the ACB per unit
before and after; and totals. Amounts are rounded to the cent here and only here, and the totals
add up the rounded lines. Not tax advice.
"""

import csv
import io
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from signalbench.live.acb import AcbBook

CENT = Decimal("0.01")
NOTES = (
    (
        "Not tax advice: a record-keeping aid that follows the CRA's published ACB method. "
        "Verify your return independently."
    ),
    (
        "Superficial losses count only the same CDR symbol as identical property. Confirm "
        "whether a US listing of the same company held elsewhere changes this."
    ),
)
CSV_HEADER = (
    "date", "symbol", "quantity", "proceeds", "acb", "fees", "gain_loss",
    "superficial_loss_added_to_acb", "allowed_gain_loss", "provisional",
)


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class TaxLine:
    day: date
    symbol: str
    quantity: Decimal
    proceeds: Decimal
    acb: Decimal
    fees: Decimal
    gain: Decimal  # before the superficial-loss adjustment
    superficial: Decimal  # the part of a loss added to ACB
    allowed: Decimal  # gain + superficial
    provisional: bool  # a loss whose 30-day window has not passed


@dataclass(frozen=True)
class TaxSplit:
    day: date
    symbol: str
    ratio: Decimal
    per_unit_before: Decimal | None  # None when no units were held
    per_unit_after: Decimal | None


@dataclass(frozen=True)
class TaxTotals:
    proceeds: Decimal
    acb: Decimal
    fees: Decimal
    gain: Decimal
    superficial: Decimal
    allowed: Decimal


@dataclass(frozen=True)
class TaxReport:
    year: int
    lines: tuple[TaxLine, ...]
    splits: tuple[TaxSplit, ...]
    totals: TaxTotals  # sums of the rounded lines


def build_tax_report(books: Mapping[str, AcbBook], year: int) -> TaxReport:
    """The year's dispositions and CDR splits across every CDR, by date then symbol."""
    lines: list[TaxLine] = []
    splits: list[TaxSplit] = []
    for symbol, book in books.items():
        for d in book.dispositions:
            if d.trade.day.year != year:
                continue
            lines.append(
                TaxLine(
                    day=d.trade.day, symbol=symbol, quantity=d.trade.quantity,
                    proceeds=cents(d.proceeds), acb=cents(d.acb), fees=cents(d.trade.fee),
                    gain=cents(d.gain), superficial=cents(d.denied), allowed=cents(d.allowed),
                    provisional=d.provisional,
                )
            )
        for record in book.splits:
            if record.split.day.year != year:
                continue
            before, after = record.per_unit_before, record.per_unit_after
            splits.append(
                TaxSplit(
                    day=record.split.day, symbol=symbol, ratio=record.split.ratio,
                    per_unit_before=None if before is None else cents(before),
                    per_unit_after=None if after is None else cents(after),
                )
            )
    zero = Decimal(0)
    totals = TaxTotals(
        proceeds=sum((line.proceeds for line in lines), zero),
        acb=sum((line.acb for line in lines), zero),
        fees=sum((line.fees for line in lines), zero),
        gain=sum((line.gain for line in lines), zero),
        superficial=sum((line.superficial for line in lines), zero),
        allowed=sum((line.allowed for line in lines), zero),
    )
    return TaxReport(
        year=year,
        lines=tuple(sorted(lines, key=lambda line: (line.day, line.symbol))),
        splits=tuple(sorted(splits, key=lambda split: (split.day, split.symbol))),
        totals=totals,
    )


def tax_csv(report: TaxReport) -> str:
    """One row per disposition, then a totals row."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for line in report.lines:
        writer.writerow([
            line.day.isoformat(), line.symbol, f"{line.quantity.normalize():f}", line.proceeds,
            line.acb, line.fees, line.gain, line.superficial, line.allowed,
            "yes" if line.provisional else "no",
        ])
    t = report.totals
    writer.writerow(
        ["total", "", "", t.proceeds, t.acb, t.fees, t.gain, t.superficial, t.allowed, ""]
    )
    return buffer.getvalue()


def _per_unit(value: Decimal | None) -> str:
    return "-" if value is None else f"C${value}"


def tax_text(report: TaxReport) -> list[str]:
    """The report as lines for the terminal and the /tax summary."""
    lines = [f"ACB report {report.year}: {len(report.lines)} dispositions"]
    for line in report.lines:
        flag = " (provisional)" if line.provisional else ""
        superficial = ""
        if line.superficial:
            superficial = f" | superficial loss added to ACB C${line.superficial}"
        lines.append(
            f"{line.day} {line.symbol} sold {line.quantity.normalize():f} | proceeds "
            f"C${line.proceeds} | ACB C${line.acb} | fees C${line.fees} | gain/loss "
            f"C${line.gain}{superficial} | allowed C${line.allowed}{flag}"
        )
    t = report.totals
    lines.append(
        f"totals: proceeds C${t.proceeds} | ACB C${t.acb} | fees C${t.fees} | gain/loss "
        f"C${t.gain} | superficial C${t.superficial} | allowed C${t.allowed}"
    )
    for split in report.splits:
        lines.append(
            f"split {split.day} {split.symbol} {split.ratio.normalize():f}-for-1 | ACB per unit "
            f"{_per_unit(split.per_unit_before)} -> {_per_unit(split.per_unit_after)}"
        )
    return lines + list(NOTES)
```

In `src/signalbench/live/ledger.py`, replace:

```python
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlmodel import col, select

from signalbench.db.models import EquitySnapshot, LiveRiskState, TickerKind, utcnow
from signalbench.live.book import ZERO, Episode, LedgerError, cad, q4
from signalbench.live.levels import LedgerLevels, StopLevels
from signalbench.strategy.portfolio import PendingEntry, PortfolioState, Position
```

with:

```python
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlmodel import col, select

from signalbench.db.models import EquitySnapshot, LiveRiskState, TickerKind, utcnow
from signalbench.live.book import ZERO, Episode, LedgerError, cad, q4
from signalbench.live.levels import LedgerLevels, StopLevels
from signalbench.live.tax import TaxReport, build_tax_report
from signalbench.strategy.portfolio import PendingEntry, PortfolioState, Position
```

Append to `src/signalbench/live/ledger.py` (the end of `Ledger`):

```python
    # --- The tax report --------------------------------------------------------------------

    def tax_report(self, year: int) -> TaxReport:
        """The year's dispositions, superficial losses, and CDR splits (spec 04). A loss within
        30 days of today is provisional. Not tax advice."""
        return build_tax_report(self.books(), year)
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_tax.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 3 passed; the full suite 685 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/tax.py src/signalbench/live/ledger.py tests/test_live_tax.py
git commit -m "feat: live tax-year ACB report with superficial losses, splits, and a CSV

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The scale-up check

**Files:**
- Create: `src/signalbench/live/scaleup.py`, `tests/test_live_scaleup.py`
- Modify: `src/signalbench/live/ledger.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_scaleup.py`:

```python
"""Spec 04: the scale-up check, each criterion at its threshold, and on the ledger."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlmodel import Session

from live_helpers import add_pair, make_ledger, send_signal
from signalbench.live.ledger import Ledger
from signalbench.live.scaleup import (
    AlertOutcome,
    ScaleUpResult,
    SignalBuy,
    is_miss,
    scale_up,
)
from strategy_helpers import weekdays

DAYS = weekdays(date(2026, 6, 1), 60)
MON, TUE, WED = date(2026, 6, 8), date(2026, 6, 9), date(2026, 6, 10)


def _result(**changes: object) -> ScaleUpResult:
    inputs: dict[str, object] = {
        "closed_managed": 10, "statuses": [("taken", None)] * 10,
        "buys": [SignalBuy(1, Decimal("40.00"), Decimal(40))], "alerts": [], "today": WED,
    }
    return scale_up(**{**inputs, **changes})


@pytest.mark.parametrize(
    ("statuses", "rate", "ok"),
    [
        # 8 / (8 + 1 expired + 1 disagree) = 0.80; price_moved, wide_spread, withdrawn, and
        # still-sent signals are left out.
        ([("taken", None)] * 8 + [("expired", None), ("skipped", "disagree"),
          ("skipped", "price_moved"), ("skipped", "wide_spread"), ("withdrawn", None),
          ("sent", None)], Decimal("0.8"), True),
        ([("taken", None)] * 7 + [("skipped", "no_time"), ("skipped", "other")],
         Decimal(7) / 9, False),
    ],
)
def test_the_take_rate_at_its_threshold(
    statuses: list[tuple[str, str | None]], rate: Decimal, ok: bool
) -> None:
    result = _result(statuses=statuses)
    assert (result.take_rate, result.checks["take_rate"]) == (rate, ok)


@pytest.mark.parametrize(
    ("price", "slippage_ok", "violations"),
    [("40.20", True, ()), ("40.24", False, ()), ("40.40", False, ()), ("40.41", False, (7,))],
)
def test_slippage_and_the_1_percent_limit_at_their_thresholds(
    price: str, slippage_ok: bool, violations: tuple[int, ...]
) -> None:
    result = _result(buys=[SignalBuy(7, Decimal(price), Decimal(40))])
    assert result.checks["slippage"] == slippage_ok
    assert result.violations == violations


def test_an_exit_alert_misses_when_ignored_or_its_window_passes_without_a_sale() -> None:
    def alert(status: str, sold_on: date | None) -> AlertOutcome:
        return AlertOutcome(1, status, TUE, sold_on)  # the alert was for MON: sell by TUE

    assert not is_miss(alert("done", TUE), WED)
    assert is_miss(alert("done", WED), WED)
    assert not is_miss(alert("sent", None), TUE)  # still inside its window
    assert is_miss(alert("sent", None), WED)
    assert is_miss(alert("ignored", TUE), WED)


def test_fewer_than_10_closed_managed_positions_is_not_active() -> None:
    result = _result(closed_managed=9)
    assert (result.active, all(result.checks.values()), result.passed) == (False, True, False)


def _round_trip(ledger: Ledger, index: int, buy: str = "40.00") -> int:
    signal = send_signal(ledger, DAYS[index])
    assert signal.id is not None
    ledger.record_fill(cdr_symbol="ZNVD", side="buy", quantity=Decimal(1),
                       price_cad=Decimal(buy), trade_date=DAYS[index + 1], signal_id=signal.id)
    return signal.id


def test_ten_closed_trades_that_followed_the_signals_pass_and_are_recorded(
    session: Session,
) -> None:
    add_pair(session)
    ledger = make_ledger(session, today=DAYS[-1])
    ledger.record_cash(Decimal("1000.00"), DAYS[0], "deposit")
    for n in range(10):
        signal_id = _round_trip(ledger, 3 * n)
        alert = ledger.record_exit_alert(signal_id=signal_id, as_of=DAYS[3 * n + 1],
                                         reason="stop", late=n == 9)
        if n == 9:  # sent late, on 2026-07-14 for 07-09: the window counts from the send
            alert.created_at = datetime(2026, 7, 14, 22, 0, tzinfo=UTC)  # 18:00 New York
            session.add(alert)
            session.commit()
        sold = DAYS[3 * n + 2] if n < 9 else date(2026, 7, 15)
        ledger.record_fill(cdr_symbol="ZNVD", side="sell", quantity=Decimal(1),
                           price_cad=Decimal("39.00"), trade_date=sold, exit_alert_id=alert.id)
    result = ledger.scale_up_check()
    assert (result.closed_managed, result.passed, result.misses) == (10, True, ())
    ledger.record_scale_up(result)
    [record] = ledger.risk_state().scale_up_history
    assert (record["passed"], record["take_rate"], record["closed_managed"]) == (True, "1", 10)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_scaleup.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.live.scaleup'`.

- [ ] **Step 3: Write the check and the ledger methods**

Create `src/signalbench/live/scaleup.py`:

```python
"""The scale-up check (spec 04): did the owner follow the signals closely enough to add C$900?

Active once at least 10 managed positions are fully closed. Profitability is not a criterion.
1. Take rate = taken / (taken + expired + skipped for disagree, no_time, or other) >= 0.80.
   `price_moved` and `wide_spread` skips (the rules require them) and withdrawn signals are out.
2. Mean |fill - cdr_signal_close| / cdr_signal_close over signal-linked buys <= 0.5%.
3. Every exit alert followed by a sale of that CDR within 1 session (a late alert: within 1
   session after it was sent). `ignored` is a miss. An alert still inside its window is not.
4. No signal-linked buy above cdr_signal_close x 1.01.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

MIN_CLOSED = 10
TAKE_RATE_MIN = Decimal("0.80")
SLIPPAGE_MAX = Decimal("0.005")
LIMIT_FACTOR = Decimal("1.01")
COUNTED_SKIPS = ("disagree", "no_time", "other")


@dataclass(frozen=True)
class SignalBuy:
    fill_id: int
    price: Decimal  # the CDR fill price
    signal_close: Decimal  # the signal's cdr_signal_close


@dataclass(frozen=True)
class AlertOutcome:
    alert_id: int
    status: str  # sent | done | ignored
    deadline: date  # the first NYSE session after the alert's session (or, late, its send date)
    sold_on: date | None  # the first sale of the CDR after the alert's session, if any


@dataclass(frozen=True)
class ScaleUpResult:
    closed_managed: int
    taken: int
    declined: int  # expired + skips for disagree, no_time, other
    buys: tuple[SignalBuy, ...]
    misses: tuple[int, ...]  # exit alert ids
    violations: tuple[int, ...]  # fill ids above cdr_signal_close x 1.01

    @property
    def active(self) -> bool:
        return self.closed_managed >= MIN_CLOSED

    @property
    def take_rate(self) -> Decimal | None:
        counted = self.taken + self.declined
        return None if counted == 0 else Decimal(self.taken) / counted

    @property
    def mean_slippage(self) -> Decimal | None:
        if not self.buys:
            return None
        gaps = [abs(b.price - b.signal_close) / b.signal_close for b in self.buys]
        return sum(gaps, Decimal(0)) / len(gaps)

    @property
    def checks(self) -> dict[str, bool]:
        rate, slippage = self.take_rate, self.mean_slippage
        return {
            "take_rate": rate is not None and rate >= TAKE_RATE_MIN,
            "slippage": slippage is not None and slippage <= SLIPPAGE_MAX,
            "exit_misses": not self.misses,
            "limit_violations": not self.violations,
        }

    @property
    def passed(self) -> bool:
        return self.active and all(self.checks.values())

    def record(self, checked_at: datetime) -> dict[str, Any]:
        """The result and its inputs, JSON-safe, for risk_state.scale_up_history."""
        rate, slippage = self.take_rate, self.mean_slippage
        return {
            "checked_at": checked_at.isoformat(),
            "closed_managed": self.closed_managed,
            "active": self.active,
            "passed": self.passed,
            "checks": self.checks,
            "taken": self.taken,
            "declined": self.declined,
            "take_rate": None if rate is None else str(rate),
            "signal_buys": len(self.buys),
            "mean_slippage": None if slippage is None else str(slippage),
            "misses": list(self.misses),
            "violations": list(self.violations),
        }


def is_miss(alert: AlertOutcome, today: date) -> bool:
    """Ignored, or its window has passed without a sale in it."""
    if alert.status == "ignored":
        return True
    sold_in_time = alert.sold_on is not None and alert.sold_on <= alert.deadline
    return not sold_in_time and today > alert.deadline


def scale_up(
    *,
    closed_managed: int,
    statuses: Sequence[tuple[str, str | None]],
    buys: Sequence[SignalBuy],
    alerts: Sequence[AlertOutcome],
    today: date,
) -> ScaleUpResult:
    """`statuses`: every signal's (status, skip_reason)."""
    taken = sum(1 for status, _ in statuses if status == "taken")
    declined = sum(
        1 for status, reason in statuses
        if status == "expired" or (status == "skipped" and reason in COUNTED_SKIPS)
    )
    return ScaleUpResult(
        closed_managed=closed_managed,
        taken=taken,
        declined=declined,
        buys=tuple(buys),
        misses=tuple(a.alert_id for a in alerts if is_miss(a, today)),
        violations=tuple(b.fill_id for b in buys if b.price > b.signal_close * LIMIT_FACTOR),
    )
```

In `src/signalbench/live/ledger.py`, replace:

```python
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlmodel import col, select

from signalbench.db.models import EquitySnapshot, LiveRiskState, TickerKind, utcnow
from signalbench.live.book import ZERO, Episode, LedgerError, cad, q4
from signalbench.live.levels import LedgerLevels, StopLevels
from signalbench.live.tax import TaxReport, build_tax_report
from signalbench.strategy.portfolio import PendingEntry, PortfolioState, Position
```

with:

```python
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlmodel import col, select

from signalbench.db.models import (
    EquitySnapshot,
    ExitAlert,
    LiveRiskState,
    TickerKind,
    TradeSignal,
    utcnow,
)
from signalbench.live.book import NEW_YORK, ZERO, Episode, LedgerError, cad, q4
from signalbench.live.levels import LedgerLevels, StopLevels
from signalbench.live.scaleup import AlertOutcome, ScaleUpResult, SignalBuy, scale_up
from signalbench.live.tax import TaxReport, build_tax_report
from signalbench.strategy.portfolio import PendingEntry, PortfolioState, Position
```

Append to `src/signalbench/live/ledger.py` (the end of `Ledger`):

```python
    # --- The scale-up check ----------------------------------------------------------------

    def scale_up_check(self) -> ScaleUpResult:
        """The four checks over every signal, signal-linked buy, and exit alert (spec 04)."""
        closed = sum(1 for trade in self.closed_trades() if trade.episode.managed)
        signals = {s.id: s for s in self._session.exec(select(TradeSignal)).all()}
        buys = [
            SignalBuy(f.id, f.price_cad, signals[f.signal_id].cdr_signal_close)
            for f in self._fills()
            if f.side == "buy" and f.signal_id is not None and f.id is not None
        ]
        alerts = self._session.exec(select(ExitAlert).order_by(col(ExitAlert.id))).all()
        return scale_up(
            closed_managed=closed,
            statuses=[(s.status, s.skip_reason) for s in signals.values()],
            buys=buys,
            alerts=[self._alert_outcome(alert) for alert in alerts],
            today=self.today,
        )

    def _alert_outcome(self, alert: ExitAlert) -> AlertOutcome:
        """The sale window: through the first session after the alert's session, or after the
        New York date it was sent when it was late."""
        assert alert.id is not None
        sent_on = alert.created_at.astimezone(NEW_YORK).date() if alert.late else alert.as_of
        sales = [
            f.trade_date for f in self._fills(alert.cdr_ticker_id)
            if f.side == "sell" and f.trade_date > alert.as_of
        ]
        return AlertOutcome(
            alert_id=alert.id, status=alert.status,
            deadline=self._calendar.next_sessions(sent_on, 1)[0], sold_on=min(sales, default=None),
        )

    def record_scale_up(self, result: ScaleUpResult) -> None:
        """Append the result and its inputs to risk_state's history, for the record."""
        risk = self.risk_state()
        risk.scale_up_history = [*risk.scale_up_history, result.record(utcnow())]
        self._session.add(risk)
        self._session.commit()
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_scaleup.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 9 passed; the full suite 694 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/scaleup.py src/signalbench/live/ledger.py tests/test_live_scaleup.py
git commit -m "feat: live scale-up check with its four criteria, recorded in risk_state

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: `live start` and the scan's config check

**Files:**
- Create: `src/signalbench/live/start.py`, `tests/test_live_start.py`

The tests copy the committed `data/strategy_v2-none-cash.yaml` and `data/cdr_spread_survey.yaml` into a throwaway repo under `tmp_path`; the real files are only read.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_start.py`:

```python
"""Spec 04: `live start` freezes the live config once; the scan's check refuses a changed file."""

from datetime import date
from pathlib import Path

import pytest
from sqlmodel import Session

from paper_helpers import git
from signalbench.backtest.preregistration import RunRefusedError
from signalbench.db.models import LiveConfig, LiveRiskState
from signalbench.live.start import (
    LIVE_CONFIG,
    LiveRefusedError,
    start_live,
    verify_live_config,
)
from signalbench.strategy.config import config_sha256

REPO = Path(__file__).resolve().parents[1]
SURVEY = "data/cdr_spread_survey.yaml"
DAY = date(2026, 10, 1)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo holding the committed live config and spread survey, and a tracked src file."""
    for name in (LIVE_CONFIG, SURVEY):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO / name).read_bytes())
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "code.py").write_text("x = 1\n", encoding="utf-8")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "data", "src")
    git(tmp_path, "commit", "-q", "-m", "live")
    return tmp_path


def _start(session: Session, repo: Path, config: str = LIVE_CONFIG) -> LiveConfig:
    return start_live(session, repo=repo, config_path=repo / config, survey_path=repo / SURVEY,
                      today=DAY)


def test_live_start_records_the_config_once(session: Session, repo: Path) -> None:
    row = _start(session, repo)
    sha = config_sha256((repo / LIVE_CONFIG).read_bytes())
    head = git(repo, "rev-parse", "HEAD")
    assert (row.config_path, row.config_sha256, row.started_on, row.start_git_sha) == (
        LIVE_CONFIG, sha, DAY, head
    )
    risk = session.get(LiveRiskState, 1)
    assert risk is not None and not risk.paused
    with pytest.raises(LiveRefusedError, match="already recorded"):
        _start(session, repo)


def test_live_start_refuses_an_edited_or_uncommitted_config(session: Session, repo: Path) -> None:
    path = repo / LIVE_CONFIG
    path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    with pytest.raises(LiveRefusedError, match="must exist and be committed, unchanged"):
        _start(session, repo)
    git(repo, "checkout", "--", LIVE_CONFIG)
    (repo / "data" / "strategy_v9.yaml").write_bytes(path.read_bytes())  # never committed
    with pytest.raises(LiveRefusedError, match="strategy_v9.yaml must exist and be committed"):
        _start(session, repo, "data/strategy_v9.yaml")
    assert session.get(LiveConfig, 1) is None


def test_live_start_runs_the_backtest_guard(session: Session, repo: Path) -> None:
    text = (repo / LIVE_CONFIG).read_text(encoding="utf-8")
    (repo / "data" / "strategy_other.yaml").write_text(text, encoding="utf-8")  # wrong name
    (repo / "data" / "strategy_v2-cost.yaml").write_text(
        text.replace("version: v2-none-cash", "version: v2-cost").replace(
            "cost_per_side: 0.002", "cost_per_side: 0.003"
        ),
        encoding="utf-8",
    )
    git(repo, "add", "data")
    git(repo, "commit", "-q", "-m", "more")
    with pytest.raises(RunRefusedError, match="must be data/strategy_v2-none-cash.yaml"):
        _start(session, repo, "data/strategy_other.yaml")
    with pytest.raises(RunRefusedError, match="cost_per_side 0.003"):
        _start(session, repo, "data/strategy_v2-cost.yaml")


def test_live_start_refuses_uncommitted_code(session: Session, repo: Path) -> None:
    (repo / "src" / "code.py").write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(LiveRefusedError, match=r"tracked code or data \(src/code.py\)"):
        _start(session, repo)


def test_the_scan_check_refuses_no_row_and_a_changed_config(session: Session, repo: Path) -> None:
    with pytest.raises(LiveRefusedError, match="Run `signalbench live start` first"):
        verify_live_config(session, repo)
    _start(session, repo)
    row, config = verify_live_config(session, repo)
    assert (row.config_path, config.version, config.enabled_setups) == (
        LIVE_CONFIG, "v2-none-cash", ("breakout",)
    )
    assert (config.breakout.time_limit, config.cash_vehicle) == (None, None)
    path = repo / LIVE_CONFIG
    path.write_text(path.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    with pytest.raises(LiveRefusedError, match="changed: sha256"):
        verify_live_config(session, repo)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_start.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'signalbench.live.start'`.

- [ ] **Step 3: Write `start_live` and `verify_live_config`**

Create `src/signalbench/live/start.py`:

```python
"""`signalbench live start`: freeze the one live config (spec 04, Live config).

`start_live()` writes the single `live_config` row once, after the backtest's guard: the file is
data/strategy_<version>.yaml, committed and unchanged, with the committed spread survey's
cost_per_side, and the code is committed too (its HEAD is recorded). `verify_live_config()` is
the evening scan's check (spec 05, step 1): the row exists and the file's sha256 still matches.
"""

from datetime import date
from pathlib import Path

from sqlmodel import Session

from signalbench.backtest.preregistration import (
    check_config_path,
    check_cost_matches_survey,
)
from signalbench.backtest.provenance import code_version, committed_unchanged
from signalbench.db.models import LiveConfig, LiveRiskState
from signalbench.strategy.config import StrategyConfig, load_strategy_config
from signalbench.strategy.spread import load_spread_survey

LIVE_CONFIG = "data/strategy_v2-none-cash.yaml"  # owner decision, overview changelog 2026-09-29


class LiveRefusedError(ValueError):
    """`live start` or the scan's config check refused. The message says why."""


def start_live(
    session: Session, *, repo: Path, config_path: Path, survey_path: Path, today: date
) -> LiveConfig:
    """Write the `live_config` row (and an unpaused `risk_state` row). Refuses a second run."""
    if session.get(LiveConfig, 1) is not None:
        raise LiveRefusedError(
            "The live config is already recorded. Trading a different config is a new owner "
            "decision (spec 04)."
        )
    for path, what in ((config_path, "The live config"), (survey_path, "The spread survey")):
        if not path.exists() or not committed_unchanged(repo, path):
            raise LiveRefusedError(
                f"{what} {path.name} must exist and be committed, unchanged (spec 04)."
            )
    config, sha = load_strategy_config(config_path)
    check_config_path(repo, config_path, config.version)
    check_cost_matches_survey(config.cost_per_side, load_spread_survey(survey_path).cost_per_side)
    version = code_version(repo)
    if version.dirty:
        raise LiveRefusedError(
            f"Uncommitted changes to tracked code or data ({', '.join(version.changed)}); commit "
            "them or check out a clean tag, then rerun."
        )
    row = LiveConfig(
        config_path=config_path.resolve().relative_to(repo.resolve()).as_posix(),
        config_sha256=sha,
        started_on=today,
        start_git_sha=version.sha,
    )
    session.add(row)
    if session.get(LiveRiskState, 1) is None:
        session.add(LiveRiskState())
    session.commit()
    session.refresh(row)
    return row


def verify_live_config(session: Session, repo: Path) -> tuple[LiveConfig, StrategyConfig]:
    """The recorded row and the config it froze, Breakout only. Refuses when there is no row or
    the file's sha256 differs from the recorded one."""
    row = session.get(LiveConfig, 1)
    if row is None:
        raise LiveRefusedError("No live config. Run `signalbench live start` first.")
    path = repo / row.config_path
    if not path.exists():
        raise LiveRefusedError(f"{row.config_path} is missing.")
    config, sha = load_strategy_config(path)
    if sha != row.config_sha256:
        raise LiveRefusedError(
            f"{row.config_path} changed: sha256 {sha[:12]} is not the recorded "
            f"{row.config_sha256[:12]}. The live config is frozen (spec 04)."
        )
    return row, config.with_setups(("breakout",))
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_start.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 5 passed; the full suite 699 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/live/start.py tests/test_live_start.py
git commit -m "feat: live start freezes the live config once, and the scan's config check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: The CLI: `live start`, `ledger tax`, `ledger split`, `ledger void-action`

**Files:**
- Modify: `src/signalbench/cli.py`
- Create: `tests/test_live_cli.py`

`ledger split` takes a CDR symbol (a CDR split) or a US symbol (a US split): spec 04 names the argument CDR, and a US split that yfinance missed needs the same correction.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_cli.py`:

```python
"""`signalbench live start` and `signalbench ledger tax|split|void-action` wiring (spec 04)."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session
from typer.testing import CliRunner

from live_helpers import add_pair, load_example, make_ledger, record_example
from paper_helpers import NEW_YORK, git
from signalbench import cli
from signalbench.cli import app
from signalbench.db.models import LiveConfig
from signalbench.live.start import LIVE_CONFIG

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]
SURVEY = "data/cdr_spread_survey.yaml"


@pytest.fixture
def ledger_cli(session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setattr(cli, "get_session", lambda: session)
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 12, 31, 18, 0, tzinfo=NEW_YORK))
    add_pair(session, "TST", "ZTST")
    return session


def test_help_lists_the_live_and_ledger_commands() -> None:
    assert "start" in runner.invoke(app, ["live", "--help"]).stdout
    listed = runner.invoke(app, ["ledger", "--help"]).stdout
    for command in ("tax", "split", "void-action"):
        assert command in listed


def test_live_start_writes_the_row_from_a_clean_repo(
    ledger_cli: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (LIVE_CONFIG, SURVEY):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes((REPO / name).read_bytes())
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "data")
    git(tmp_path, "commit", "-q", "-m", "live")
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "LIVE_CONFIG_PATH", tmp_path / LIVE_CONFIG)
    monkeypatch.setattr(cli, "SPREAD_SURVEY_PATH", tmp_path / SURVEY)
    result = runner.invoke(app, ["live", "start"])
    assert result.exit_code == 0, result.stderr
    row = ledger_cli.get(LiveConfig, 1)
    assert row is not None
    assert result.stdout == (
        f"live config: {LIVE_CONFIG} | config_sha256 {row.config_sha256[:12]} | started "
        f"2026-12-31 | code {row.start_git_sha[:12]}\n"
    )
    second = runner.invoke(app, ["live", "start"])
    assert second.exit_code == 1
    assert "already recorded" in second.stderr


def test_ledger_tax_prints_the_report_and_writes_the_csv(
    ledger_cli: Session, tmp_path: Path
) -> None:
    record_example(make_ledger(ledger_cli), load_example("acb_rebuy"))
    path = tmp_path / "tax-2026.csv"
    result = runner.invoke(app, ["ledger", "tax", "2026", "--csv", str(path)])
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("ACB report 2026: 2 dispositions\n")
    assert f"csv: {path}\n" in result.stdout
    assert path.read_text(encoding="utf-8").splitlines()[-1] == (
        "total,,,198.00,187.00,0.00,11.00,2.10,13.10,"
    )


def test_ledger_split_records_a_cdr_or_a_us_split_and_void_action_voids_it(
    ledger_cli: Session,
) -> None:
    ledger = make_ledger(ledger_cli)
    ledger.record_cash(Decimal("100.00"), date(2026, 3, 2), "deposit")
    ledger.record_fill(cdr_symbol="ZTST", side="buy", quantity=Decimal(3),
                       price_cad=Decimal(30), trade_date=date(2026, 3, 2))
    result = runner.invoke(app, ["ledger", "split", "ZTST", "2", "2026-03-09"])
    assert (result.exit_code, result.stdout) == (
        0, "corporate action 1: ZTST cdr_split 2-for-1, ex-date 2026-03-09\n"
    )
    result = runner.invoke(app, ["ledger", "split", "TST", "0.5", "2026-03-10"])
    assert result.stdout == "corporate action 2: TST us_split 0.5-for-1, ex-date 2026-03-10\n"
    result = runner.invoke(app, ["ledger", "void-action", "1", "yfinance was right after all"])
    assert (result.exit_code, result.stdout) == (
        0, "corporate action 1 voided: yfinance was right after all\n"
    )
    assert make_ledger(ledger_cli).books()["ZTST"].units == Decimal(3)


def test_ledger_split_and_void_action_refuse_with_one_line(ledger_cli: Session) -> None:
    bad = runner.invoke(app, ["ledger", "split", "ZTST", "two", "2026-03-09"])
    assert (bad.exit_code, bad.stderr) == (
        2, "RATIO must be a number and EX_DATE a YYYY-MM-DD date.\n"
    )
    refused = runner.invoke(app, ["ledger", "split", "ZTST", "2", "2026-03-09"])
    assert refused.exit_code == 1
    assert refused.stderr.startswith("ZTST had no open position or sent signal on 2026-03-09")
    missing = runner.invoke(app, ["ledger", "void-action", "9", "no such row"])
    assert (missing.exit_code, missing.stderr) == (1, "no corporate action 9 to void\n")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_live_cli.py -q`
Expected: 5 failed: exit code 2 (`No such command 'live'` or `'ledger'`) for each.

- [ ] **Step 3: Wire the commands**

In `src/signalbench/cli.py`, replace:

```python
from datetime import UTC, date, datetime
```

with:

```python
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
```

In `src/signalbench/cli.py`, replace:

```python
from signalbench.market.calendar import HISTORY_START, NyseSessions
```

with:

```python
from signalbench.live.book import LedgerError, SplitKind
from signalbench.live.ledger import Ledger
from signalbench.live.start import LIVE_CONFIG, start_live
from signalbench.live.tax import tax_csv, tax_text
from signalbench.market.calendar import HISTORY_START, NyseSessions
```

In `src/signalbench/cli.py`, replace:

```python
PAPER_REPORTS_DIR = REPO_ROOT / "reports" / "paper"
```

with:

```python
PAPER_REPORTS_DIR = REPO_ROOT / "reports" / "paper"
LIVE_CONFIG_PATH = REPO_ROOT / LIVE_CONFIG
```

Append to `src/signalbench/cli.py`:

```python
live_app = typer.Typer(help="The one live strategy, v2-none-cash (spec 04).")
app.add_typer(live_app, name="live")
ledger_app = typer.Typer(help="The live ledger: the tax report and split corrections (spec 04).")
app.add_typer(ledger_app, name="ledger")


def _ledger(session: Session) -> Ledger:
    return Ledger(session, calendar=NyseSessions(), today=_now().date())


@live_app.command("start")
def live_start() -> None:
    """Freeze data/strategy_v2-none-cash.yaml as the live config. Runs once."""
    try:
        with get_session() as session:
            row = start_live(
                session, repo=REPO_ROOT, config_path=LIVE_CONFIG_PATH,
                survey_path=SPREAD_SURVEY_PATH, today=_now().date(),
            )
            line = (
                f"live config: {row.config_path} | config_sha256 {row.config_sha256[:12]} | "
                f"started {row.started_on} | code {row.start_git_sha[:12]}"
            )
    except (ValueError, yaml.YAMLError) as error:  # LiveRefusedError, RunRefusedError, ConfigError
        typer.echo(" ".join(str(error).split()), err=True)
        raise typer.Exit(1) from None
    typer.echo(line)


@ledger_app.command("tax")
def ledger_tax(
    year: Annotated[int, typer.Argument(help="The tax year, e.g. 2026.")],
    csv_path: Annotated[
        Path | None, typer.Option("--csv", help="Also write every disposition to this CSV.")
    ] = None,
) -> None:
    """The year's ACB report: dispositions, superficial losses, and CDR splits."""
    with get_session() as session:
        report = _ledger(session).tax_report(year)
    for line in tax_text(report):
        typer.echo(line)
    if csv_path is not None:
        csv_path.write_text(tax_csv(report), encoding="utf-8")
        typer.echo(f"csv: {csv_path}")


@ledger_app.command("split")
def ledger_split(
    symbol: Annotated[str, typer.Argument(help="A CDR (ZNVD) or a US symbol (NVDA).")],
    ratio: Annotated[str, typer.Argument(help="New units per old unit: 2 for a 2-for-1.")],
    ex_date: Annotated[str, typer.Argument(help="The ex-date, YYYY-MM-DD.")],
) -> None:
    """Record a split yfinance missed (source owner)."""
    try:
        new_per_old, day = Decimal(ratio), date.fromisoformat(ex_date)
    except (InvalidOperation, ValueError):
        typer.echo("RATIO must be a number and EX_DATE a YYYY-MM-DD date.", err=True)
        raise typer.Exit(2) from None
    try:
        with get_session() as session:
            cdr = session.exec(
                select(Ticker).where(Ticker.symbol == symbol, Ticker.kind == TickerKind.cdr)
            ).first()
            kind: SplitKind = "us_split" if cdr is None else "cdr_split"
            action = _ledger(session).record_split(
                kind=kind, symbol=symbol, ex_date=day, ratio=new_per_old, source="owner"
            )
            line = f"corporate action {action.id}: {symbol} {kind} {ratio}-for-1, ex-date {day}"
    except LedgerError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    typer.echo(line)


@ledger_app.command("void-action")
def ledger_void_action(
    action_id: Annotated[int, typer.Argument(help="The corporate action's id.")],
    reason: Annotated[str, typer.Argument(help="Why it is wrong.")],
) -> None:
    """Void a wrong split; its stop rows are undone by new split rows."""
    try:
        with get_session() as session:
            _ledger(session).void_corporate_action(action_id, reason)
    except LedgerError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    typer.echo(f"corporate action {action_id} voided: {reason}")
```

- [ ] **Step 4: Run the tests and checks**

Run: `uv run pytest tests/test_live_cli.py tests/test_cli.py tests/test_paper_cli.py -q && uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 48 passed (5 new, and the 43 existing CLI and paper CLI tests); the full suite 704 passed; ruff and mypy clean.

- [ ] **Step 5: Commit**

```bash
git add src/signalbench/cli.py tests/test_live_cli.py
git commit -m "feat: signalbench live start and ledger tax, split, and void-action commands

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Docs: spec 04 implementation choices

**Files:**
- Modify: `docs/superpowers/specs/2026-09-22-swing-assistant-04-ledger-design.md`

- [ ] **Step 1: Record the implementation choices in the spec's changelog**

Append to `docs/superpowers/specs/2026-09-22-swing-assistant-04-ledger-design.md`:

```markdown
- 2026-09-30: implementation choices (plan [2026-09-30-swing-04-ledger.md](../plans/2026-09-30-swing-04-ledger.md)):
  - **Ids** are integers (`/void 12`; Telegram button data is capped at 64 bytes), and the interface takes `int` where it showed `UUID`.
  - **Columns added:** `fills.forced` (a buy recorded with `force=True`, also logged), `stop_updates.corporate_action_id` (the split a `split` row applies or undoes), and `risk_state.scale_up_history` (each scale-up result with its inputs).
  - **Splits:** `record_split` writes the `split` stop rows itself and a CDR split withdraws the CDR's `sent` signals, in one transaction; voiding writes the undoing rows. A split recorded before a position opened rescales its initial stop without a row. A split ratio of 1 is refused. `ledger split` also takes a US symbol.
  - **Stops:** `stop_in_force(signal, D)` replays the rows in the order written, leaving out raises decided at D's close or later, so a forced rescan sees the stop in force during D. `record_stop_update` computes the CDR display stop itself and takes no `reason` (split rows come from `record_split`).
  - **Pending signals** hold their slot through the close of their entry session (the owner may have bought and not reported it yet).
  - **Money:** prices, fees, and stops are stored at 4 decimals and quantities at 6; more decimals are refused, not rounded. Equity snapshots are rounded to 4 decimals before the peak and pause use them; the tax report rounds to the cent. A position with no stored CDR price is valued at its ACB.
  - **Fills:** on one trade date, fills replay in the order recorded, after a split with that ex-date. The no-margin check is the running cash from the buy on. A void that would leave a later sale oversold is refused (record the corrected fill first). A buy may link to a `sent` or `expired` signal (then `taken`), not to a skipped or withdrawn one.
  - **`live start`** also refuses uncommitted tracked code, like `paper start`, so `start_git_sha` names the code that ran.
```

- [ ] **Step 2: Run the checks**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: 704 passed; ruff and mypy clean.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-09-22-swing-assistant-04-ledger-design.md
git commit -m "docs: spec 04 implementation choices

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

> ## ⛔ Controller: stop here until the owner says go
>
> Tasks 1–13 are buildable with no real database. Tasks 14–15 touch the owner's Postgres. Run them only on the owner's go-ahead, in this order, and stop at the first surprise. `signalbench live start` is **not** run by this plan: spec 05's gate runs it once, from the worktree pinned to the `live-v1` tag, after migration `0014`, so `start_git_sha` names the code that trades.

### Task 14: ⛔ Run migration `0013` on the real database

**Files:** none (database only)

Spec 05's gate lists migrations `0013` and `0014` together; running `0013` now is harmless (spec 05's `0014` goes on top) and lets Task 15 check the tables. If the owner would rather migrate once, skip to spec 05 and run both there.

- [ ] **Step 1: Database up, migration pending**

Run: `docker compose up -d && uv run alembic current`
Expected: `0012_paper_trading (head)` among the output lines. If it shows `0011_jev_readings`, spec 07's gated migration was never run (paper trading was dropped before it): Step 2 would then run `0012` as well, creating the four empty paper tables; ask the owner before going on. Anything else: **stop and report**.

- [ ] **Step 2: Migrate**

Run: `uv run alembic upgrade 0013_live_ledger && uv run alembic current`
Expected: `Running upgrade 0012_paper_trading -> 0013_live_ledger, Spec 04 live ledger` (after `Running upgrade 0011_jev_readings -> 0012_paper_trading, Spec 07 forward paper trading` in the 0011 case), then `0013_live_ledger`. (The target is named, not `head`: spec 05's `0014`–`0016` are in the tree now and are run by spec 05's Task 14.)

### Task 15: ⛔ A read-only check of the empty ledger

**Files:** none

- [ ] **Step 1: The tax report of the empty ledger**

Run: `uv run signalbench ledger tax 2026`
Expected, exit 0, and nothing written:

```text
ACB report 2026: 0 dispositions
totals: proceeds C$0 | ACB C$0 | fees C$0 | gain/loss C$0 | superficial C$0 | allowed C$0
Not tax advice: a record-keeping aid that follows the CRA's published ACB method. Verify your return independently.
Superficial losses count only the same CDR symbol as identical property. Confirm whether a US listing of the same company held elsewhere changes this.
```

- [ ] **Step 2: Nothing else**

Do not run `live start`, `ledger split`, or `ledger void-action` here, and record no deposit: the first `/deposit 100` is spec 05's gate, through the bot.

---

## Spec coverage check

| Spec 04 item | Where |
| --- | --- |
| Live config: `live_config` row, the backtest guard, refuse a second run; the scan refuses a changed file | Task 11 (`start_live`, `verify_live_config`), Task 12 (`live start`) |
| Tables of migration `0013_live_ledger`, unique rules, append-only and never-deleted rows | Task 1; voids in Tasks 3 and 6 |
| Cash; positions by CDR with post-split units; ACB | Tasks 2, 3, 7 |
| Managed vs manual; manual holds a slot and its decisions are discarded | Tasks 4, 7, 8 |
| Strategy levels: entry session, initial and current stop, highest close, timing, exits (stop, then earnings from SEC 2.02 + Finnhub), one open exit alert, `entry_us`, CDR display levels | Tasks 4, 5, 7, 8 |
| Trade R on planned risk, equal in CAD and US terms, unchanged by splits | Task 4; Task 6 (US and CDR splits) |
| Equity, CDR mark, peak, pause, `/resume` with the peak reset, `auto_resume_sessions` ignored | Tasks 5, 7 |
| `PortfolioState` for `decide()`: positions, pending signals holding slots, cash, equity, peak, pause | Task 7 |
| Splits: US and CDR, the rows, `sent` signals withdrawn, owner corrections, voids, the scale check (3%), dividends | Task 6; Task 12 (`ledger split`, `ledger void-action`) |
| ACB and superficial losses, provisional losses, the tax report (`ledger tax YEAR [--csv PATH]`) | Tasks 2, 9, 12 |
| Scale-up check: the four criteria, active at 10 closed managed positions, stored for the record | Task 10 |
| Interface and validation | Tasks 3–7, 9, 10 (ids are `int`: see Decisions) |
| Testing: hand-checked ACB and CDR-split fixtures | Task 2 (engine), Task 3 (ACB through the ledger), Task 6 (CDR split through the ledger, with the void) |
| Gate: rebuilding positions and cash from fills and corporate actions alone | Task 7 (`test_positions_and_cash_rebuild_from_fills_and_corporate_actions_alone`) |
| Out of scope here (spec 05): the scan, Telegram, scheduling, `live start` on the real database | — |
