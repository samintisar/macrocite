from datetime import UTC, date, datetime

import httpx
from sqlmodel import Session, select

from signalbench.db.models import (
    DocType,
    DocumentTicker,
    EarningsEvent,
    RawDocument,
    Ticker,
)
from signalbench.ingest.earnings import (
    FINNHUB_EARNINGS_SOURCE,
    SEC_EARNINGS_SOURCE,
    cluster_earliest,
    earnings_dates,
    ingest_finnhub_calendar,
    sync_sec_earnings_events,
)
from signalbench.ingest.finnhub import FinnhubClient
from signalbench.ingest.ratelimit import RateLimiter

FAST = RateLimiter(calls=1_000_000, period=1.0)


def _ticker(session: Session, symbol: str) -> Ticker:
    ticker = Ticker(symbol=symbol, company_name=symbol)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    return ticker


def _filing(session: Session, ticker: Ticker, accession: str, accepted: datetime, items: str) -> None:
    document = RawDocument(
        source="sec_edgar",
        external_id=accession,
        doc_type=DocType.eight_k,
        raw_text="x",
        published_at=accepted,
        acceptance_at=accepted,
        items=items,
    )
    session.add(document)
    session.flush()
    session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
    session.commit()


def _events(session: Session, source: str) -> list[tuple[str, date]]:
    tickers = {ticker.id: ticker.symbol for ticker in session.exec(select(Ticker)).all()}
    rows = session.exec(select(EarningsEvent).where(EarningsEvent.source == source)).all()
    return sorted((tickers[row.ticker_id], row.event_date) for row in rows)


def test_sec_events_use_new_york_date_of_item_202_filings(session: Session) -> None:
    aapl = _ticker(session, "AAPL")
    _filing(session, aapl, "a-1", datetime(2024, 1, 15, 21, 30, tzinfo=UTC), "2.02,9.01")
    _filing(session, aapl, "a-2", datetime(2024, 4, 1, 2, 0, tzinfo=UTC), "2.02")  # 22:00 ET on Mar 31
    _filing(session, aapl, "a-3", datetime(2024, 5, 1, 12, 0, tzinfo=UTC), "5.02")
    assert sync_sec_earnings_events(session) == 2
    assert sync_sec_earnings_events(session) == 0
    assert _events(session, SEC_EARNINGS_SOURCE) == [
        ("AAPL", date(2024, 1, 15)),
        ("AAPL", date(2024, 3, 31)),
    ]


def _calendar(responses: dict[str, list[httpx.Response]], queried: list[str] | None = None) -> FinnhubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        symbol = request.url.params["symbol"]
        if queried is not None:
            queried.append(symbol)
        return responses[symbol].pop(0)

    return FinnhubClient("k", httpx.Client(transport=httpx.MockTransport(handler)), limiter=FAST)


def _rows(*rows: tuple[str, str]) -> httpx.Response:
    return httpx.Response(200, json={"earningsCalendar": [{"symbol": s, "date": d} for s, d in rows]})


TODAY = date(2026, 9, 22)


def test_finnhub_calendar_replaces_its_future_window(session: Session) -> None:
    nvda = _ticker(session, "NVDA")
    responses = {
        "NVDA": [
            _rows(("NVDA", "2026-10-01"), ("NVDA", "2026-10-01")),
            _rows(("NVDA", "2026-10-03")),
        ]
    }
    finnhub = _calendar(responses)
    assert ingest_finnhub_calendar(session, finnhub, [nvda], TODAY) == 1
    assert ingest_finnhub_calendar(session, finnhub, [nvda], TODAY) == 1
    assert _events(session, FINNHUB_EARNINGS_SOURCE) == [("NVDA", date(2026, 10, 3))]


def test_finnhub_calendar_queries_each_ticker_and_passes_the_window(session: Session) -> None:
    mu = _ticker(session, "MU")
    cost = _ticker(session, "COST")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        symbol = request.url.params["symbol"]
        day = {"MU": "2026-09-30", "COST": "2026-09-24"}[symbol]
        return _rows((symbol, day))

    finnhub = FinnhubClient("k", httpx.Client(transport=httpx.MockTransport(handler)), limiter=FAST)
    assert ingest_finnhub_calendar(session, finnhub, [mu, cost], TODAY) == 2
    assert [(r.url.params["symbol"], r.url.params["from"], r.url.params["to"]) for r in seen] == [
        ("MU", "2026-09-22", "2026-10-22"),
        ("COST", "2026-09-22", "2026-10-22"),
    ]
    assert _events(session, FINNHUB_EARNINGS_SOURCE) == [
        ("COST", date(2026, 9, 24)),
        ("MU", date(2026, 9, 30)),
    ]


def test_finnhub_calendar_attributes_rows_to_the_queried_ticker(session: Session) -> None:
    goog = _ticker(session, "GOOG")
    finnhub = _calendar({"GOOG": [_rows(("GOOGL", "2026-10-27"))]})
    assert ingest_finnhub_calendar(session, finnhub, [goog], TODAY) == 1
    assert _events(session, FINNHUB_EARNINGS_SOURCE) == [("GOOG", date(2026, 10, 27))]


def test_finnhub_calendar_queries_share_classes_with_a_dot(session: Session) -> None:
    brk = _ticker(session, "BRK-B")
    queried: list[str] = []
    finnhub = _calendar({"BRK.B": [_rows(("BRK.A", "2026-11-01"))]}, queried)
    assert ingest_finnhub_calendar(session, finnhub, [brk], TODAY) == 1
    assert queried == ["BRK.B"]
    assert _events(session, FINNHUB_EARNINGS_SOURCE) == [("BRK-B", date(2026, 11, 1))]


def test_finnhub_calendar_error_keeps_that_tickers_events(session: Session) -> None:
    nvda = _ticker(session, "NVDA")
    mu = _ticker(session, "MU")
    finnhub = _calendar(
        {
            "NVDA": [_rows(("NVDA", "2026-10-01")), _rows(("NVDA", "2026-10-03"))],
            "MU": [_rows(("MU", "2026-09-30")), httpx.Response(200, json={"error": "limit"})],
        }
    )
    ingest_finnhub_calendar(session, finnhub, [nvda, mu], TODAY)
    assert ingest_finnhub_calendar(session, finnhub, [nvda, mu], TODAY) == 1
    assert _events(session, FINNHUB_EARNINGS_SOURCE) == [
        ("MU", date(2026, 9, 30)),
        ("NVDA", date(2026, 10, 3)),
    ]


def test_cluster_earliest() -> None:
    days = [date(2026, 1, 5), date(2026, 1, 3), date(2026, 1, 1), date(2026, 4, 30), date(2026, 5, 1)]
    assert cluster_earliest(days) == [date(2026, 1, 1), date(2026, 1, 5), date(2026, 4, 30)]


def test_earnings_dates_merges_sources(session: Session) -> None:
    nvda = _ticker(session, "NVDA")
    session.add(EarningsEvent(ticker_id=nvda.id, event_date=date(2026, 8, 27), source=SEC_EARNINGS_SOURCE))
    session.add(EarningsEvent(ticker_id=nvda.id, event_date=date(2026, 8, 26), source=FINNHUB_EARNINGS_SOURCE))
    session.commit()
    assert earnings_dates(session, nvda.id) == [date(2026, 8, 26)]
