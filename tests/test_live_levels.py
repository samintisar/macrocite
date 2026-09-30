"""Spec 04: CDR marks, US-equivalent levels, and the trailing stop's rows and timing."""

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session

from live_helpers import add_pair, add_prices, make_ledger, send_signal
from signalbench.db.models import TradeSignal
from signalbench.live.ledger import Ledger, LedgerError
from signalbench.live.levels import SignalLevels, StopLevels

D, E, F, G = date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3), date(2026, 6, 4)


@pytest.fixture
def ledger(session: Session) -> Ledger:
    stock, receipt = add_pair(session)
    add_prices(session, stock, [D, E, F, G], [200, 201, 202, 203])
    # The CDR trades on D and E, then only quotes: the last traded ratio is 40.20 / 201 = 0.2.
    add_prices(session, receipt, [D, E, F, G], [40, 40.2, 40.5, 40.5], volumes=[100, 100, 0, 0])
    ledger = make_ledger(session)
    ledger.record_cash(Decimal("100.00"), D, "deposit")
    return ledger


def _open(ledger: Ledger) -> TradeSignal:
    signal = send_signal(ledger, D)  # US 200 / stop 188, CDR 40 / stop 37.60
    assert signal.id is not None
    ledger.record_fill(cdr_symbol="ZNVD", side="buy", quantity=Decimal(1),
                       price_cad=Decimal("40.20"), trade_date=E, signal_id=signal.id)
    return signal


def test_the_cdr_mark_uses_the_last_traded_ratio_not_the_stale_quote(
    ledger: Ledger, session: Session
) -> None:
    cdr = ledger._cdr("ZNVD")
    assert ledger.cdr_ratio(cdr, G) == Decimal("0.2")
    assert ledger.cdr_mark(cdr, G) == Decimal("40.60")  # 203 x 0.2, not the stale 40.50
    assert ledger.cdr_mark(cdr, D) == Decimal(40)


def test_a_cdr_that_never_traded_is_marked_at_its_latest_close(session: Session) -> None:
    stock, receipt = add_pair(session, "AMD", "ZAMD")
    add_prices(session, stock, [D, E], [100, 110])
    add_prices(session, receipt, [D, E], [10, 10.5], volume=0)
    ledger = make_ledger(session)
    cdr = ledger._cdr("ZAMD")
    assert ledger.cdr_mark(cdr, E) == Decimal("10.5")
    assert ledger.cdr_ratio(cdr, E) == Decimal("10.5") / Decimal(110)
    assert ledger.cdr_mark(ledger._cdr("ZAMD"), date(2026, 5, 29)) is None


def test_levels_without_splits_are_the_signal_s_and_entry_us_scales_the_fill(
    ledger: Ledger,
) -> None:
    signal = _open(ledger)
    assert signal.id is not None
    assert ledger.signal_levels(signal.id) == SignalLevels(
        Decimal(200), Decimal(188), Decimal(40), Decimal("37.60")
    )
    assert ledger.entry_us(signal.id) == Decimal("201.00")  # 200 x 40.20 / 40
    assert ledger.current_stop(signal.id) == StopLevels(Decimal(188), Decimal("37.60"))


def test_a_raise_applies_from_the_next_session_with_the_cdr_stop_at_the_traded_ratio(
    ledger: Ledger,
) -> None:
    signal = _open(ledger)
    assert signal.id is not None
    row = ledger.record_stop_update(signal_id=signal.id, session=F, new_us_stop=Decimal(190))
    assert (row.reason, row.old_us_stop, row.new_us_stop, row.old_cdr_stop, row.new_cdr_stop) == (
        "trail", Decimal(188), Decimal(190), Decimal("37.60"), Decimal("38.00")  # 190 x 0.2
    )
    assert ledger.stop_in_force(signal.id, F) == StopLevels(Decimal(188), Decimal("37.60"))
    assert ledger.stop_in_force(signal.id, G) == StopLevels(Decimal(190), Decimal("38.00"))
    assert ledger.current_stop(signal.id).us == Decimal(190)


def test_a_trailing_stop_only_rises_once_per_session_on_an_open_managed_position(
    ledger: Ledger,
) -> None:
    signal = _open(ledger)
    assert signal.id is not None
    ledger.record_stop_update(signal_id=signal.id, session=F, new_us_stop=Decimal(190))
    for session_, stop, message in [
        (G, "189.5", "only rises: 189.5000 is not above the stop 190"),
        (G, "190", "is not above the stop 190"),
        (F, "191", "already has a raise for 2026-06-03"),
        (D, "189", "has no open position on 2026-06-01"),
    ]:
        with pytest.raises(LedgerError, match=message):
            ledger.record_stop_update(
                signal_id=signal.id, session=session_, new_us_stop=Decimal(stop)
            )
    ledger.record_exit_alert(signal_id=signal.id, as_of=G, reason="stop")
    with pytest.raises(LedgerError, match="has an open exit alert: no stop raise"):
        ledger.record_stop_update(signal_id=signal.id, session=G, new_us_stop=Decimal(195))
    unfilled = send_signal(ledger, E)
    assert unfilled.id is not None
    with pytest.raises(LedgerError, match="has no open position"):
        ledger.record_stop_update(signal_id=unfilled.id, session=F, new_us_stop=Decimal(195))
