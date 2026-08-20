from collections.abc import Generator

import pytest
from sqlmodel import Session, SQLModel, create_engine

from signalbench.db import models as _models  # noqa: F401  # register tables


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
