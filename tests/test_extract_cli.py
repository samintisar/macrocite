import sys
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from sqlmodel import Session, SQLModel, create_engine
from typer.testing import CliRunner

from signalbench.cli import app
from signalbench.db import models as _models  # noqa: F401  # register tables

runner = CliRunner()


def test_extract_help() -> None:
    result = runner.invoke(app, ["extract", "--help"])
    assert result.exit_code == 0
    assert "--llm" in result.stdout or "fake" in result.stdout.lower() or "extract" in result.stdout


def test_extract_fake_does_not_import_together(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def _session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    monkeypatch.setattr("signalbench.cli.get_session", _session)
    sys.modules.pop("signalbench.extraction.together_llm", None)
    sys.modules.pop("together", None)

    result = runner.invoke(app, ["extract", "--llm", "fake"])
    assert result.exit_code == 0, result.stdout + str(result.exception)
    assert "together" not in sys.modules
    assert "signalbench.extraction.together_llm" not in sys.modules
