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
