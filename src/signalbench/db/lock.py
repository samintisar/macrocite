"""One run at a time: a Postgres session advisory lock."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text


@contextmanager
def advisory_lock(engine: Engine, key: int) -> Iterator[bool]:
    """Yield True when this process holds the lock named `key`, False when another run does.

    The lock lives on its own connection, held open for the whole run, so the run's
    transactions do not release it; Postgres drops it if the process dies.
    """
    with engine.connect() as connection:
        held = bool(
            connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        )
        connection.commit()
        try:
            yield held
        finally:
            if held:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                connection.commit()
