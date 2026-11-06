"""Spec 04: signals and exit alerts (written by the scan), managed vs manual, and trade R."""

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session

from live_helpers import add_pair, make_ledger, send_signal
from repo_helpers import evening
from signalbench.live.ledger import Ledger, LedgerError

D = date(2026, 6, 1)  # a Monday: the signal session
E = date(2026, 6, 2)  # its entry session


@pytest.fixture
def ledger(session: Session) -> Ledger:
    add_pair(session)
    add_pair(session, "AMD", "ZAMD")
    ledger = make_ledger(session)
    ledger.record_cash(Decimal("1000.00"), D, "deposit")
    return ledger


def _fill(ledger: Ledger, side: str, quantity: str, price: str, day: date = E,
          cdr: str = "ZNVD", **links: int) -> None:
    ledger.record_fill(cdr_symbol=cdr, side=side, quantity=Decimal(quantity),
                       price_cad=Decimal(price), trade_date=day, **links)


def test_a_signal_derives_its_stop_pct_and_cdr_stop(ledger: Ledger) -> None:
    signal = send_signal(ledger, D)
    assert (signal.stop_pct, signal.cdr_stop, signal.status) == (
        Decimal("0.06"), Decimal("37.60"), "sent"
    )
    with pytest.raises(LedgerError, match="a NVDA signal for 2026-06-01 is already recorded"):
        send_signal(ledger, D)
    with pytest.raises(LedgerError, match="ZAMD is not the CDR of NVDA"):
        send_signal(ledger, E, cdr="ZAMD")
    with pytest.raises(LedgerError, match="0 < stop < close"):
        send_signal(ledger, E, us_stop="200")


def test_a_linked_buy_takes_the_signal_and_opens_a_managed_position(ledger: Ledger) -> None:
    signal = send_signal(ledger, D)
    assert signal.id is not None
    _fill(ledger, "buy", "1", "40.20", signal_id=signal.id)
    _fill(ledger, "buy", "1", "40.30", signal_id=signal.id)  # a second lot of the same signal
    assert signal.status == "taken"
    _fill(ledger, "buy", "2", "12.00", cdr="ZAMD")
    [manual, managed] = ledger.open_episodes()  # by CDR symbol: ZAMD, ZNVD
    assert (managed.managed, managed.key, len(managed.fills)) == (True, str(signal.id), 2)
    assert (manual.managed, manual.key) == (False, f"manual-{manual.opening.id}")


def test_links_that_do_not_fit_are_refused(ledger: Ledger) -> None:
    signal = send_signal(ledger, D)
    other = send_signal(ledger, D, us="AMD", cdr="ZAMD")
    assert signal.id is not None and other.id is not None
    with pytest.raises(LedgerError, match="only a buy can be linked to a signal"):
        _fill(ledger, "sell", "1", "40", signal_id=signal.id)
    with pytest.raises(LedgerError, match="is for another CDR, not ZAMD"):
        _fill(ledger, "buy", "1", "12", cdr="ZAMD", signal_id=signal.id)
    _fill(ledger, "buy", "1", "40.00")  # manual first
    with pytest.raises(LedgerError, match="adds to a position it did not open"):
        _fill(ledger, "buy", "1", "40.10", signal_id=signal.id)
    ledger.mark_signal(other.id, "skipped", "disagree")
    with pytest.raises(LedgerError, match="is skipped; record the buy without the signal"):
        _fill(ledger, "buy", "1", "12", cdr="ZAMD", signal_id=other.id)


def test_a_signal_opens_at_most_one_position(ledger: Ledger) -> None:
    signal = send_signal(ledger, D)
    assert signal.id is not None
    _fill(ledger, "buy", "1", "40.20", signal_id=signal.id)
    _fill(ledger, "sell", "1", "41.00", date(2026, 6, 3))
    with pytest.raises(LedgerError, match="already opened a position that has closed"):
        _fill(ledger, "buy", "1", "40.50", date(2026, 6, 4), signal_id=signal.id)


def test_an_expired_signal_can_still_be_taken_but_a_marked_one_cannot_change(
    ledger: Ledger,
) -> None:
    signal = send_signal(ledger, D)
    assert signal.id is not None
    ledger.mark_signal(signal.id, "expired")
    with pytest.raises(LedgerError, match="already expired"):
        ledger.mark_signal(signal.id, "withdrawn")
    _fill(ledger, "buy", "1", "40.20", signal_id=signal.id)  # reported after the scan
    assert ledger.signal(signal.id).status == "taken"
    other = send_signal(ledger, E)
    assert other.id is not None
    with pytest.raises(LedgerError, match="a skip, and only a skip, needs a skip reason"):
        ledger.mark_signal(other.id, "skipped")
    with pytest.raises(LedgerError, match="skip reason must be one of"):
        ledger.mark_signal(other.id, "skipped", "bored")  # type: ignore[arg-type]


def test_a_sent_signal_holds_its_slot_through_its_entry_session_s_close(
    ledger: Ledger,
) -> None:
    signal = send_signal(ledger, D)  # expires at 16:00 New York on E
    assert signal.id is not None
    assert ledger.pending_signals(D) == []  # written after D's decision
    assert [s.id for s in ledger.pending_signals(E)] == [signal.id]  # maybe bought, unreported
    assert ledger.pending_signals(date(2026, 6, 3)) == []
    ledger.mark_signal(signal.id, "expired")
    assert ledger.pending_signals(E) == []
    taken = send_signal(ledger, E)
    assert taken.id is not None
    _fill(ledger, "buy", "1", "40.10", date(2026, 6, 3), signal_id=taken.id)
    assert ledger.pending_signals(date(2026, 6, 3)) == []  # a position now, not a pending entry


