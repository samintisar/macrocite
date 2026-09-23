import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlmodel import Session, col, select

from signalbench.db.models import Price, Ticker

logger = logging.getLogger(__name__)

# 10 calendar days always covers at least 5 sessions, so late adjustments are picked up.
REFETCH_CALENDAR_DAYS = 10


@dataclass(frozen=True)
class DailyBar:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal
    volume: int


@dataclass(frozen=True)
class PriceIngestResult:
    created: int
    updated: int
    rejected: int


PriceFetcher = Callable[[str, date], list[DailyBar]]


def validate_bar(bar: DailyBar) -> str | None:
    if min(bar.open, bar.high, bar.low, bar.close, bar.adj_close) <= 0:
        return "non_positive_price"
    if bar.high < bar.low:
        return "high_below_low"
    if bar.volume < 0:
        return "negative_volume"
    return None


def fetch_start(session: Session, ticker: Ticker, history_start: date) -> date:
    last: date | None = session.exec(
        select(func.max(Price.date)).where(Price.ticker_id == ticker.id)
    ).one()
    if last is None:
        return history_start
    return max(history_start, last - timedelta(days=REFETCH_CALENDAR_DAYS))


def ingest_daily_prices(
    session: Session,
    ticker: Ticker,
    fetch: PriceFetcher,
    history_start: date,
) -> PriceIngestResult:
    start = fetch_start(session, ticker, history_start)
    symbol = ticker.price_symbol or ticker.symbol
    existing = {
        row.date: row
        for row in session.exec(
            select(Price).where(Price.ticker_id == ticker.id, col(Price.date) >= start)
        ).all()
    }
    created = updated = rejected = 0
    for bar in fetch(symbol, start):
        reason = validate_bar(bar)
        if reason is not None:
            logger.warning("Rejected %s bar on %s: %s", symbol, bar.date, reason)
            rejected += 1
            continue
        row = existing.get(bar.date)
        if row is None:
            row = Price(
                ticker_id=ticker.id,
                date=bar.date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                adj_close=bar.adj_close,
                volume=bar.volume,
            )
            existing[bar.date] = row
            created += 1
        elif _row_values(row) != _bar_values(bar):
            row.open = bar.open
            row.high = bar.high
            row.low = bar.low
            row.close = bar.close
            row.adj_close = bar.adj_close
            row.volume = bar.volume
            updated += 1
        else:
            continue
        session.add(row)
    session.commit()
    return PriceIngestResult(created=created, updated=updated, rejected=rejected)


def _row_values(row: Price) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, int]:
    return (row.open, row.high, row.low, row.close, row.adj_close, row.volume)


def _bar_values(bar: DailyBar) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, int]:
    return (bar.open, bar.high, bar.low, bar.close, bar.adj_close, bar.volume)


def fetch_yfinance_daily(symbol: str, start: date) -> list[DailyBar]:
    import yfinance as yf

    frame = yf.Ticker(symbol).history(start=start.isoformat(), auto_adjust=False, timeout=30)
    bars: list[DailyBar] = []
    for idx, row in frame.iterrows():
        adj = row["Adj Close"] if "Adj Close" in row.index else row["Close"]
        values = [
            float(row["Open"]),
            float(row["High"]),
            float(row["Low"]),
            float(row["Close"]),
            float(adj),
            float(row["Volume"]),
        ]
        # yfinance returns NaN for the current, unfinished session.
        if any(math.isnan(value) for value in values):
            continue
        bars.append(
            DailyBar(
                date=idx.date(),
                open=_decimal(values[0]),
                high=_decimal(values[1]),
                low=_decimal(values[2]),
                close=_decimal(values[3]),
                adj_close=_decimal(values[4]),
                volume=int(values[5]),
            )
        )
    return bars


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))
