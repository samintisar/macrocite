import threading
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlmodel import Session, select

from signalbench.db.models import (
    DocType,
    DocumentTicker,
    JevReading,
    RawDocument,
    Ticker,
    TickerKind,
)
from signalbench.jev import backfill
from signalbench.jev.backfill import job_state, pending_work, run_backfill
from signalbench.jev.client import JevFatalError, JevRejectedError
from signalbench.jev.fake import FakeJevClient, fake_result

UNIVERSE = ["AAA", "BBB"]
NEWS_DAY = datetime(2026, 9, 21, 13, 0, tzinfo=UTC)  # 09:00 in New York


def _quiet(_line: str) -> None:
    pass


def _ticker(session: Session, symbol: str) -> Ticker:
    ticker = Ticker(symbol=symbol, company_name=f"{symbol.title()} Corp", kind=TickerKind.us_stock)
    session.add(ticker)
    session.commit()
    return ticker


def _document(
    session: Session,
    tickers: list[Ticker],
    external_id: str,
    doc_type: DocType,
    published_at: datetime,
    text: str | None,
    *,
    acceptance_at: datetime | None = None,
    items: str | None = None,
) -> RawDocument:
    document = RawDocument(
        source="sec_edgar" if doc_type == DocType.eight_k else "finnhub",
        external_id=external_id,
        doc_type=doc_type,
        raw_text=text or "<html></html>",
        text=text,
        published_at=published_at,
        acceptance_at=acceptance_at,
        items=items,
    )
    session.add(document)
    session.flush()
    for ticker in tickers:
        session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
    session.commit()
    return document


@pytest.fixture
def seeded(session: Session) -> Session:
    aaa, bbb, zzz = (_ticker(session, symbol) for symbol in ("AAA", "BBB", "ZZZ"))
    _document(
        session, [aaa], "8k-results", DocType.eight_k, datetime(2024, 1, 2, tzinfo=UTC),
        "Aaa Corp reported record revenue.",
        acceptance_at=datetime(2024, 1, 2, 21, 5, tzinfo=UTC), items="2.02,9.01",
    )
    _document(session, [aaa], "8k-no-text", DocType.eight_k, datetime(2024, 2, 1, tzinfo=UTC), None)
    _document(
        session, [bbb], "8k-huge", DocType.eight_k, datetime(2024, 3, 1, tzinfo=UTC), "x" * 100_000
    )
    for minute in range(22):  # 22 AAA items on one New York day, plus news-both below
        _document(
            session, [aaa], f"news-{minute:02d}", DocType.news,
            NEWS_DAY + timedelta(minutes=minute), f"Headline {minute:02d}",
        )
    _document(
        session, [aaa, bbb], "news-both", DocType.news, NEWS_DAY + timedelta(hours=1), "Both names"
    )
    _document(session, [zzz], "news-other", DocType.news, NEWS_DAY, "Not in the universe")
    return session


def test_pending_work_counts_and_the_news_cap(seeded: Session) -> None:
    work = pending_work(seeded, UNIVERSE, "all", None)
    assert (work.no_text, work.too_long, work.already_read) == (1, 1, 0)
    assert (work.capped, work.capped_days) == (3, 1)  # AAA's 23 items that day keep the newest 20
    sources = [(job.symbol, job.source) for job in work.jobs]
    assert sources.count(("AAA", "filings")) == 1
    assert sources.count(("AAA", "news")) == 20
    assert sources.count(("BBB", "news")) == 1  # the shared item is read once per ticker
    assert "ZZZ" not in {job.symbol for job in work.jobs}
    kept = sorted(
        job.timestamp for job in work.jobs if job.symbol == "AAA" and job.source == "news"
    )
    assert kept[0] == NEWS_DAY + timedelta(minutes=3)  # minutes 0, 1, 2 were capped
    assert kept[-1] == NEWS_DAY + timedelta(hours=1)
    filing = work.jobs[0]  # jobs run oldest first
    assert filing.timestamp == datetime(2024, 1, 2, 21, 5, tzinfo=UTC)  # the acceptance time
    assert (filing.company_name, filing.items) == ("Aaa Corp", "2.02,9.01")


def test_pending_work_by_source_and_since(seeded: Session) -> None:
    filings = pending_work(seeded, UNIVERSE, "filings", None)
    assert [job.source for job in filings.jobs] == ["filings"]
    news = pending_work(seeded, UNIVERSE, "news", date(2026, 9, 21))
    assert {job.source for job in news.jobs} == {"news"} and len(news.jobs) == 21
    assert pending_work(seeded, UNIVERSE, "news", date(2026, 9, 22)).jobs == []


