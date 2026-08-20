from datetime import date
from decimal import Decimal

from sqlmodel import Session, select

from signalbench.db.models import Price, Ticker
from signalbench.ingest.prices import DailyBar, ingest_daily_prices


def test_ingest_writes_adj_close_and_is_idempotent(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)

    bars = [
        DailyBar(
            date=date(2024, 1, 2),
            open=Decimal("185.0000"),
            high=Decimal("186.0000"),
            low=Decimal("184.0000"),
            close=Decimal("185.5000"),
            adj_close=Decimal("185.2000"),
            volume=50_000_000,
        )
    ]

    def fetch(_symbol: str) -> list[DailyBar]:
        return bars

    n1 = ingest_daily_prices(session, ticker, fetch=fetch)
    n2 = ingest_daily_prices(session, ticker, fetch=fetch)
    assert n1 == 1
    assert n2 == 0
    row = session.exec(select(Price)).one()
    assert row.adj_close == Decimal("185.2000")
    assert row.close == Decimal("185.5000")
    assert row.ticker_id == ticker.id
