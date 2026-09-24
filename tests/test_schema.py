from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import DateTime
from sqlalchemy.exc import IntegrityError
from sqlalchemy.types import TypeDecorator
from sqlmodel import Session, select

from signalbench.db.models import (
    BacktestRun,
    DocType,
    DocumentTicker,
    EarningsEvent,
    Price,
    RawDocument,
    Ticker,
    TickerKind,
)


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


def test_price_volume_column_is_bigint() -> None:
    from sqlalchemy import BigInteger

    assert isinstance(Price.__table__.c.volume.type, BigInteger)


def test_ticker_kind_defaults_to_us_stock(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple")
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    assert ticker.kind is TickerKind.us_stock
    assert ticker.price_symbol is None
    assert ticker.us_ticker_id is None


def test_cdr_links_to_its_us_ticker(session: Session) -> None:
    us = Ticker(symbol="NVDA", company_name="Nvidia")
    session.add(us)
    session.commit()
    session.refresh(us)
    session.add(
        Ticker(
            symbol="ZNVD",
            company_name="Nvidia",
            kind=TickerKind.cdr,
            price_symbol="ZNVD.NE",
            us_ticker_id=us.id,
        )
    )
    session.commit()
    cdr = session.exec(select(Ticker).where(Ticker.symbol == "ZNVD")).one()
    assert cdr.kind is TickerKind.cdr
    assert cdr.us_ticker_id == us.id
    assert cdr.price_symbol == "ZNVD.NE"


def test_raw_document_new_fields(session: Session) -> None:
    accepted = datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)
    filing = RawDocument(
        source="sec_edgar",
        external_id="0000320193-26-000018",
        doc_type=DocType.eight_k,
        raw_text="<html></html>",
        published_at=accepted,
        acceptance_at=accepted,
        items="2.02,9.01",
        text="Item 2.02 Results of Operations",
    )
    news = RawDocument(
        source="finnhub",
        external_id="123",
        doc_type=DocType.news,
        raw_text="Headline",
        published_at=accepted,
    )
    session.add(filing)
    session.add(news)
    session.commit()
    session.refresh(filing)
    session.refresh(news)
    assert filing.acceptance_at == accepted
    assert filing.items == "2.02,9.01"
    assert filing.text == "Item 2.02 Results of Operations"
    assert news.acceptance_at is None
    assert news.text is None


def test_earnings_event_unique_per_ticker_date_source(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple")
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    session.add(EarningsEvent(ticker_id=ticker.id, event_date=date(2026, 7, 30), source="sec_2.02"))
    session.commit()
    session.add(EarningsEvent(ticker_id=ticker.id, event_date=date(2026, 7, 30), source="sec_2.02"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_backtest_run_round_trips_json(session: Session) -> None:
    run = BacktestRun(
        strategy_version="v1",
        config_sha256="a" * 64,
        git_sha="b" * 40,
        setup="pullback",
        jev_mode="off",
        start_date=date(2012, 1, 3),
        end_date=date(2026, 9, 23),
        data_fingerprint="c" * 64,
        metrics={"trades": 31, "skips_by_reason": {"regime": 4}},
        pass_bar={"trades": {"value": 31.0, "threshold": 30.0, "passed": True}},
        passed=True,
        trade_log={"trades": [], "events": [{"date": "2012-01-04", "event": "pause"}]},
    )
    session.add(run)
    session.commit()
    stored = session.exec(select(BacktestRun)).one()
    assert stored.metrics["skips_by_reason"] == {"regime": 4}
    assert stored.pass_bar["trades"]["passed"] is True
    assert stored.trade_log["events"][0]["event"] == "pause"
    assert stored.run_at.tzinfo is not None
