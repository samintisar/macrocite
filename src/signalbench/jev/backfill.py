"""Read every stored 8-K and news item once per ticker (spec 03, backfill).

Idempotent and resumable: a (document, ticker) pair that already has a reading for this model
and question set is never sent again, and each reading is committed as soon as it arrives.
Jev calls run on a small thread pool; every database read and write stays on the calling thread.
"""

import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlmodel import Session, col, select

from signalbench.db.models import (
    DocType,
    DocumentTicker,
    JevReading,
    RawDocument,
    Ticker,
)
from signalbench.jev.client import JevClient, JevError, JevFatalError, JevResult
from signalbench.jev.questions import (
    MAX_STATE_CHARS,
    MODEL,
    QUESTION_SET,
    Source,
    build_state,
)

NEW_YORK = ZoneInfo("America/New_York")
NEWS_DAILY_CAP = 20  # per symbol per New York calendar day, most recent first
MAX_CONSECUTIVE_FAILURES = 20
PROGRESS_EVERY = 100
BackfillSource = Literal["filings", "news", "all"]
DOC_TYPES: dict[Source, DocType] = {"filings": DocType.eight_k, "news": DocType.news}


@dataclass(frozen=True)
class ReadJob:
    """One (document, ticker) pair to read."""

    document_id: uuid.UUID
    ticker_id: uuid.UUID
    symbol: str
    company_name: str
    source: Source
    items: str | None
    timestamp: datetime  # 8-K: acceptance time (else published); news: published


@dataclass(frozen=True)
class PendingWork:
    jobs: list[ReadJob]
    already_read: int
    no_text: int
    too_long: int
    capped: int  # news items over the daily cap (read or not)
    capped_days: int  # symbol-days that hit the cap


@dataclass
class BackfillSummary:
    read: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    skipped: list[tuple[ReadJob, str]] = field(default_factory=list)
    builds: Counter[str] = field(default_factory=Counter)
    stopped: str | None = None
    budget_reached: bool = False  # a clean stop: run again to continue
    not_started: int = 0


@dataclass(frozen=True)
class _Row:
    job: ReadJob
    text_chars: int
    read: bool


def _sources(source: BackfillSource) -> list[Source]:
    return ["filings", "news"] if source == "all" else [source]


def _rows(
    session: Session,
    symbols: Collection[str],
    source: Source,
    since: date | None,
    model: str,
    question_set: str,
) -> list[_Row]:
    tickers = {
        ticker.id: ticker
        for ticker in session.exec(select(Ticker).where(col(Ticker.symbol).in_(list(symbols))))
    }
    documents = select(
        RawDocument.id,
        RawDocument.items,
        func.coalesce(RawDocument.acceptance_at, RawDocument.published_at),  # 8-K: acceptance
        func.coalesce(func.length(RawDocument.text), 0),
    ).where(RawDocument.doc_type == DOC_TYPES[source])
    if since is not None:
        start = datetime.combine(since, time(0, 0), tzinfo=NEW_YORK)
        documents = documents.where(col(RawDocument.published_at) >= start)
    found = {
        doc_id: (items, stamp, int(chars))
        for doc_id, items, stamp, chars in session.exec(documents)
    }
    links = session.exec(
        select(DocumentTicker.document_id, DocumentTicker.ticker_id).where(
            col(DocumentTicker.ticker_id).in_(list(tickers))
        )
    ).all()
    read = set(
        session.exec(
            select(JevReading.document_id, JevReading.ticker_id).where(
                JevReading.model_requested == model, JevReading.question_set == question_set
            )
        ).all()
    )
    rows: list[_Row] = []
    for doc_id, ticker_id in links:
        if doc_id not in found:
            continue
        items, stamp, chars = found[doc_id]
        ticker = tickers[ticker_id]
        job = ReadJob(doc_id, ticker_id, ticker.symbol, ticker.company_name, source, items, stamp)
        rows.append(_Row(job, chars, (doc_id, ticker_id) in read))
    return rows


def _cap_news(rows: list[_Row]) -> tuple[list[_Row], int, int]:
    """Keep the newest NEWS_DAILY_CAP items per symbol per New York day."""
    days: dict[tuple[str, date], list[_Row]] = defaultdict(list)
    for row in rows:
        days[(row.job.symbol, row.job.timestamp.astimezone(NEW_YORK).date())].append(row)
    kept: list[_Row] = []
    capped = capped_days = 0
    for day_rows in days.values():
        day_rows.sort(key=lambda row: (row.job.timestamp, str(row.job.document_id)), reverse=True)
        kept.extend(day_rows[:NEWS_DAILY_CAP])
        if len(day_rows) > NEWS_DAILY_CAP:
            capped += len(day_rows) - NEWS_DAILY_CAP
            capped_days += 1
    return kept, capped, capped_days


