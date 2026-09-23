from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import httpx
from sqlmodel import Session, select

from signalbench.db.models import DocType, DocumentTicker, RawDocument, Ticker
from signalbench.ingest.finnhub import FinnhubClient
from signalbench.ingest.news import (
    NEWS_SOURCE,
    ingest_company_news,
    last_news_date,
    news_windows,
)
from signalbench.ingest.ratelimit import RateLimiter

FAST = RateLimiter(calls=1_000_000, period=1.0)
TODAY = date(2026, 9, 22)
ARTICLES = [
    {"id": 101, "datetime": 1789000000, "headline": "Nvidia wins contract", "summary": "Big deal.", "url": "https://example.com/a", "related": "NVDA,MSFT"},
    {"id": 102, "datetime": 1789003600, "headline": "Nvidia CFO speaks", "summary": "", "url": "", "related": "NVDA"},
    {"id": 103, "datetime": 1789007200, "headline": "", "summary": "No headline", "url": "https://example.com/c", "related": "NVDA"},
]


def _finnhub(articles: list[dict[str, object]]) -> FinnhubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=articles)

    return FinnhubClient("k", httpx.Client(transport=httpx.MockTransport(handler)), limiter=FAST)


def _ticker(session: Session, symbol: str) -> Ticker:
    ticker = Ticker(symbol=symbol, company_name=symbol)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    return ticker


def test_ingest_stores_headline_summary_and_links_queried_symbol(session: Session) -> None:
    nvda = _ticker(session, "NVDA")
    created = ingest_company_news(session, _finnhub(ARTICLES), nvda, TODAY - timedelta(days=5), TODAY)
    assert created == 2
    docs = {doc.external_id: doc for doc in session.exec(select(RawDocument)).all()}
    assert set(docs) == {"101", "102"}
    first = docs["101"]
    assert first.source == NEWS_SOURCE
    assert first.doc_type is DocType.news
    assert first.text == "Nvidia wins contract\n\nBig deal."
    assert first.url == "https://example.com/a"
    assert first.published_at == datetime.fromtimestamp(1789000000, tz=UTC)
    assert first.acceptance_at is None
    assert docs["102"].text == "Nvidia CFO speaks"
    assert docs["102"].url is None
    links = session.exec(select(DocumentTicker)).all()
    assert {link.ticker_id for link in links} == {nvda.id}


def test_rerun_is_noop_and_other_symbol_gets_link_without_duplicate(session: Session) -> None:
    nvda = _ticker(session, "NVDA")
    msft = _ticker(session, "MSFT")
    finnhub = _finnhub(ARTICLES[:1])
    ingest_company_news(session, finnhub, nvda, TODAY, TODAY)
    assert ingest_company_news(session, finnhub, nvda, TODAY, TODAY) == 0
    assert ingest_company_news(session, finnhub, msft, TODAY, TODAY) == 0
    assert len(session.exec(select(RawDocument)).all()) == 1
    assert {link.ticker_id for link in session.exec(select(DocumentTicker)).all()} == {nvda.id, msft.id}


def test_last_news_date(session: Session) -> None:
    nvda = _ticker(session, "NVDA")
    assert last_news_date(session, nvda) is None
    ingest_company_news(session, _finnhub(ARTICLES[:2]), nvda, TODAY, TODAY)
    assert last_news_date(session, nvda) == datetime.fromtimestamp(1789003600, tz=UTC).date()


def test_news_windows_backfill_a_year_in_contiguous_chunks() -> None:
    windows = news_windows(None, TODAY)
    assert windows[0][0] == TODAY - timedelta(days=365)
    assert windows[-1][1] == TODAY
    assert all(end - start <= timedelta(days=29) for start, end in windows)
    assert all(nxt[0] == prev[1] + timedelta(days=1) for prev, nxt in pairwise(windows))


def test_news_windows_overlap_two_days_after_last_article() -> None:
    assert news_windows(date(2026, 9, 20), TODAY) == [(date(2026, 9, 18), TODAY)]


def test_share_class_symbol_is_queried_with_a_dot_and_linked_to_our_ticker(session: Session) -> None:
    brk = _ticker(session, "BRK-B")
    queried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queried.append(request.url.params["symbol"])
        return httpx.Response(200, json=ARTICLES[:1])

    finnhub = FinnhubClient("k", httpx.Client(transport=httpx.MockTransport(handler)), limiter=FAST)
    assert ingest_company_news(session, finnhub, brk, TODAY, TODAY) == 1
    assert queried == ["BRK.B"]
    assert {link.ticker_id for link in session.exec(select(DocumentTicker)).all()} == {brk.id}
