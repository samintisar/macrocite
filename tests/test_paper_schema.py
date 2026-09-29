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
