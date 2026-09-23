from datetime import UTC, date, datetime
from decimal import Decimal

from sqlmodel import Session

from signalbench.db.models import (
    DocType,
    EarningsEvent,
    Price,
    RawDocument,
    Ticker,
    TickerKind,
)
from signalbench.ingest.stats import collect_stats
from signalbench.ingest.text import DOCUMENT_SEPARATOR


def test_collect_stats(session: Session) -> None:
    us = Ticker(symbol="NVDA", company_name="Nvidia", kind=TickerKind.us_stock, active=True)
    idle = Ticker(symbol="THIN", company_name="Thin", kind=TickerKind.us_stock, active=False)
    cdr = Ticker(symbol="ZNVD", company_name="Nvidia", kind=TickerKind.cdr)
    session.add_all([us, idle, cdr])
    session.commit()
    for ticker in (us, cdr):
        session.add(
            Price(
                ticker_id=ticker.id,
                date=date(2026, 9, 21),
                open=Decimal(1),
                high=Decimal(1),
                low=Decimal(1),
                close=Decimal(1),
                adj_close=Decimal(1),
                volume=1,
            )
        )
    accepted = datetime(2024, 1, 15, 21, 30, tzinfo=UTC)
    session.add_all(
        [
            RawDocument(source="sec_edgar", external_id="a", doc_type=DocType.eight_k, raw_text="x", published_at=accepted, acceptance_at=accepted, text=f"p{DOCUMENT_SEPARATOR}e"),
            RawDocument(source="sec_edgar", external_id="b", doc_type=DocType.eight_k, raw_text="x", published_at=accepted, acceptance_at=accepted, text="p"),
            RawDocument(source="sec_edgar", external_id="c", doc_type=DocType.eight_k, raw_text="x", published_at=datetime(2015, 1, 1, tzinfo=UTC), acceptance_at=datetime(2015, 1, 1, tzinfo=UTC)),
            RawDocument(source="finnhub", external_id="n", doc_type=DocType.news, raw_text="h", published_at=accepted),
            EarningsEvent(ticker_id=us.id, event_date=date(2024, 1, 15), source="sec_2.02"),
        ]
    )
    session.commit()

    assert dict(collect_stats(session, since=date(2016, 1, 1))) == {
        "us_stocks": 2,
        "active_us_stocks": 1,
        "cdrs": 1,
        "cdrs_with_prices": 1,
        "price_rows": 2,
        "eight_ks_since": 2,
        "eight_ks_with_exhibit_text": 1,
        "news_rows": 1,
        "earnings_events": 1,
    }
