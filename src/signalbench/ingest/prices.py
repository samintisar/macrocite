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
# history_start can fall on a weekend or holiday; the first session is a few days later.
HISTORY_START_SLACK_DAYS = 7


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


@dataclass(frozen=True)
class Split:
    ex_date: date
    ratio: float  # new shares per old share: 2.0 for a 2-for-1 split, 0.1 for a 1-for-10 reverse


def validate_bar(bar: DailyBar) -> str | None:
    if min(bar.open, bar.high, bar.low, bar.close, bar.adj_close) <= 0:
        return "non_positive_price"
    if bar.high < bar.low:
        return "high_below_low"
    if bar.volume < 0:
        return "negative_volume"
    return None


def fetch_start(
    session: Session, ticker: Ticker, history_start: date, full: bool = False
) -> date:
    """Refetch the last few days of stored history, or everything when `full` is set.

    Stored history that starts well after `history_start` (rows from the old 2y fetch, or a
    lowered start) is refetched in full. A name listed after `history_start` also refetches in
    full each run: one request, and unchanged rows are not written.
    """
    if full:
        return history_start
    first, last = session.exec(
        select(func.min(Price.date), func.max(Price.date)).where(Price.ticker_id == ticker.id)
    ).one()
    if first is None or last is None:
        return history_start
    if first > history_start + timedelta(days=HISTORY_START_SLACK_DAYS):
        return history_start
    return max(history_start, last - timedelta(days=REFETCH_CALENDAR_DAYS))


def ingest_daily_prices(
    session: Session,
    ticker: Ticker,
    fetch: PriceFetcher,
    history_start: date,
    full: bool = False,
) -> PriceIngestResult:
    """Fetch, then write in one commit. When the refetch window shows that older closes changed
    (a dividend or split rescaled the whole history), the whole history is fetched first and
    written instead, so a failed full fetch writes nothing: the stored history never mixes two
    scales, and the next run sees the same change and tries again."""
    start = fetch_start(session, ticker, history_start, full=full)
    symbol = ticker.price_symbol or ticker.symbol
    existing = _stored(session, ticker, start)
    bars = fetch(symbol, start)
    if not full and _history_rescaled(existing, bars):
        logger.info("%s history was rescaled; refetching from %s", symbol, history_start)
        existing = _stored(session, ticker, history_start)
        bars = fetch(symbol, history_start)
    created = updated = rejected = 0
    for bar in bars:
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


def _stored(session: Session, ticker: Ticker, start: date) -> dict[date, Price]:
    return {
        row.date: row
        for row in session.exec(
            select(Price).where(Price.ticker_id == ticker.id, col(Price.date) >= start)
        ).all()
    }


def _history_rescaled(existing: dict[date, Price], bars: list[DailyBar]) -> bool:
    """The newest stored row may be a partial session; a change to an older close means a
    dividend or split rescaled the whole history, not just the refetch window."""
    if not existing:
        return False
    last_complete = max(existing)
    for bar in bars:
        row = existing.get(bar.date)
        if (
            row is not None
            and validate_bar(bar) is None
            and bar.date < last_complete
            and (row.close, row.adj_close) != (bar.close, bar.adj_close)
        ):
            return True
    return False


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


def fetch_yfinance_splits(symbol: str, since: date) -> list[Split]:
    """The splits yfinance lists with an ex-date after `since`. Its prices (`close` and
    `adj_close` alike) are already divided by every later split, on every date."""
    import yfinance as yf

    frame = yf.Ticker(symbol).history(
        start=(since + timedelta(days=1)).isoformat(), auto_adjust=False, actions=True, timeout=30
    )
    if "Stock Splits" not in frame.columns:
        return []
    return [
        Split(ex_date=idx.date(), ratio=float(ratio))
        for idx, ratio in frame["Stock Splits"].items()
        if float(ratio) > 0.0 and idx.date() > since
    ]


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))
