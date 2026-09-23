import uuid
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, col, select

from signalbench.db.models import (
    DocumentTicker,
    EarningsEvent,
    RawDocument,
    Ticker,
)
from signalbench.ingest.finnhub import FinnhubClient, finnhub_symbol

NEW_YORK = ZoneInfo("America/New_York")
SEC_EARNINGS_SOURCE = "sec_2.02"
FINNHUB_EARNINGS_SOURCE = "finnhub"
CLUSTER_DAYS = 3
CALENDAR_DAYS_AHEAD = 30


def has_item_202(items: str | None) -> bool:
    return items is not None and "2.02" in [item.strip() for item in items.split(",")]


def sync_sec_earnings_events(session: Session) -> int:
    """One event per ticker on the New York date each Item 2.02 8-K was accepted."""
    existing: set[tuple[uuid.UUID, date]] = set(
        session.exec(
            select(EarningsEvent.ticker_id, col(EarningsEvent.event_date)).where(
                EarningsEvent.source == SEC_EARNINGS_SOURCE
            )
        ).all()
    )
    rows = session.exec(
        select(DocumentTicker.ticker_id, col(RawDocument.acceptance_at), col(RawDocument.items))
        .join(RawDocument, col(RawDocument.id) == DocumentTicker.document_id)
        .where(
            RawDocument.source == "sec_edgar",
            col(RawDocument.acceptance_at).is_not(None),
        )
    ).all()
    created = 0
    for ticker_id, acceptance_at, items in rows:
        if acceptance_at is None or not has_item_202(items):
            continue
        event_date = acceptance_at.astimezone(NEW_YORK).date()
        if (ticker_id, event_date) in existing:
            continue
        session.add(
            EarningsEvent(ticker_id=ticker_id, event_date=event_date, source=SEC_EARNINGS_SOURCE)
        )
        existing.add((ticker_id, event_date))
        created += 1
    session.commit()
    return created


def ingest_finnhub_calendar_for_ticker(
    session: Session, finnhub: FinnhubClient, ticker: Ticker, today: date
) -> int:
    """Replace the ticker's upcoming Finnhub events with its next 30 days of calendar.

    The unfiltered calendar is truncated, so each ticker is queried on its own. Finnhub may
    answer with another share class (GOOG gives GOOGL rows), so every row is attributed to
    the queried ticker. Stored rows change only after the query succeeds.
    """
    end = today + timedelta(days=CALENDAR_DAYS_AHEAD)
    payload = finnhub.get(
        "/calendar/earnings",
        {
            "from": today.isoformat(),
            "to": end.isoformat(),
            "symbol": finnhub_symbol(ticker.symbol),
        },
    )
    rows = payload.get("earningsCalendar") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise TypeError(f"Finnhub response has no earningsCalendar list: {payload!r}"[:300])
    event_dates = {date.fromisoformat(str(row["date"])) for row in rows if row.get("date")}
    for stale in session.exec(
        select(EarningsEvent).where(
            EarningsEvent.ticker_id == ticker.id,
            EarningsEvent.source == FINNHUB_EARNINGS_SOURCE,
            col(EarningsEvent.event_date) >= today,
        )
    ).all():
        session.delete(stale)
    session.flush()
    for event_date in sorted(event_dates):
        session.add(
            EarningsEvent(ticker_id=ticker.id, event_date=event_date, source=FINNHUB_EARNINGS_SOURCE)
        )
    session.commit()
    return len(event_dates)


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
