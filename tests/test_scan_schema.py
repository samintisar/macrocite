"""Spec 05 tables: `scan_runs` and `bot_heartbeat`, and migration 0014 that creates them."""

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, col, select

from signalbench.db.models import BotHeartbeat, ScanHold, ScanRun

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0014_scan_runs.py"
TABLES = ("scan_runs", "bot_heartbeat")
NOW = datetime(2026, 10, 2, 21, 5, tzinfo=UTC)


def test_a_scan_run_keeps_its_steps_warnings_and_counts(session: Session) -> None:
    session.add(
        ScanRun(as_of=date(2026, 10, 2), started_at=NOW, status="failed", failed_step=2,
                error="QQQ has no bar", warnings=["earnings calendar FAILED"],
                counts={"signals": 1}, git_sha="a" * 40, git_dirty=False,
                config_sha256="b" * 64)
    )
    session.add(ScanRun(as_of=date(2026, 10, 5), started_at=NOW, status="running"))
    session.commit()
    session.expire_all()
    failed, running = session.exec(select(ScanRun).order_by(col(ScanRun.id))).all()
    assert (failed.failed_step, failed.warnings, failed.counts) == (
        2, ["earnings calendar FAILED"], {"signals": 1}
    )
    assert failed.started_at == NOW
    assert (running.finished_at, running.warnings, running.counts, running.git_sha) == (
        None, [], {}, None
    )


def test_the_heartbeat_is_one_row(session: Session) -> None:
    session.add(BotHeartbeat(beat_at=NOW))
    session.commit()
    session.add(BotHeartbeat(beat_at=NOW))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    [beat] = session.exec(select(BotHeartbeat)).all()
    assert (beat.id, beat.beat_at) == (1, NOW)


def test_migration_0014_creates_the_model_columns_and_drops_them_again() -> None:
    spec = importlib.util.spec_from_file_location("migration_0014", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        inspector = inspect(connection)
        created = {
            table: [(c["name"], bool(c["nullable"])) for c in inspector.get_columns(table)]
            for table in TABLES
        }
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        left = inspect(connection).get_table_names()
    for table in TABLES:
        model = SQLModel.metadata.tables[table]
        assert created[table] == [(c.name, bool(c.nullable)) for c in model.columns], table
    assert left == []


def test_migration_0015_creates_the_scan_holds_table_and_drops_it_again() -> None:
    path = MIGRATION.with_name("0015_scan_holds.py")
    spec = importlib.util.spec_from_file_location("migration_0015", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == "0014_scan_runs"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
        inspector = inspect(connection)
        created = [(c["name"], bool(c["nullable"])) for c in inspector.get_columns("scan_holds")]
        unique = [u["column_names"] for u in inspector.get_unique_constraints("scan_holds")]
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
        left = inspect(connection).get_table_names()
    model = SQLModel.metadata.tables["scan_holds"]
    assert created == [(c.name, bool(c.nullable)) for c in model.columns]
    assert unique == [["us_symbol", "session"]]
    assert left == []


def test_a_held_session_is_unique_per_symbol(session: Session) -> None:
    session.add(ScanHold(us_symbol="NVDA", session=date(2026, 10, 9)))
    session.commit()
    session.add(ScanHold(us_symbol="NVDA", session=date(2026, 10, 9)))
    with pytest.raises(IntegrityError):
        session.commit()
