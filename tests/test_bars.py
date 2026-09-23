from datetime import date
from decimal import Decimal

from sqlmodel import Session

from signalbench.db.models import Price, Ticker
from signalbench.market.bars import adjusted_bars


def test_adjusted_bars_scale_ohl_by_adj_factor_in_date_order(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple")
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    for day, adj in ((date(2024, 1, 3), "100.0000"), (date(2024, 1, 2), "200.0000")):
        session.add(
            Price(
                ticker_id=ticker.id,
                date=day,
                open=Decimal("210.0000"),
                high=Decimal("220.0000"),
                low=Decimal("190.0000"),
                close=Decimal("200.0000"),
                adj_close=Decimal(adj),
                volume=1_000,
            )
        )
    session.commit()

    bars = adjusted_bars(session, ticker.id)
    assert [bar.date for bar in bars] == [date(2024, 1, 2), date(2024, 1, 3)]
    assert (bars[0].open, bars[0].high, bars[0].low, bars[0].close) == (210.0, 220.0, 190.0, 200.0)
    assert (bars[1].open, bars[1].high, bars[1].low, bars[1].close) == (105.0, 110.0, 95.0, 100.0)
    assert [bar.date for bar in adjusted_bars(session, ticker.id, start=date(2024, 1, 3))] == [
        date(2024, 1, 3)
    ]
    assert [bar.date for bar in adjusted_bars(session, ticker.id, end=date(2024, 1, 2))] == [
        date(2024, 1, 2)
    ]