def pending_work(
    session: Session,
    symbols: Collection[str],
    source: BackfillSource,
    since: date | None,
    *,
    model: str = MODEL,
    question_set: str = QUESTION_SET,
) -> PendingWork:
    """Every unread (document, ticker) pair for the universe `symbols`, oldest first."""
    jobs: list[ReadJob] = []
    already_read = no_text = too_long = capped = capped_days = 0
    for kind in _sources(source):
        rows = _rows(session, symbols, kind, since, model, question_set)
        if kind == "news":
            rows, capped, capped_days = _cap_news(rows)
        for row in rows:
            job = row.job
            header = build_state(job.company_name, job.symbol, job.source, job.items, "")
            if row.read:
                already_read += 1
            elif row.text_chars == 0:
                no_text += 1
            elif len(header) + row.text_chars > MAX_STATE_CHARS:
                too_long += 1
            else:
                jobs.append(job)
    jobs.sort(key=lambda job: (job.timestamp, str(job.document_id), job.symbol))
    return PendingWork(jobs, already_read, no_text, too_long, capped, capped_days)


def job_state(session: Session, job: ReadJob) -> str:
    """The state sent to Jev for one job (loads only the document's text column)."""
    text = session.exec(select(RawDocument.text).where(RawDocument.id == job.document_id)).one()
    return build_state(job.company_name, job.symbol, job.source, job.items, text or "")


def save_reading(
    session: Session, job: ReadJob, result: JevResult, model: str, question_set: str
) -> None:
    session.add(
        JevReading(
            document_id=job.document_id,
            ticker_id=job.ticker_id,
            model_requested=model,
            model_resolved=result.model_resolved,
            question_set=question_set,
            response_id=result.response_id,
            p_negative=result.p_negative,
            p_neutral=result.p_neutral,
            p_positive=result.p_positive,
            event_type=result.event_type,
            p_routine=result.p_routine,
            answers=result.answers,
            input_tokens=result.input_tokens,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
        )
    )
    session.commit()


def run_backfill(
    session: Session,
    jobs: Sequence[ReadJob],
    client: JevClient,
    *,
    max_cost_usd: float,
    concurrency: int = 4,
    echo: Callable[[str], None] = print,
    model: str = MODEL,
    question_set: str = QUESTION_SET,
) -> BackfillSummary:
    """Read `jobs` until done, a fatal error, repeated failures, or the budget is reached.

    The budget guard stops submitting once the summed `usage.cost` reaches `max_cost_usd`;
    calls already in flight (at most `concurrency - 1`) still finish and are stored.
    """
    summary = BackfillSummary()
    queue: Iterator[ReadJob] = iter(jobs)
    remaining = len(jobs)
    in_flight: dict[Future[JevResult], ReadJob] = {}
    failures_in_a_row = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        while True:
            while summary.stopped is None and len(in_flight) < concurrency:
                if summary.cost_usd >= max_cost_usd:
                    if remaining:
                        summary.budget_reached = True
                        summary.stopped = (
                            f"budget reached: ${summary.cost_usd:.4f} of ${max_cost_usd:.2f}"
                        )
                    break
                job = next(queue, None)
                if job is None:
                    break
                remaining -= 1
                in_flight[pool.submit(client.read, job_state(session, job))] = job
            if not in_flight:
                break
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                job = in_flight.pop(future)
                try:
                    result = future.result()
                except JevFatalError as error:
                    summary.stopped = str(error)
                    continue
                except JevError as error:
                    summary.skipped.append((job, str(error)))
                    echo(f"skip {job.symbol} {job.document_id}: {error}")
                    failures_in_a_row += 1
                    if failures_in_a_row >= MAX_CONSECUTIVE_FAILURES and summary.stopped is None:
                        summary.stopped = (
                            f"{MAX_CONSECUTIVE_FAILURES} documents in a row failed; "
                            f"the last error was: {error}"
                        )
                    continue
                failures_in_a_row = 0
                save_reading(session, job, result, model, question_set)
                summary.read += 1
                summary.cost_usd += result.cost_usd
                summary.input_tokens += result.input_tokens
                summary.builds[result.model_resolved] += 1
                if summary.read % PROGRESS_EVERY == 0:
                    echo(f"read {summary.read}/{len(jobs)} | ${summary.cost_usd:.4f}")
    summary.not_started = remaining
    return summary
