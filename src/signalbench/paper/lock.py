"""One `paper run` at a time (spec 07, Commands step 1): a Postgres session advisory lock."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine

from signalbench.db import lock

PAPER_RUN_LOCK = 2026_0929_07  # an arbitrary bigint that names the paper-run lock


@contextmanager
def advisory_lock(engine: Engine, key: int = PAPER_RUN_LOCK) -> Iterator[bool]:
    with lock.advisory_lock(engine, key) as held:
        yield held
