import uuid
from dataclasses import dataclass
from datetime import date

from sqlmodel import Session, col, select

from signalbench.db.models import Price


@dataclass(frozen=True)
class AdjustedBar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


def adjust(row: Price) -> AdjustedBar:
    """Scale open/high/low by adj_close/close so the whole bar is split- and dividend-adjusted."""
    factor = float(row.adj_close) / float(row.close)
    return AdjustedBar(
        date=row.date,
        open=float(row.open) * factor,
        high=float(row.high) * factor,
        low=float(row.low) * factor,
        close=float(row.adj_close),
        volume=row.volume,
    )


def adjusted_bars(
    session: Session,
    ticker_id: uuid.UUID,
    start: date | None = None,
    end: date | None = None,
) -> list[AdjustedBar]:
    query = select(Price).where(Price.ticker_id == ticker_id)
    if start is not None:
        query = query.where(col(Price.date) >= start)
    if end is not None:
        query = query.where(col(Price.date) <= end)
    return [adjust(row) for row in session.exec(query.order_by(col(Price.date))).all()]
