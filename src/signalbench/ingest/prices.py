from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlmodel import Session, select

from signalbench.db.models import Price, Ticker


@dataclass(frozen=True)
class DailyBar:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal
    volume: int


def ingest_daily_prices(
    session: Session,
    ticker: Ticker,
    fetch: Callable[[str], list[DailyBar]],
) -> int:
    created = 0
    for bar in fetch(ticker.symbol):
        existing = session.exec(
            select(Price).where(Price.ticker_id == ticker.id, Price.date == bar.date)
        ).first()
        if existing is not None:
            continue
        session.add(
            Price(
                ticker_id=ticker.id,
                date=bar.date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                adj_close=bar.adj_close,
                volume=bar.volume,
            )
        )
        created += 1
    session.commit()
    return created


def fetch_yfinance_daily(symbol: str) -> list[DailyBar]:
    import yfinance as yf

    frame = yf.Ticker(symbol).history(period="max", auto_adjust=False, timeout=30)
    bars: list[DailyBar] = []
    for idx, row in frame.iterrows():
        adj = row["Adj Close"] if "Adj Close" in row.index else row["Close"]
        bars.append(
            DailyBar(
                date=idx.date(),
                open=Decimal(str(round(float(row["Open"]), 4))),
                high=Decimal(str(round(float(row["High"]), 4))),
                low=Decimal(str(round(float(row["Low"]), 4))),
                close=Decimal(str(round(float(row["Close"]), 4))),
                adj_close=Decimal(str(round(float(adj), 4))),
                volume=int(row["Volume"]),
            )
        )
    return bars
