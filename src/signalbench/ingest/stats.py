from datetime import UTC, date, datetime

from sqlalchemy import func
from sqlmodel import Session, col, select

from signalbench.db.models import (
    DocType,
    EarningsEvent,
    Price,
    RawDocument,
    Ticker,
    TickerKind,
)
from signalbench.ingest.news import NEWS_SOURCE
from signalbench.ingest.text import DOCUMENT_SEPARATOR


def collect_stats(session: Session, since: date) -> list[tuple[str, int]]:
    since_at = datetime(since.year, since.month, since.day, tzinfo=UTC)
    cdr_ids = select(Ticker.id).where(Ticker.kind == TickerKind.cdr)
    eight_ks = select(func.count()).select_from(RawDocument).where(
        RawDocument.doc_type == DocType.eight_k,
        col(RawDocument.acceptance_at) >= since_at,
    )
    return [
        ("us_stocks", _count(session, select(func.count()).select_from(Ticker).where(Ticker.kind == TickerKind.us_stock))),
        ("active_us_stocks", _count(session, select(func.count()).select_from(Ticker).where(Ticker.kind == TickerKind.us_stock, Ticker.active))),
        ("cdrs", _count(session, select(func.count()).select_from(Ticker).where(Ticker.kind == TickerKind.cdr))),
        ("cdrs_with_prices", _count(session, select(func.count(func.distinct(Price.ticker_id))).where(col(Price.ticker_id).in_(cdr_ids)))),
        ("price_rows", _count(session, select(func.count()).select_from(Price))),
        ("eight_ks_since", _count(session, eight_ks)),
        ("eight_ks_with_exhibit_text", _count(session, eight_ks.where(col(RawDocument.text).contains(DOCUMENT_SEPARATOR)))),
        ("news_rows", _count(session, select(func.count()).select_from(RawDocument).where(RawDocument.source == NEWS_SOURCE))),
        ("earnings_events", _count(session, select(func.count()).select_from(EarningsEvent))),
    ]


def _count(session: Session, statement: object) -> int:
    return int(session.exec(statement).one())  # type: ignore[call-overload]
