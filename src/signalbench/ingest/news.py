import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from sqlalchemy import func
from sqlmodel import Session, col, select

from signalbench.db.models import DocType, DocumentTicker, RawDocument, Ticker
from signalbench.ingest.finnhub import FinnhubClient

NEWS_SOURCE = "finnhub"
NEWS_BACKFILL_DAYS = 365
NEWS_OVERLAP_DAYS = 2
NEWS_CHUNK_DAYS = 30


def news_windows(last_published: date | None, today: date) -> list[tuple[date, date]]:
    """Date ranges to request: a year of backfill, or from 2 days before the last article."""
    if last_published is None:
        start = today - timedelta(days=NEWS_BACKFILL_DAYS)
    else:
        start = last_published - timedelta(days=NEWS_OVERLAP_DAYS)
    windows: list[tuple[date, date]] = []
    while start <= today:
        end = min(today, start + timedelta(days=NEWS_CHUNK_DAYS - 1))
        windows.append((start, end))
        start = end + timedelta(days=1)
    return windows


def last_news_date(session: Session, ticker: Ticker) -> date | None:
    document_ids = list(
        session.exec(
            select(DocumentTicker.document_id).where(DocumentTicker.ticker_id == ticker.id)
        ).all()
    )
    if not document_ids:
        return None
    latest: datetime | None = session.exec(
        select(func.max(RawDocument.published_at)).where(
            RawDocument.source == NEWS_SOURCE,
            col(RawDocument.id).in_(document_ids),
        )
    ).one()
    return None if latest is None else latest.date()


def ingest_company_news(
    session: Session,
    finnhub: FinnhubClient,
    ticker: Ticker,
    start: date,
    end: date,
) -> int:
    articles = cast(
        list[dict[str, Any]],
        finnhub.get(
            "/company-news",
            {"symbol": ticker.symbol, "from": start.isoformat(), "to": end.isoformat()},
        ),
    )
    external_ids = [str(article["id"]) for article in articles]
    known: dict[str, uuid.UUID] = {}
    if external_ids:
        known = {
            document.external_id: document.id
            for document in session.exec(
                select(RawDocument).where(
                    RawDocument.source == NEWS_SOURCE,
                    col(RawDocument.external_id).in_(external_ids),
                )
            ).all()
        }
    linked: set[uuid.UUID] = set()
    if known:
        linked = set(
            session.exec(
                select(DocumentTicker.document_id).where(
                    DocumentTicker.ticker_id == ticker.id,
                    col(DocumentTicker.document_id).in_(list(known.values())),
                )
            ).all()
        )

    created = 0
    for article in articles:
        external_id = str(article["id"])
        if external_id in known:
            document_id = known[external_id]
            if document_id not in linked:
                session.add(DocumentTicker(document_id=document_id, ticker_id=ticker.id))
                linked.add(document_id)
            continue
        headline = str(article.get("headline") or "").strip()
        if not headline:
            continue
        summary = str(article.get("summary") or "").strip()
        text = f"{headline}\n\n{summary}" if summary else headline
        document = RawDocument(
            source=NEWS_SOURCE,
            external_id=external_id,
            doc_type=DocType.news,
            url=str(article["url"]) if article.get("url") else None,
            title=headline,
            raw_text=text,
            text=text,
            published_at=datetime.fromtimestamp(int(article["datetime"]), tz=UTC),
        )
        session.add(document)
        session.flush()
        session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
        known[external_id] = document.id
        linked.add(document.id)
        created += 1
    session.commit()
    return created
