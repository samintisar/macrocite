"""The app's database engine: a database that accepts a connection but never answers makes
the connection fail after a timeout instead of hanging (the bot froze that way on 2026-10-01,
started while Docker's port was up and Postgres was not)."""

import socket
import threading
import time
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from signalbench.db.session import CONNECT_TIMEOUT_SECONDS, make_engine


@pytest.fixture
def silent_port() -> Iterator[int]:
    """A port that accepts TCP connections (into the OS backlog) and never speaks Postgres."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    try:
        yield server.getsockname()[1]
    finally:
        server.close()


def test_a_silent_database_fails_after_the_timeout_instead_of_hanging(
    silent_port: int,
) -> None:
    engine = make_engine(
        f"postgresql+psycopg://u:p@127.0.0.1:{silent_port}/db", connect_timeout=1
    )
    outcome: dict[str, object] = {}

    def connect() -> None:
        started = time.monotonic()
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except OperationalError as error:
            outcome["error"] = error
        outcome["seconds"] = time.monotonic() - started

    worker = threading.Thread(target=connect, daemon=True)
    worker.start()
    worker.join(15)
    assert not worker.is_alive(), "the connection hung instead of timing out"
    assert isinstance(outcome["error"], OperationalError)
    assert outcome["seconds"] < 10


def test_the_default_timeout_is_ten_seconds_and_sqlite_needs_none() -> None:
    assert CONNECT_TIMEOUT_SECONDS == 10
    with make_engine("sqlite://").connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1
