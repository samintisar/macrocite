from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlmodel import Session

from signalbench.db.models import (
    DocType,
    DocumentTicker,
    JevReading,
    RawDocument,
    Ticker,
    TickerKind,
)
from signalbench.jev.store import load_document_readings
from signalbench.market.legal_close import LegalCloses
from signalbench.strategy.readings import DocumentReading, JevReadingsView, ReadingsView
from strategy_helpers import WeekdaySessions, weekdays

NEW_YORK = ZoneInfo("America/New_York")
DAYS = weekdays(date(2024, 1, 1), 40)  # Monday 2024-01-01 onward, no holidays
D = DAYS[10]  # Monday 2024-01-15


def _reading(
    legal_close: date,
    *,
    p_negative: float = 0.1,
    p_positive: float = 0.1,
    p_routine: float = 0.1,
) -> DocumentReading:
    return DocumentReading(
        legal_close=legal_close,
        p_negative=p_negative,
        p_neutral=max(0.0, 1.0 - p_negative - p_positive),
        p_positive=p_positive,
        p_routine=p_routine,
        event_type="earnings",
        document_id="doc",
    )


def _view(*readings: DocumentReading, theta: float | None = None) -> JevReadingsView:
    return JevReadingsView({"AAA": list(readings)}, DAYS, block_theta=theta)


def test_positive_needs_p_positive_at_least_070_and_p_routine_below_050() -> None:
    assert _view(_reading(D, p_positive=0.70, p_routine=0.49)).sentiment_trigger("AAA", D)
    assert not _view(_reading(D, p_positive=0.69, p_routine=0.1)).sentiment_trigger("AAA", D)
    assert not _view(_reading(D, p_positive=0.9, p_routine=0.50)).sentiment_trigger("AAA", D)


def test_the_sentiment_trigger_lasts_3_sessions_from_the_legal_close() -> None:
    view = _view(_reading(D, p_positive=0.8))
    assert [view.sentiment_trigger("AAA", day) for day in DAYS[9:14]] == [
        False,  # the session before the legal close: not visible yet
        True,
        True,
        True,
        False,  # the 4th session
    ]


def test_windows_count_sessions_not_calendar_days() -> None:
    friday = DAYS[4]  # 2024-01-05
    view = _view(_reading(friday, p_positive=0.8))
    assert view.sentiment_trigger("AAA", DAYS[6])  # Tuesday is the 3rd session
    assert not view.sentiment_trigger("AAA", DAYS[7])


def test_a_catalyst_lasts_10_sessions() -> None:
    view = _view(_reading(D, p_positive=0.8))
    assert [view.catalyst("AAA", day) for day in DAYS[9:21]] == [False] + [True] * 10 + [False]


def test_blocking_needs_the_filter_on_and_p_negative_at_least_theta() -> None:
    negative = _reading(D, p_negative=0.7)
    assert not _view(negative).blocked("AAA", D)  # filter off (information only)
    on = _view(negative, theta=0.7)
    assert [on.blocked("AAA", day) for day in DAYS[9:21]] == [False] + [True] * 10 + [False]
    assert not _view(_reading(D, p_negative=0.69), theta=0.7).blocked("AAA", D)


def test_max_p_negative_over_the_10_session_window() -> None:
    view = _view(
        _reading(DAYS[0], p_negative=0.95),  # 21 sessions before DAYS[21]: outside
        _reading(DAYS[12], p_negative=0.4),
        _reading(DAYS[20], p_negative=0.6),
        _reading(DAYS[22], p_negative=0.99),  # after the as-of: not visible
    )
    assert view.max_p_negative("AAA", DAYS[21]) == 0.6
    assert view.max_p_negative("AAA", DAYS[11]) is None
    assert view.max_p_negative("BBB", DAYS[21]) is None


def test_a_symbol_without_readings_is_neither_positive_nor_negative() -> None:
    view: ReadingsView = _view(_reading(D, p_positive=0.9, p_negative=0.9), theta=0.5)
    assert not view.blocked("BBB", D)
    assert not view.catalyst("BBB", D)
    assert not view.sentiment_trigger("BBB", D)


def _seed_filing(
    session: Session, accepted: datetime, *, model: str = "typesafe/jev-1.13"
) -> None:
    ticker = Ticker(symbol="AAA", company_name="Aaa Corp", kind=TickerKind.us_stock)
    document = RawDocument(
        source="sec_edgar",
        external_id="0000000000-24-000001",
        doc_type=DocType.eight_k,
        raw_text="<html></html>",
        text="Record results.",
        published_at=datetime(2024, 1, 15, tzinfo=UTC),  # the filing date; acceptance wins
        acceptance_at=accepted,
    )
    session.add_all([ticker, document])
    session.flush()
    session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
    session.add(
        JevReading(
            document_id=document.id, ticker_id=ticker.id, model_requested=model,
            model_resolved=f"{model}-20260917", question_set="q1", response_id="gen-dec-1",
            p_negative=0.05, p_neutral=0.15, p_positive=0.8, event_type="earnings",
            p_routine=0.1, answers={}, input_tokens=500, cost_usd=0.00002, latency_ms=900,
        )
    )
    session.commit()


def _closes() -> LegalCloses:
    return LegalCloses(WeekdaySessions().session_closes(DAYS[0], DAYS[-1]))


def test_a_filing_accepted_at_1605_is_invisible_at_that_close_and_visible_the_next(
    session: Session,
) -> None:
    _seed_filing(session, datetime.combine(D, time(16, 5), tzinfo=NEW_YORK))
    readings = load_document_readings(session, ["AAA"], _closes())
    assert [r.legal_close for r in readings["AAA"]] == [DAYS[11]]
    view = JevReadingsView(readings, DAYS, block_theta=None)
    assert view.sentiment_trigger("AAA", D) is False
    assert view.sentiment_trigger("AAA", DAYS[11]) is True


def test_a_filing_accepted_before_the_close_is_visible_that_day(session: Session) -> None:
    _seed_filing(session, datetime.combine(D, time(15, 59), tzinfo=NEW_YORK))
    readings = load_document_readings(session, ["AAA"], _closes())
    assert [r.legal_close for r in readings["AAA"]] == [D]
    assert readings["AAA"][0].p_positive == 0.8


def test_readings_of_another_model_or_symbol_are_not_loaded(session: Session) -> None:
    _seed_filing(session, datetime.combine(D, time(9, 0), tzinfo=NEW_YORK), model="other/model")
    assert load_document_readings(session, ["AAA"], _closes()) == {"AAA": []}
    assert load_document_readings(session, ["BBB"], _closes()) == {"BBB": []}
