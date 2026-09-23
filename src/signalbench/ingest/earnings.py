import uuid
from datetime import date, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from signalbench.db.models import (
    DocumentTicker,
    EarningsEvent,
    RawDocument,
    Ticker,
    TickerKind,
)
from signalbench.ingest.finnhub import FinnhubClient

NEW_YORK = ZoneInfo("America/New_York")
SEC_EARNINGS_SOURCE = "sec_2.02"
FINNHUB_EARNINGS_SOURCE = "finnhub"
CLUSTER_DAYS = 3
CALENDAR_DAYS_AHEAD = 30


def has_item_202(items: str | None) -> bool:
    return items is not None and "2.02" in [item.strip() for item in items.split(",")]


def sync_sec_earnings_events(session: Session) -> int:
    """One event per ticker on the New York date each Item 2.02 8-K was accepted."""
    existing = {
        (row.ticker_id, row.event_date)
        for row in session.exec(
            select(EarningsEvent).where(EarningsEvent.source == SEC_EARNINGS_SOURCE)
        ).all()
    }
    documents = session.exec(
        select(RawDocument).where(
            RawDocument.source == "sec_edgar",
            col(RawDocument.acceptance_at).is_not(None),
        )
    ).all()
    created = 0
    for document in documents:
        if document.acceptance_at is None or not has_item_202(document.items):
            continue
        event_date = document.acceptance_at.astimezone(NEW_YORK).date()
        ticker_ids = session.exec(
            select(DocumentTicker.ticker_id).where(DocumentTicker.document_id == document.id)
        ).all()
        for ticker_id in ticker_ids:
            if (ticker_id, event_date) in existing:
                continue
            session.add(
                EarningsEvent(ticker_id=ticker_id, event_date=event_date, source=SEC_EARNINGS_SOURCE)
            )
            existing.add((ticker_id, event_date))
            created += 1
    session.commit()
    return created


def ingest_finnhub_calendar(session: Session, finnhub: FinnhubClient, today: date) -> int:
    """Replace Finnhub's upcoming events for the next 30 days with the current calendar."""
    end = today + timedelta(days=CALENDAR_DAYS_AHEAD)
    payload = cast(
        dict[str, Any],
        finnhub.get("/calendar/earnings", {"from": today.isoformat(), "to": end.isoformat()}),
    )
    for stale in session.exec(
        select(EarningsEvent).where(
            EarningsEvent.source == FINNHUB_EARNINGS_SOURCE,
            col(EarningsEvent.event_date) >= today,
        )
    ).all():
        session.delete(stale)
    session.flush()

    ticker_ids = {
        ticker.symbol: ticker.id
        for ticker in session.exec(select(Ticker).where(Ticker.kind == TickerKind.us_stock)).all()
    }
    seen: set[tuple[uuid.UUID, date]] = set()
    created = 0
    for row in payload.get("earningsCalendar", []):
        ticker_id = ticker_ids.get(str(row.get("symbol", "")))
        if ticker_id is None or not row.get("date"):
            continue
        event_date = date.fromisoformat(str(row["date"]))
        if (ticker_id, event_date) in seen:
            continue
        seen.add((ticker_id, event_date))
        session.add(
            EarningsEvent(ticker_id=ticker_id, event_date=event_date, source=FINNHUB_EARNINGS_SOURCE)
        )
        created += 1
    session.commit()
    return created


def cluster_earliest(dates: list[date]) -> list[date]:
    """Collapse dates within CLUSTER_DAYS of a cluster's first date to that first date."""
    clusters: list[date] = []
    for day in sorted(set(dates)):
        if clusters and (day - clusters[-1]).days <= CLUSTER_DAYS:
            continue
        clusters.append(day)
    return clusters


def earnings_dates(session: Session, ticker_id: uuid.UUID) -> list[date]:
    dates = session.exec(
        select(col(EarningsEvent.event_date)).where(EarningsEvent.ticker_id == ticker_id)
    ).all()
    return cluster_earliest(list(dates))
