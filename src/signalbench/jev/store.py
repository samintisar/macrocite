"""Database reads for spec 03: readings keyed by legal close, and calibration samples."""

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from datetime import date

from sqlalchemy import func
from sqlmodel import Session, col, select

from signalbench.db.models import JevReading, RawDocument, Ticker
from signalbench.jev.calibration import Sample, label_for
from signalbench.jev.questions import MODEL, QUESTION_SET
from signalbench.market.bars import adjusted_bars
from signalbench.market.legal_close import LegalCloses
from signalbench.strategy.readings import DocumentReading


def load_document_readings(
    session: Session,
    symbols: Collection[str],
    legal_closes: LegalCloses,
    *,
    model: str = MODEL,
    question_set: str = QUESTION_SET,
) -> dict[str, list[DocumentReading]]:
    """Every reading for `symbols`, keyed by the document's legal close (8-K: acceptance time,
    else publish time; news: publish time). Documents past the last known close are left out."""
    tickers = {
        ticker.id: ticker.symbol
        for ticker in session.exec(select(Ticker).where(col(Ticker.symbol).in_(list(symbols))))
    }
    out: dict[str, list[DocumentReading]] = {symbol: [] for symbol in symbols}
    rows = session.exec(
        select(JevReading, func.coalesce(RawDocument.acceptance_at, RawDocument.published_at))
        .join(RawDocument, col(RawDocument.id) == JevReading.document_id)
        .where(
            col(JevReading.ticker_id).in_(list(tickers)),
            JevReading.model_requested == model,
            JevReading.question_set == question_set,
        )
    ).all()
    for reading, stamp in rows:
        close = legal_closes.of(stamp)
        if close is None:
            continue
        out[tickers[reading.ticker_id]].append(
            DocumentReading(
                legal_close=close,
                p_negative=reading.p_negative,
                p_neutral=reading.p_neutral,
                p_positive=reading.p_positive,
                p_routine=reading.p_routine,
                event_type=reading.event_type,
                document_id=str(reading.document_id),
                model_resolved=reading.model_resolved,
            )
        )
    for readings in out.values():
        readings.sort(key=lambda r: (r.legal_close, r.document_id))
    return out


def reading_builds(
    session: Session, *, model: str = MODEL, question_set: str = QUESTION_SET
) -> dict[str, int]:
    """How many stored readings each resolved model build produced."""
    builds = session.exec(
        select(JevReading.model_resolved).where(
            JevReading.model_requested == model, JevReading.question_set == question_set
        )
    ).all()
    return dict(sorted(Counter(builds).items()))


def resolved_builds(documents: Mapping[str, Sequence[DocumentReading]]) -> dict[str, int]:
    """How many readings actually loaded (e.g. by `load_document_readings`) each resolved model
    build produced, so it sums to the readings count a run or filter decision actually used."""
    counts = Counter(reading.model_resolved for rows in documents.values() for reading in rows)
    return dict(sorted(counts.items()))


def _closes_by_date(session: Session, symbol: str) -> dict[date, float]:
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).first()
    if ticker is None:
        return {}
    return {bar.date: bar.close for bar in adjusted_bars(session, ticker.id)}


def calibration_samples(
    session: Session,
    symbols: Collection[str],
    benchmark_symbol: str,
    legal_closes: LegalCloses,
    sessions: Sequence[date],
    *,
    model: str = MODEL,
    question_set: str = QUESTION_SET,
) -> tuple[list[Sample], int]:
    """One labeled sample per reading, and how many readings could not be labeled yet."""
    benchmark = _closes_by_date(session, benchmark_symbol)
    readings = load_document_readings(
        session, symbols, legal_closes, model=model, question_set=question_set
    )
    samples: list[Sample] = []
    unlabeled = 0
    for symbol, rows in sorted(readings.items()):
        closes = _closes_by_date(session, symbol)
        for row in rows:
            label = label_for(row.legal_close, closes, benchmark, sessions)
            if label is None:
                unlabeled += 1
                continue
            samples.append(
                Sample(row.legal_close, row.p_negative, row.p_neutral, row.p_positive, label)
            )
    return samples, unlabeled
