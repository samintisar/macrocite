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


def test_extract_skips_llm_when_signal_exists_for_current_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from signalbench.config import settings
    from signalbench.db.models import EventType, RawDocument, Signal, Ticker
    from signalbench.extraction.schema import ExtractionResult

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    with Session(engine) as seed:
        ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
        seed.add(ticker)
        seed.commit()
        seed.refresh(ticker)
        extracted = RawDocument(
            source="sec_edgar",
            external_id="acc-already",
            doc_type="eight_k",
            raw_text="already extracted",
            published_at=datetime(2024, 1, 15, tzinfo=UTC),
        )
        pending = RawDocument(
            source="sec_edgar",
            external_id="acc-pending",
            doc_type="eight_k",
            raw_text="needs extract",
            published_at=datetime(2024, 1, 16, tzinfo=UTC),
        )
        seed.add(extracted)
        seed.add(pending)
        seed.commit()
        seed.refresh(extracted)
        seed.refresh(pending)
        seed.add(
            Signal(
                document_id=extracted.id,
                ticker_id=ticker.id,
                model_version=settings.model_version,
                prompt_version=settings.prompt_version,
                sentiment=0.4,
                event_type=EventType.earnings,
                confidence=0.8,
                rationale="Beat on EPS.",
                raw_llm_response={"ok": True},
            )
        )
        seed.commit()

    @contextmanager
    def _session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    class CountingLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt: str, raw_text: str) -> ExtractionResult:
            self.calls += 1
            return ExtractionResult(claims=[])

    llm = CountingLLM()
    monkeypatch.setattr("signalbench.cli.get_session", _session)
    monkeypatch.setattr("signalbench.cli._llm_client", lambda _choice: llm)
    monkeypatch.delenv("TOGETHER_API_KEY", raising=False)

    result = runner.invoke(app, ["extract", "--llm", "fake"])
    assert result.exit_code == 0, result.stdout + str(result.exception)
    assert llm.calls == 1
