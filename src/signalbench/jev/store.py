"""Database reads for spec 03: readings keyed by legal close, and the v1 trade logs."""

from collections.abc import Collection

from sqlalchemy import func
from sqlmodel import Session, col, select

from signalbench.db.models import JevReading, RawDocument, Ticker
from signalbench.jev.questions import MODEL, QUESTION_SET
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
            )
        )
    for readings in out.values():
        readings.sort(key=lambda r: (r.legal_close, r.document_id))
    return out
