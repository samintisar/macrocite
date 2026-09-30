"""Spec 04: the live session review on stored bars, with the real v2-none-cash config: the
trailing stop on a hand-worked series, its timing, exits, and what is discarded."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, select

from live_helpers import (
    add_benchmark,
    add_pair,
    add_prices,
    make_ledger,
    send_signal,
    universe,
)
from signalbench.db.models import EarningsEvent, Ticker
from signalbench.ingest.earnings import FINNHUB_EARNINGS_SOURCE
from signalbench.live.ledger import Ledger
from signalbench.live.review import SessionReview, live_market, review_session
from signalbench.strategy.config import load_strategy_config
from strategy_helpers import WeekdaySessions, weekdays

CONFIG = load_strategy_config(Path(__file__).parents[1] / "data" / "strategy_v2-none-cash.yaml")[0]
DAYS = weekdays(date(2026, 3, 2), 70)
SIGNAL, ENTRY = 59, 60  # indexes into DAYS
# 60 flat closes, so ATR(14) is exactly 2 (high/low are close +/- 1), then up by 1 a session:
# the trailing stop is the highest close - 3 x 2. 97.2 on 64 is below the stop raised on 63.
US = [100.0] * 60 + [100.5, 101.5, 102.5, 103.5, 97.2, 110.0, 111.0] + [111.0] * 3
UNIVERSE = universe(("NVDA", "ZNVD", "Information Technology"), ("XOM", "ZXOM", "Energy"))


@pytest.fixture
def ledger(session: Session) -> Ledger:
    nvda, znvd = add_pair(session)
    xom, zxom = add_pair(session, "XOM", "ZXOM", "Energy")
    add_prices(session, nvda, DAYS, US)
    add_prices(session, znvd, DAYS, [close / 5 for close in US], volume=100)  # ratio 0.2
    add_prices(session, xom, DAYS, [50.0] * 70)
    add_prices(session, zxom, DAYS, [10.0] * 70, volume=100)
    add_benchmark(session, DAYS, [300.0] * 70)
    ledger = make_ledger(session, today=DAYS[-1])
    ledger.record_cash(Decimal("100.00"), DAYS[0], "deposit")
    return ledger


def _open(ledger: Ledger) -> int:
    signal = send_signal(ledger, DAYS[SIGNAL], us_close="100", us_stop="96", cdr_close="20")
    assert signal.id is not None
    ledger.record_fill(cdr_symbol="ZNVD", side="buy", quantity=Decimal(1),
                       price_cad=Decimal("20.10"), trade_date=DAYS[ENTRY], signal_id=signal.id)
    return signal.id


def _review(ledger: Ledger, session: Session, index: int, **extra: object) -> SessionReview:
    day = DAYS[index]
    market = live_market(session, UNIVERSE, CONFIG, WeekdaySessions(), day)
    return review_session(ledger, market, CONFIG, day, **extra)


def _scan(ledger: Ledger, session: Session, indexes: range) -> dict[int, SessionReview]:
    """What the evening scan does with each review: write the alerts and the raises."""
    reviews = {}
    for index in indexes:
        review = reviews[index] = _review(ledger, session, index)
        for call in review.exits:
            ledger.record_exit_alert(signal_id=call.signal_id, as_of=DAYS[index],
                                     reason=call.reason)
        for up in review.raises:
            ledger.record_stop_update(signal_id=up.signal_id, session=up.session,
                                      new_us_stop=up.new_us_stop)
    return reviews


def _earnings(session: Session, symbol: str, index: int) -> None:
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
    session.add(EarningsEvent(ticker_id=ticker.id, event_date=DAYS[index],
                              source=FINNHUB_EARNINGS_SOURCE))
    session.commit()


def test_the_trailing_stop_on_a_hand_worked_series(ledger: Ledger, session: Session) -> None:
    signal_id = _open(ledger)
    reviews = _scan(ledger, session, range(ENTRY, 65))
    # highest - 6: 94.5 and 95.5 stay below the initial 96; 96.5 on 62; 97.5 on 63.
    raises = [(r.session, r.old_us_stop, r.new_us_stop) for v in reviews.values() for r in v.raises]
    assert raises == [
        (DAYS[62], Decimal(96), Decimal("96.5")), (DAYS[63], Decimal("96.5"), Decimal("97.5")),
    ]
    assert ledger.current_stop(signal_id).cdr == Decimal("19.50")  # 97.5 x 0.2
    # 97.2 on 64 is below 97.5, raised at 63's close and in force from 64: a stop exit, and
    # no raise on the exit day. The stop in force during 63 was 96.5.
    assert [(e.signal_id, e.reason) for e in reviews[64].exits] == [(signal_id, "stop")]
    assert reviews[64].raises == ()
    assert ledger.stop_in_force(signal_id, DAYS[63]).us == Decimal("96.5")
    assert ledger.open_exit_alert(signal_id) is not None


def test_no_raise_or_second_alert_while_an_exit_alert_is_sent(
    ledger: Ledger, session: Session
) -> None:
    signal_id = _open(ledger)
    _scan(ledger, session, range(ENTRY, 65))  # the stop alert on 64 stays sent
    on_65 = _review(ledger, session, 65)  # 110: decide() raises the stop, which is dropped
    assert (on_65.exits, on_65.raises, on_65.discarded) == ((), (), (str(signal_id),))
    alert = ledger.open_exit_alert(signal_id)
    assert alert is not None and alert.id is not None
    ledger.mark_exit_alert(alert.id, "ignored")  # managed again from the next session
    [up] = _review(ledger, session, 66).raises
    assert up.new_us_stop > Decimal("97.5")


def test_a_stop_exit_comes_before_an_earnings_exit(ledger: Ledger, session: Session) -> None:
    _open(ledger)
    _earnings(session, "NVDA", 66)  # within 2 sessions of 64
    reviews = _scan(ledger, session, range(ENTRY, 65))
    assert [e.reason for v in reviews.values() for e in v.exits] == ["stop"]


def test_the_earnings_exit_fires_from_a_finnhub_calendar_date(
    ledger: Ledger, session: Session
) -> None:
    signal_id = _open(ledger)
    _earnings(session, "NVDA", 63)  # an upcoming calendar date, no SEC 2.02 filing
    reviews = _scan(ledger, session, range(ENTRY, 63))
    assert [(i, e.reason) for i, v in reviews.items() for e in v.exits] == [(61, "earnings")]
    assert ledger.open_exit_alert(signal_id) is not None


def test_a_position_is_never_closed_for_time_and_has_no_target(
    ledger: Ledger, session: Session
) -> None:
    signal_id = _open(ledger)
    _scan(ledger, session, range(ENTRY, 64))
    [position] = ledger.portfolio_state(DAYS[63]).positions
    assert (position.target, position.time_limit, position.sessions_held) == (None, None, 4)
    assert ledger.open_exit_alert(signal_id) is None


def test_a_manual_position_holds_its_slot_and_its_decisions_are_discarded(
    ledger: Ledger, session: Session
) -> None:
    fill = ledger.record_fill(cdr_symbol="ZXOM", side="buy", quantity=Decimal(2),
                              price_cad=Decimal(10), trade_date=DAYS[ENTRY])
    _earnings(session, "XOM", 62)  # decide() returns an earnings exit for it on 61
    review = _review(ledger, session, 61)
    assert review.exits == ()
    assert review.discarded == (f"manual-{fill.id}",)
    assert review.state.slots_used() == 1


def test_a_held_symbol_gets_no_decision(ledger: Ledger, session: Session) -> None:
    signal_id = _open(ledger)
    _scan(ledger, session, range(ENTRY, 64))
    review = _review(ledger, session, 64, hold={"NVDA"})  # the scale check held NVDA tonight
    assert (review.exits, review.discarded) == ((), (str(signal_id),))