def test_backfill_stores_readings_and_a_second_run_makes_no_calls(seeded: Session) -> None:
    client = FakeJevClient()
    work = pending_work(seeded, UNIVERSE, "filings", None)
    summary = run_backfill(seeded, work.jobs, client, max_cost_usd=10.0, echo=_quiet)
    assert (summary.read, summary.stopped, summary.skipped) == (1, None, [])
    assert client.calls == [
        (
            "Company: Aaa Corp (AAA)\nSource: SEC 8-K, items 2.02, 9.01\n\n"
            "Aaa Corp reported record revenue."
        )
    ]
    stored = seeded.exec(select(JevReading)).one()
    assert (stored.model_requested, stored.question_set) == ("typesafe/jev-1.13", "q1")
    assert stored.model_resolved == "typesafe/jev-1.13-20260917"
    assert (stored.p_positive, stored.p_routine, stored.cost_usd) == (0.7, 0.1, 0.00002)
    assert summary.builds == {"typesafe/jev-1.13-20260917": 1}
    again = pending_work(seeded, UNIVERSE, "filings", None)
    assert (again.jobs, again.already_read) == ([], 1)
    second = FakeJevClient()
    assert run_backfill(seeded, again.jobs, second, max_cost_usd=10.0, echo=_quiet).read == 0
    assert second.calls == []


def test_the_budget_guard_stops_cleanly_and_the_next_run_resumes(seeded: Session) -> None:
    jobs = pending_work(seeded, UNIVERSE, "news", None).jobs[:5]
    client = FakeJevClient(lambda _state: fake_result(cost_usd=0.4))
    summary = run_backfill(seeded, jobs, client, max_cost_usd=1.0, concurrency=1, echo=_quiet)
    assert summary.read == 3  # 0.4, 0.8, then 1.2 reaches the limit
    assert summary.cost_usd == pytest.approx(1.2)
    assert summary.not_started == 2
    assert summary.budget_reached
    assert summary.stopped == "budget reached: $1.2000 of $1.00"
    assert len(pending_work(seeded, UNIVERSE, "news", None).jobs) == 21 - 3


def test_the_budget_guard_stops_cleanly_under_a_thread_pool(seeded: Session) -> None:
    jobs = pending_work(seeded, UNIVERSE, "news", None).jobs[:10]
    client = FakeJevClient(lambda _state: fake_result(cost_usd=0.4))
    summary = run_backfill(seeded, jobs, client, max_cost_usd=1.0, concurrency=4, echo=_quiet)
    assert summary.budget_reached
    assert summary.stopped is not None and summary.stopped.startswith("budget reached")
    assert len(client.calls) == summary.read  # every call made was stored, none lost
    assert len(seeded.exec(select(JevReading)).all()) == summary.read
    assert summary.read < len(jobs)  # the budget stopped the run before every pair was read


def test_a_fatal_error_stops_the_run(seeded: Session) -> None:
    jobs = pending_work(seeded, UNIVERSE, "news", None).jobs[:4]
    answers: list[Any] = [fake_result(), JevFatalError("HTTP 402: insufficient credits")]
    client = FakeJevClient(lambda _state: answers.pop(0) if answers else fake_result())
    summary = run_backfill(seeded, jobs, client, max_cost_usd=10.0, concurrency=1, echo=_quiet)
    assert summary.read == 1
    assert summary.stopped == "HTTP 402: insufficient credits"
    assert not summary.budget_reached
    assert summary.not_started == 2
    assert len(client.calls) == 2


def test_a_rejected_document_is_skipped_with_its_reason(seeded: Session) -> None:
    jobs = pending_work(seeded, UNIVERSE, "news", None).jobs[:3]
    too_large = job_state(seeded, jobs[1])
    client = FakeJevClient(
        lambda state: JevRejectedError(413, "too large") if state == too_large else fake_result()
    )
    lines: list[str] = []
    summary = run_backfill(
        seeded, jobs, client, max_cost_usd=10.0, concurrency=1, echo=lines.append
    )
    assert summary.read == 2 and summary.stopped is None
    ((skipped_job, reason),) = summary.skipped
    assert skipped_job == jobs[1] and reason == "HTTP 413: too large"
    assert any(line.startswith("skip AAA ") and "HTTP 413" in line for line in lines)


def test_repeated_failures_stop_the_run(seeded: Session) -> None:
    jobs = pending_work(seeded, UNIVERSE, "news", None).jobs
    client = FakeJevClient(lambda _state: JevRejectedError(400, "bad request"))
    summary = run_backfill(seeded, jobs, client, max_cost_usd=10.0, concurrency=1, echo=_quiet)
    assert summary.read == 0 and len(summary.skipped) == 20
    assert summary.stopped is not None and "20 documents in a row failed" in summary.stopped
    assert summary.not_started == 1


def test_workers_call_jev_but_only_the_main_thread_writes(
    seeded: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    writers: list[int] = []
    save = backfill.save_reading

    def spy(*args: Any, **kwargs: Any) -> None:
        writers.append(threading.get_ident())
        save(*args, **kwargs)

    monkeypatch.setattr(backfill, "save_reading", spy)
    jobs = pending_work(seeded, UNIVERSE, "news", None).jobs
    client = FakeJevClient()
    summary = run_backfill(seeded, jobs, client, max_cost_usd=10.0, concurrency=4, echo=_quiet)
    assert summary.read == 21
    assert len(seeded.exec(select(JevReading)).all()) == 21
    assert set(writers) == {threading.get_ident()}
    assert threading.get_ident() not in set(client.threads)
