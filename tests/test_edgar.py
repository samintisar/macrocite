import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from sqlmodel import Session, SQLModel, create_engine, select

from signalbench.db.models import DocType, DocumentTicker, RawDocument, Ticker
from signalbench.ingest.edgar import (
    backfill_filing_text,
    exhibit_documents,
    ingest_eight_ks_for_symbol,
    parse_acceptance,
)
from signalbench.ingest.ratelimit import RateLimiter
from signalbench.ingest.text import DOCUMENT_SEPARATOR

FIXTURES = Path(__file__).parent / "fixtures"
UA = "SignalBench/0.1 (test@example.com)"
FAST = RateLimiter(calls=1_000_000, period=1.0)
ACCESSION = "0000320193-24-000001"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _routes(overrides: dict[str, str] | None = None) -> dict[str, str]:
    routes = {
        "company_tickers.json": _fixture("company_tickers.json"),
        "submissions/CIK0000320193.json": _fixture("edgar_submissions.json"),
        "aapl-20240115.htm": _fixture("edgar_8k.html"),
        f"{ACCESSION}-index.htm": _fixture("edgar_index.htm"),
        "a8-kex991.htm": _fixture("edgar_ex991.htm"),
    }
    routes.update(overrides or {})
    return routes


def _client(routes: dict[str, str], requested: list[str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if requested is not None:
            requested.append(url)
        for suffix, body in routes.items():
            if url.endswith(suffix):
                return httpx.Response(200, text=body)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _add_aapl(session: Session) -> Ticker:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    return ticker


def _columns(rows: list[tuple[str, str, str, str, str, str]]) -> dict[str, list[str]]:
    keys = ["accessionNumber", "form", "filingDate", "acceptanceDateTime", "items", "primaryDocument"]
    return {key: [row[index] for row in rows] for index, key in enumerate(keys)}


def test_ingest_stores_one_8k_and_issuer_link(session: Session) -> None:
    ticker = _add_aapl(session)
    created = ingest_eight_ks_for_symbol(session, "AAPL", _client(_routes()), UA, limiter=FAST)
    assert created == 1
    doc = session.exec(select(RawDocument)).one()
    assert doc.source == "sec_edgar"
    assert doc.external_id == ACCESSION
    assert doc.doc_type is DocType.eight_k
    assert "Item 2.02" in doc.raw_text
    links = session.exec(select(DocumentTicker)).all()
    assert [(link.document_id, link.ticker_id) for link in links] == [(doc.id, ticker.id)]


def test_ingest_records_acceptance_items_and_exhibit_text(session: Session) -> None:
    _add_aapl(session)
    ingest_eight_ks_for_symbol(session, "AAPL", _client(_routes()), UA, limiter=FAST)
    doc = session.exec(select(RawDocument)).one()
    accepted = datetime(2024, 1, 15, 21, 30, 5, tzinfo=UTC)
    assert doc.acceptance_at == accepted
    assert doc.published_at == accepted
    assert doc.items == "2.02,9.01"
    assert doc.text is not None
    assert "Item 2.02 Results of Operations" in doc.text
    assert DOCUMENT_SEPARATOR in doc.text
    assert "record first quarter revenue" in doc.text
    assert "risks and uncertainties" not in doc.text
    assert "Pursuant to the requirements" not in doc.text
    assert "HIDDEN XBRL" not in doc.text


def test_missing_filing_index_means_no_exhibits(session: Session) -> None:
    _add_aapl(session)
    routes = _routes()
    del routes[f"{ACCESSION}-index.htm"]
    ingest_eight_ks_for_symbol(session, "AAPL", _client(routes), UA, limiter=FAST)
    doc = session.exec(select(RawDocument)).one()
    assert doc.text is not None
    assert DOCUMENT_SEPARATOR not in doc.text


def test_missing_exhibit_document_is_skipped(session: Session) -> None:
    _add_aapl(session)
    routes = _routes()
    del routes["a8-kex991.htm"]
    created = ingest_eight_ks_for_symbol(session, "AAPL", _client(routes), UA, limiter=FAST)
    assert created == 1
    doc = session.exec(select(RawDocument)).one()
    assert doc.text is not None
    assert "Item 2.02 Results of Operations" in doc.text
    assert DOCUMENT_SEPARATOR not in doc.text


def test_ingest_retries_archive_on_429(session: Session) -> None:
    _add_aapl(session)
    routes = _routes()
    hits = {"archive": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("aapl-20240115.htm"):
            hits["archive"] += 1
            if hits["archive"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
        for suffix, body in routes.items():
            if url.endswith(suffix):
                return httpx.Response(200, text=body)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert ingest_eight_ks_for_symbol(session, "AAPL", client, UA, limiter=FAST) == 1
    assert hits["archive"] == 2


def test_second_ingest_is_noop(session: Session) -> None:
    _add_aapl(session)
    client = _client(_routes())
    ingest_eight_ks_for_symbol(session, "AAPL", client, UA, limiter=FAST)
    assert ingest_eight_ks_for_symbol(session, "AAPL", client, UA, limiter=FAST) == 0
    assert len(session.exec(select(RawDocument)).all()) == 1


def test_ingest_commits_so_filings_survive_session_close() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        _add_aapl(session)
        ingest_eight_ks_for_symbol(session, "AAPL", _client(_routes()), UA, limiter=FAST)
    with Session(engine) as session:
        assert len(session.exec(select(RawDocument)).all()) == 1
        assert len(session.exec(select(DocumentTicker)).all()) == 1


def test_pages_older_filings_and_skips_pages_before_since(session: Session) -> None:
    _add_aapl(session)
    submissions = {
        "cik": "0000320193",
        "filings": {
            "recent": _columns([]),
            "files": [
                {"name": "CIK0000320193-submissions-001.json", "filingTo": "2017-12-31"},
                {"name": "CIK0000320193-submissions-002.json", "filingTo": "2012-12-31"},
            ],
        },
    }
    page = _columns(
        [
            (
                "0000320193-17-000009",
                "8-K",
                "2017-05-02",
                "2017-05-02T20:31:00.000Z",
                "2.02,9.01",
                "a8-k20170502.htm",
            )
        ]
    )
    requested: list[str] = []
    routes = _routes(
        {
            "submissions/CIK0000320193.json": json.dumps(submissions),
            "CIK0000320193-submissions-001.json": json.dumps(page),
            "a8-k20170502.htm": "<html><body><p>Item 2.02 fiscal 2017 results</p></body></html>",
        }
    )
    created = ingest_eight_ks_for_symbol(
        session, "AAPL", _client(routes, requested), UA, since=date(2016, 1, 1), limiter=FAST
    )
    assert created == 1
    assert any(url.endswith("submissions-001.json") for url in requested)
    assert not any(url.endswith("submissions-002.json") for url in requested)


def test_keeps_8k_amendments_and_skips_other_forms_and_old_filings(session: Session) -> None:
    _add_aapl(session)
    recent = _columns(
        [
            (ACCESSION, "8-K", "2024-01-15", "2024-01-15T21:30:05.000Z", "2.02,9.01", "aapl-20240115.htm"),
            ("0000320193-24-000002", "8-K/A", "2024-02-01", "2024-02-01T12:00:00.000Z", "5.02", "aapl-8ka.htm"),
            ("0000320193-24-000003", "10-Q", "2024-02-02", "2024-02-02T12:00:00.000Z", "", "aapl-10q.htm"),
            ("0000320193-15-000004", "8-K", "2015-06-01", "2015-06-01T12:00:00.000Z", "8.01", "aapl-2015.htm"),
        ]
    )
    routes = _routes(
        {
            "submissions/CIK0000320193.json": json.dumps({"cik": "0000320193", "filings": {"recent": recent, "files": []}}),
            "aapl-8ka.htm": "<html><body><p>Item 5.02 amendment</p></body></html>",
        }
    )
    created = ingest_eight_ks_for_symbol(
        session, "AAPL", _client(routes), UA, since=date(2016, 1, 1), limiter=FAST
    )
    assert created == 2
    forms = {doc.external_id: doc.form for doc in session.exec(select(RawDocument)).all()}
    assert forms == {ACCESSION: "8-K", "0000320193-24-000002": "8-K/A"}


def test_parse_acceptance() -> None:
    assert parse_acceptance("2026-07-30T20:30:28.000Z") == datetime(2026, 7, 30, 20, 30, 28, tzinfo=UTC)
    assert parse_acceptance("") is None


def test_exhibit_documents_lists_only_ex99_in_order() -> None:
    assert exhibit_documents(_fixture("edgar_index.htm")) == ["a8-kex991.htm"]


def test_backfill_filing_text_fills_rows_stored_before_spec_01(session: Session) -> None:
    ticker = _add_aapl(session)
    legacy = RawDocument(
        source="sec_edgar",
        external_id=ACCESSION,
        doc_type=DocType.eight_k,
        raw_text=_fixture("edgar_8k.html"),
        published_at=datetime(2024, 1, 15, tzinfo=UTC),
    )
    session.add(legacy)
    session.flush()
    session.add(DocumentTicker(document_id=legacy.id, ticker_id=ticker.id))
    session.commit()

    client = _client(_routes())
    assert backfill_filing_text(session, "AAPL", client, UA, limiter=FAST) == 1
    session.refresh(legacy)
    assert legacy.text is not None
    assert "record first quarter revenue" in legacy.text
    assert legacy.items == "2.02,9.01"
    assert legacy.acceptance_at == datetime(2024, 1, 15, 21, 30, 5, tzinfo=UTC)
    assert legacy.published_at == legacy.acceptance_at
    assert backfill_filing_text(session, "AAPL", client, UA, limiter=FAST) == 0


def _stored_filing(session: Session, ticker: Ticker, text: str | None) -> RawDocument:
    document = RawDocument(
        source="sec_edgar",
        external_id=ACCESSION,
        doc_type=DocType.eight_k,
        raw_text=_fixture("edgar_8k.html"),
        text=text,
        published_at=datetime(2024, 1, 15, tzinfo=UTC),
    )
    session.add(document)
    session.flush()
    session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
    session.commit()
    return document


def test_existing_filing_gets_linked_to_the_queried_ticker(session: Session) -> None:
    other = Ticker(symbol="GOOGL", company_name="Alphabet Inc.", active=True)
    session.add(other)
    ticker = _add_aapl(session)
    document = _stored_filing(session, other, text="already clean")

    created = ingest_eight_ks_for_symbol(
        session, "AAPL", _client(_routes()), UA, cik10="0000320193", limiter=FAST
    )
    assert created == 0
    links = {(link.document_id, link.ticker_id) for link in session.exec(select(DocumentTicker)).all()}
    assert links == {(document.id, other.id), (document.id, ticker.id)}
    assert len(session.exec(select(RawDocument)).all()) == 1
    session.refresh(document)
    assert document.form == "8-K"


def test_existing_filing_without_text_gets_text_items_and_acceptance(session: Session) -> None:
    ticker = _add_aapl(session)
    document = _stored_filing(session, ticker, text=None)

    created = ingest_eight_ks_for_symbol(
        session, "AAPL", _client(_routes()), UA, cik10="0000320193", limiter=FAST
    )
    assert created == 0
    session.refresh(document)
    accepted = datetime(2024, 1, 15, 21, 30, 5, tzinfo=UTC)
    assert document.text is not None
    assert "record first quarter revenue" in document.text
    assert document.items == "2.02,9.01"
    assert document.acceptance_at == accepted
    assert document.published_at == accepted
    assert len(session.exec(select(DocumentTicker)).all()) == 1


def test_second_ingest_only_requests_submissions(session: Session) -> None:
    _add_aapl(session)
    requested: list[str] = []
    client = _client(_routes(), requested)
    ingest_eight_ks_for_symbol(session, "AAPL", client, UA, cik10="0000320193", limiter=FAST)
    requested.clear()
    assert ingest_eight_ks_for_symbol(session, "AAPL", client, UA, cik10="0000320193", limiter=FAST) == 0
    assert requested == ["https://data.sec.gov/submissions/CIK0000320193.json"]