def test_one_open_exit_alert_per_position_and_a_linked_sale_closes_it(ledger: Ledger) -> None:
    signal = send_signal(ledger, D)
    assert signal.id is not None
    with pytest.raises(LedgerError, match="has no open position"):
        ledger.record_exit_alert(signal_id=signal.id, as_of=E, reason="stop")
    _fill(ledger, "buy", "2", "40.20", signal_id=signal.id)
    alert = ledger.record_exit_alert(signal_id=signal.id, as_of=E, reason="stop")
    with pytest.raises(LedgerError, match="already has an open exit alert"):
        ledger.record_exit_alert(signal_id=signal.id, as_of=E, reason="earnings")
    ledger.mark_exit_alert(alert.id or 0, "ignored")
    again = ledger.record_exit_alert(signal_id=signal.id, as_of=date(2026, 6, 3), reason="stop")
    assert again.id is not None
    _fill(ledger, "sell", "2", "37.00", date(2026, 6, 4), exit_alert_id=again.id)
    assert ledger.exit_alert(again.id).status == "done"
    with pytest.raises(LedgerError, match="already done"):
        ledger.mark_exit_alert(again.id, "ignored")


def test_trade_r_is_pnl_over_planned_risk_in_cad_and_in_us_terms(ledger: Ledger) -> None:
    # Signal: US 200 / stop 188 (6%), CDR 40 / stop 37.60. Buy 2 @ 40.20, sell 2 @ 43.00.
    # P&L 86.00 - 80.40 = 5.60; planned risk 2 x (40 - 37.60) = 4.80; R = 5.60 / 4.80.
    signal = send_signal(ledger, D)
    assert signal.id is not None
    _fill(ledger, "buy", "2", "40.20", signal_id=signal.id)
    _fill(ledger, "sell", "2", "43.00", date(2026, 6, 5))
    _fill(ledger, "buy", "1", "12.00", cdr="ZAMD")
    _fill(ledger, "sell", "1", "11.00", date(2026, 6, 5), cdr="ZAMD")
    managed, manual = ledger.closed_trades()
    assert (managed.pnl, managed.planned_risk, managed.r) == (
        Decimal("5.60"), Decimal("4.80"), Decimal("5.60") / Decimal("4.80")
    )
    # US-equivalent: P&L x (200 / 40) over units x (200 - 188).
    us_r = managed.pnl * Decimal(200) / Decimal(40) / (2 * (Decimal(200) - Decimal(188)))
    assert us_r == managed.r
    assert (manual.episode.managed, manual.pnl, manual.r) == (False, Decimal("-1.00"), None)


def test_an_adopted_signal_is_recorded_with_its_source(ledger: Ledger) -> None:
    signal = ledger.record_signal(
        as_of=D, us_symbol="NVDA", cdr_symbol="ZNVD", us_signal_close=Decimal(200),
        us_stop=Decimal(188), cdr_signal_close=Decimal(40), suggested_units=Decimal(3),
        order_type="market", risk_amount_cad=Decimal("7.20"), explanation="Adopted.",
        expires_at=evening(E, 16), source="adopted",
    )
    assert (signal.source, signal.cdr_stop) == ("adopted", Decimal("37.6000"))


def test_a_buy_matches_a_sent_or_expired_scan_signal_from_the_last_5_sessions(
    ledger: Ledger,
) -> None:
    signal = send_signal(ledger, D)  # Monday 2026-06-01
    assert signal.id is not None
    assert ledger.matching_signal("ZNVD", D) == signal  # the same day
    ledger.mark_signal(signal.id, "expired")
    assert ledger.matching_signal("ZNVD", date(2026, 6, 8)) == signal  # 5 sessions after
    assert ledger.matching_signal("ZNVD", date(2026, 6, 9)) is None  # 6 sessions after
    assert ledger.matching_signal("ZNVD", date(2026, 5, 29)) is None  # before it
    assert ledger.matching_signal("ZAMD", E) is None  # another CDR


def test_skipped_withdrawn_taken_and_adopted_signals_are_never_matched(ledger: Ledger) -> None:
    skipped = send_signal(ledger, D)
    assert skipped.id is not None
    ledger.mark_signal(skipped.id, "skipped", "disagree")
    assert ledger.matching_signal("ZNVD", E) is None
    ledger.record_signal(
        as_of=E, us_symbol="NVDA", cdr_symbol="ZNVD", us_signal_close=Decimal(200),
        us_stop=Decimal(188), cdr_signal_close=Decimal(40), suggested_units=Decimal(1),
        order_type="market", risk_amount_cad=Decimal("2.40"), explanation="Adopted.",
        expires_at=evening(E, 16), source="adopted",
    )
    assert ledger.matching_signal("ZNVD", E) is None
    taken = send_signal(ledger, date(2026, 6, 3))
    _fill(ledger, "buy", "1", "40", date(2026, 6, 4), signal_id=taken.id or 0)
    assert ledger.matching_signal("ZNVD", date(2026, 6, 4)) is None
