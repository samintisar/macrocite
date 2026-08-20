from sqlmodel import Session, create_engine

from signalbench.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)


def get_session() -> Session:
    return Session(engine)
