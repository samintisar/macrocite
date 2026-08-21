from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from signalbench.db.models import EventType, RawDocument, Signal, Ticker


def _doc_and_ticker(session: Session) -> tuple[RawDocument, Ticker]:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    doc = RawDocument(
        source="sec_edgar",
        external_id="acc-1",
        doc_type="eight_k",
        raw_text="Apple reports earnings.",
        published_at=datetime(2024, 1, 15, tzinfo=UTC),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc, ticker


def test_unique_document_ticker_model_prompt(session: Session) -> None:
    doc, ticker = _doc_and_ticker(session)
    kwargs = {
        "document_id": doc.id,
        "ticker_id": ticker.id,
        "model_version": "deepseek-ai/DeepSeek-V4-Flash-0731",
        "prompt_version": "v1",
        "sentiment": 0.4,
        "event_type": EventType.earnings,
        "confidence": 0.8,
        "rationale": "Beat on EPS.",
        "raw_llm_response": {"ok": True},
    }
    session.add(Signal(**kwargs))
    session.commit()
    session.add(Signal(**kwargs))
    with pytest.raises(IntegrityError):
        session.commit()


def test_new_prompt_version_inserts_second_row(session: Session) -> None:
    doc, ticker = _doc_and_ticker(session)
    session.add(
        Signal(
            document_id=doc.id,
            ticker_id=ticker.id,
            model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
            prompt_version="v1",
            sentiment=0.4,
            event_type=EventType.earnings,
            confidence=0.8,
            rationale="v1",
        )
    )
    session.add(
        Signal(
            document_id=doc.id,
            ticker_id=ticker.id,
            model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
            prompt_version="v2",
            sentiment=0.5,
            event_type=EventType.earnings,
            confidence=0.7,
            rationale="v2",
        )
    )
    session.commit()
    rows = session.exec(select(Signal)).all()
    assert len(rows) == 2


def test_sentiment_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        Signal(
            document_id=__import__("uuid").uuid4(),
            ticker_id=__import__("uuid").uuid4(),
            model_version="m",
            prompt_version="v1",
            sentiment=2.0,
            event_type=EventType.other,
            confidence=0.5,
            rationale="bad",
        )
