from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from signalbench.db.models import DocType, JevReading, RawDocument, Ticker, TickerKind


def _document_and_ticker(session: Session) -> tuple[RawDocument, Ticker]:
    ticker = Ticker(symbol="AAA", company_name="Aaa Corp", kind=TickerKind.us_stock)
    document = RawDocument(
        source="sec_edgar",
        external_id="0000000000-24-000001",
        doc_type=DocType.eight_k,
        raw_text="<html></html>",
        text="Results.",
        published_at=datetime(2024, 1, 2, tzinfo=UTC),
    )
    session.add_all([ticker, document])
    session.commit()
    return document, ticker


def _reading(document: RawDocument, ticker: Ticker, question_set: str = "q1") -> JevReading:
    return JevReading(
        document_id=document.id,
        ticker_id=ticker.id,
        model_requested="typesafe/jev-1.13",
        model_resolved="typesafe/jev-1.13-20260917",
        question_set=question_set,
        response_id="gen-dec-1",
        p_negative=0.1,
        p_neutral=0.2,
        p_positive=0.7,
        event_type="earnings",
        p_routine=0.05,
        answers={"impact": {"type": "choice", "choice": "positive"}},
        input_tokens=500,
        cost_usd=0.000021,
        latency_ms=850,
    )


def test_a_reading_round_trips(session: Session) -> None:
    document, ticker = _document_and_ticker(session)
    session.add(_reading(document, ticker))
    session.commit()
    stored = session.exec(select(JevReading)).one()
    assert stored.p_positive == 0.7 and stored.event_type == "earnings"
    assert stored.answers == {"impact": {"type": "choice", "choice": "positive"}}
    assert stored.read_at.tzinfo is not None


def test_one_reading_per_document_ticker_model_and_question_set(session: Session) -> None:
    document, ticker = _document_and_ticker(session)
    session.add(_reading(document, ticker))
    session.add(_reading(document, ticker, question_set="q2"))  # a new question set is allowed
    session.commit()
    session.add(_reading(document, ticker))
    with pytest.raises(IntegrityError):
        session.commit()
