"""One `paper run` at a time (spec 07, Commands step 1): a Postgres session advisory lock."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text

PAPER_RUN_LOCK = 2026_0929_07  # an arbitrary bigint that names the paper-run lock


@contextmanager
def advisory_lock(engine: Engine, key: int = PAPER_RUN_LOCK) -> Iterator[bool]:
    """Yield True when this process holds the lock, False when another run does.

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
