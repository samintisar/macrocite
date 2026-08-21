from pathlib import Path

import httpx
from sqlmodel import Session, SQLModel, create_engine, select

from signalbench.db.models import DocumentTicker, RawDocument, Ticker
from signalbench.ingest.edgar import ingest_eight_ks_for_symbol

FIXTURES = Path(__file__).parent / "fixtures"


def test_ingest_stores_one_8k_and_issuer_link(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, text=(FIXTURES / "company_tickers.json").read_text())
        if "submissions/CIK0000320193.json" in url:
            return httpx.Response(200, text=(FIXTURES / "edgar_submissions.json").read_text())
        if url.endswith("aapl-20240115.htm"):
            return httpx.Response(200, text=(FIXTURES / "edgar_8k.html").read_text())
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    created = ingest_eight_ks_for_symbol(
        session,
        symbol="AAPL",
        client=client,
        user_agent="SignalBench/0.1 (test@example.com)",
    )
    assert created == 1
    doc = session.exec(select(RawDocument)).one()
    assert doc.source == "sec_edgar"
    assert doc.external_id == "0000320193-24-000001"
    assert doc.doc_type.value == "eight_k"
    assert doc.published_at.tzinfo is not None
    assert "Item 2.02" in doc.raw_text or "8-K" in doc.raw_text
    links = session.exec(select(DocumentTicker)).all()
    assert len(links) == 1
    assert links[0].ticker_id == ticker.id


def test_ingest_retries_archive_on_429(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    hits = {"archive": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, text=(FIXTURES / "company_tickers.json").read_text())
        if "submissions/CIK0000320193.json" in url:
            return httpx.Response(200, text=(FIXTURES / "edgar_submissions.json").read_text())
        if url.endswith("aapl-20240115.htm"):
            hits["archive"] += 1
            if hits["archive"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, text=(FIXTURES / "edgar_8k.html").read_text())
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    created = ingest_eight_ks_for_symbol(
        session,
        symbol="AAPL",
        client=client,
        user_agent="SignalBench/0.1 (test@example.com)",
    )
    assert created == 1
    assert hits["archive"] == 2


def test_second_ingest_is_noop(session: Session) -> None:
    session.add(Ticker(symbol="AAPL", company_name="Apple Inc.", active=True))
    session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, text=(FIXTURES / "company_tickers.json").read_text())
        if "submissions" in url:
            return httpx.Response(200, text=(FIXTURES / "edgar_submissions.json").read_text())
        return httpx.Response(200, text=(FIXTURES / "edgar_8k.html").read_text())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ingest_eight_ks_for_symbol(session, "AAPL", client, "SignalBench/0.1 (test@example.com)")
    ingest_eight_ks_for_symbol(session, "AAPL", client, "SignalBench/0.1 (test@example.com)")
    docs = session.exec(select(RawDocument)).all()
    assert len(docs) == 1


def test_ingest_commits_so_filings_survive_session_close() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, text=(FIXTURES / "company_tickers.json").read_text())
        if "submissions" in url:
            return httpx.Response(200, text=(FIXTURES / "edgar_submissions.json").read_text())
        return httpx.Response(200, text=(FIXTURES / "edgar_8k.html").read_text())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with Session(engine) as session:
        session.add(Ticker(symbol="AAPL", company_name="Apple Inc.", active=True))
        session.commit()
        ingest_eight_ks_for_symbol(
            session, "AAPL", client, "SignalBench/0.1 (test@example.com)"
        )

    with Session(engine) as session:
        docs = session.exec(select(RawDocument)).all()
        links = session.exec(select(DocumentTicker)).all()
        assert len(docs) == 1
        assert len(links) == 1
