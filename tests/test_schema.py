from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import DateTime
from sqlalchemy.exc import IntegrityError
from sqlalchemy.types import TypeDecorator
from sqlmodel import Session, select

from signalbench.db.models import DocumentTicker, Price, RawDocument, Ticker


def test_document_has_no_ticker_id_column() -> None:
    assert "ticker_id" not in RawDocument.model_fields


def test_published_at_uses_timezone_aware_datetime_column() -> None:
    published_at_type = RawDocument.metadata.tables["raw_documents"].c.published_at.type
    assert isinstance(published_at_type, TypeDecorator)
    assert isinstance(published_at_type.impl, DateTime)
    assert published_at_type.impl.timezone is True


def test_unique_source_external_id_rejects_duplicate(session: Session) -> None:
    doc = RawDocument(
        source="sec_edgar",
        external_id="0000320193-24-000001",
        doc_type="eight_k",
        url="https://www.sec.gov/example",
        title="Item 2.02",
        raw_text="<html>8-K</html>",
        published_at=datetime(2024, 1, 15, tzinfo=UTC),
    )
    session.add(doc)
    session.commit()
    session.add(
        RawDocument(
            source="sec_edgar",
            external_id="0000320193-24-000001",
            doc_type="eight_k",
            url="https://www.sec.gov/example-2",
            title="dup",
            raw_text="x",
            published_at=datetime(2024, 1, 16, tzinfo=UTC),
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_one_document_can_link_two_tickers(session: Session) -> None:
    aapl = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    msft = Ticker(symbol="MSFT", company_name="Microsoft Corporation", active=True)
    session.add(aapl)
    session.add(msft)
    session.commit()
    session.refresh(aapl)
    session.refresh(msft)
    doc = RawDocument(
        source="sec_edgar",
        external_id="0000320193-24-000002",
        doc_type="eight_k",
        raw_text="mentions both",
        published_at=datetime(2024, 2, 1, tzinfo=UTC),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    session.add(DocumentTicker(document_id=doc.id, ticker_id=aapl.id))
    session.add(DocumentTicker(document_id=doc.id, ticker_id=msft.id))
    session.commit()
    links = session.exec(select(DocumentTicker).where(DocumentTicker.document_id == doc.id)).all()
    assert {link.ticker_id for link in links} == {aapl.id, msft.id}


def test_price_requires_adj_close(session: Session) -> None:
    ticker = Ticker(symbol="NVDA", company_name="NVIDIA Corporation", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    session.add(
        Price(
            ticker_id=ticker.id,
            date=date(2024, 1, 2),
            open=Decimal("100.0000"),
            high=Decimal("101.0000"),
            low=Decimal("99.0000"),
            close=Decimal("100.5000"),
            adj_close=Decimal("100.5000"),
            volume=1_000_000,
        )
    )
    session.commit()
    row = session.exec(select(Price)).one()
    assert row.adj_close == Decimal("100.5000")
