from datetime import UTC, datetime

from sqlmodel import Session, select

from signalbench.db.models import RawDocument, Signal, Ticker
from signalbench.extraction.extract import extract_document
from signalbench.extraction.schema import (
    EventTypeName,
    ExtractedClaim,
    ExtractionResult,
)


class FakeLLM:
    def __init__(self, result: ExtractionResult) -> None:
        self.result = result
        self.calls = 0

    def complete(self, prompt: str, raw_text: str) -> ExtractionResult:
        self.calls += 1
        assert "8-K" in raw_text or "earnings" in raw_text.lower() or len(raw_text) > 0
        assert len(prompt) > 0
        return self.result


def test_extract_persists_rationale_and_raw_payload(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    doc = RawDocument(
        source="sec_edgar",
        external_id="acc-extract-1",
        doc_type="eight_k",
        raw_text="Apple reports earnings beat.",
        published_at=datetime(2024, 1, 15, tzinfo=UTC),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    result = ExtractionResult(
        claims=[
            ExtractedClaim(
                ticker="AAPL",
                sentiment=0.7,
                event_type=EventTypeName.earnings,
                confidence=0.85,
                rationale="Beat on EPS.",
            )
        ]
    )
    llm = FakeLLM(result)
    created = extract_document(
        session,
        document=doc,
        llm=llm,
        model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
        prompt_version="v1",
    )
    assert created == 1
    row = session.exec(select(Signal)).one()
    assert row.rationale == "Beat on EPS."
    assert row.raw_llm_response is not None
    assert row.model_version == "deepseek-ai/DeepSeek-V4-Flash-0731"
    assert row.prompt_version == "v1"
    assert row.ticker_id == ticker.id
    assert llm.calls == 1


def test_same_key_does_not_duplicate(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    doc = RawDocument(
        source="sec_edgar",
        external_id="acc-extract-2",
        doc_type="eight_k",
        raw_text="x",
        published_at=datetime(2024, 1, 15, tzinfo=UTC),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    result = ExtractionResult(
        claims=[
            ExtractedClaim(
                ticker="AAPL",
                sentiment=0.1,
                event_type=EventTypeName.other,
                confidence=0.5,
                rationale="neutral.",
            )
        ]
    )
    extract_document(session, doc, FakeLLM(result), "deepseek-ai/DeepSeek-V4-Flash-0731", "v1")
    extract_document(session, doc, FakeLLM(result), "deepseek-ai/DeepSeek-V4-Flash-0731", "v1")
    assert len(session.exec(select(Signal)).all()) == 1


def test_one_document_two_ticker_signals(session: Session) -> None:
    aapl = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    msft = Ticker(symbol="MSFT", company_name="Microsoft Corporation", active=True)
    session.add(aapl)
    session.add(msft)
    session.commit()
    doc = RawDocument(
        source="sec_edgar",
        external_id="acc-multi",
        doc_type="eight_k",
        raw_text="Apple and Microsoft announced a partnership.",
        published_at=datetime(2024, 3, 1, tzinfo=UTC),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    result = ExtractionResult(
        claims=[
            ExtractedClaim(
                ticker="AAPL",
                sentiment=0.3,
                event_type=EventTypeName.product,
                confidence=0.6,
                rationale="Partnership named.",
            ),
            ExtractedClaim(
                ticker="MSFT",
                sentiment=0.3,
                event_type=EventTypeName.product,
                confidence=0.6,
                rationale="Partnership named.",
            ),
        ]
    )
    created = extract_document(
        session, doc, FakeLLM(result), "deepseek-ai/DeepSeek-V4-Flash-0731", "v1"
    )
    assert created == 2
    rows = session.exec(select(Signal).where(Signal.document_id == doc.id)).all()
    assert {row.ticker_id for row in rows} == {aapl.id, msft.id}
