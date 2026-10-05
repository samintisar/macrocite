from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from signalbench.config import settings

# While Docker starts, its port can accept a connection that Postgres never answers; without a
# timeout that connect hangs forever (it froze the bot on 2026-10-01). Ten seconds, then an error.
CONNECT_TIMEOUT_SECONDS = 10


def make_engine(url: str, connect_timeout: int = CONNECT_TIMEOUT_SECONDS) -> Engine:
    """An engine whose Postgres connections give up after `connect_timeout` seconds."""
    args = {"connect_timeout": connect_timeout} if url.startswith("postgresql") else {}
    return create_engine(url, pool_pre_ping=True, connect_args=args)


engine = make_engine(settings.database_url)


def get_session() -> Session:
    return Session(engine)
