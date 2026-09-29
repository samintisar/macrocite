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
