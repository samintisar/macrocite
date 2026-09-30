"""Spec 04 splits: the hand-checked CDR split, US splits on the tool's levels, voids, the
withdrawal of a sent signal, and the scale check."""

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session, col, select

from live_helpers import add_pair, add_prices, load_example, make_ledger, send_signal
from signalbench.db.models import Price, StopUpdateRow, TradeSignal
from signalbench.live.ledger import Ledger, LedgerError
from signalbench.live.levels import SignalLevels, StopLevels

D, E, F, G = date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3), date(2026, 6, 4)
EX = date(2026, 6, 8)  # a Monday ex-date


@pytest.fixture
def ledger(session: Session) -> Ledger:
    add_pair(session)
    add_pair(session, "TST", "ZTST")
    ledger = make_ledger(session)
    ledger.record_cash(Decimal("1000.00"), date(2026, 3, 2), "deposit")
    return ledger


def _split(ledger: Ledger, kind: str, symbol: str, ratio: str, ex: date = EX) -> int:
    action = ledger.record_split(kind=kind, symbol=symbol, ex_date=ex, ratio=Decimal(ratio),
                                 source="owner")
    assert action.id is not None
    return action.id


def _fill(ledger: Ledger, side: str, quantity: str, price: str, day: date,
          cdr: str = "ZTST", **links: int) -> None:
    ledger.record_fill(cdr_symbol=cdr, side=side, quantity=Decimal(quantity),
                       price_cad=Decimal(price), trade_date=day, **links)


def _open(ledger: Ledger, units: str = "2") -> TradeSignal:
    signal = send_signal(ledger, D)  # US 200 / stop 188, CDR 40 / stop 37.60
    assert signal.id is not None
    _fill(ledger, "buy", units, "40.20", E, cdr="ZNVD", signal_id=signal.id)
    return signal


def test_the_hand_checked_cdr_split_through_the_ledger(ledger: Ledger) -> None:
    example = load_example("cdr_split")  # buy 3 @ 30, 2-for-1, sell 4 @ 16
    buy, split, sell = example["events"]
    cash = ledger.cash()
    _fill(ledger, "buy", buy["quantity"], buy["price"], buy["date"])
    action = _split(ledger, "cdr_split", "ZTST", split["split"], split["date"])
    book = ledger.books()["ZTST"]
    assert (book.units, book.acb, book.per_unit) == (Decimal(6), Decimal("90.00"), Decimal(15))
    assert ledger.cash() == cash - Decimal("90.00")  # the split moves no cash
    ledger.void_corporate_action(action, "recorded by mistake")
    assert ledger.books()["ZTST"].units == Decimal(3)  # the void restores the pre-split units
    second = _split(ledger, "cdr_split", "ZTST", split["split"], split["date"])
    with pytest.raises(LedgerError, match="a sale of 7 on 2026-03-16 is more than the 6 units"):
        _fill(ledger, "sell", "7", sell["price"], sell["date"])
    _fill(ledger, "sell", sell["quantity"], sell["price"], sell["date"])
    book = ledger.books()["ZTST"]
    [disposition] = book.dispositions
    assert (disposition.acb, disposition.gain) == (Decimal("60.00"), Decimal("4.00"))
    assert (book.units, book.acb) == (Decimal(2), Decimal("30.00"))
    assert ledger.cash() == cash - Decimal("90.00") + Decimal("64.00")
    with pytest.raises(LedgerError, match="voiding corporate action"):  # 4 sold of 3 held
        ledger.void_corporate_action(second, "no")


def test_a_bad_split_is_refused(ledger: Ledger) -> None:
    for kind, symbol, ratio, message in [
        ("us_split", "NVDA", "1", "above 0 and not 1"),
        ("us_split", "NVDA", "-2", "above 0 and not 1"),
        ("us_split", "NVDA", "1.0000001", "more than 6 decimals"),
        ("us_split", "ZNVD", "2", "ZNVD is not a known US stock"),
        ("cdr_split", "ZTST", "2", "no open position or sent signal on 2026-06-08"),
        ("reverse", "NVDA", "2", "kind must be one of us_split, cdr_split"),
    ]:
        with pytest.raises(LedgerError, match=message):
            _split(ledger, kind, symbol, ratio)
    _split(ledger, "us_split", "NVDA", "2")
    with pytest.raises(LedgerError, match="NVDA us_split on 2026-06-08 is already recorded"):
        _split(ledger, "us_split", "NVDA", "2")


def test_a_us_split_rescales_the_tool_s_levels_and_never_r(ledger: Ledger, session: Session) -> None:
    signal = _open(ledger)
    assert signal.id is not None
    ledger.record_stop_update(signal_id=signal.id, session=F, new_us_stop=Decimal(190))
    action = _split(ledger, "us_split", "NVDA", "2")
    assert ledger.signal_levels(signal.id) == SignalLevels(
        Decimal(100), Decimal(94), Decimal(40), Decimal("37.60")
    )
    assert ledger.entry_us(signal.id) == Decimal("100.50")  # 201.00 / 2
    assert ledger.current_stop(signal.id).us == Decimal(95)  # the raised 190 / 2
    [row] = session.exec(select(StopUpdateRow).where(StopUpdateRow.reason == "split")).all()
    assert (row.session, row.old_us_stop, row.new_us_stop, row.corporate_action_id) == (
        EX, Decimal(190), Decimal(95), action
    )
    assert row.old_cdr_stop == row.new_cdr_stop  # a US split leaves the CDR stop alone
    # A forced rescan of F sees the stop in force during F: the initial 188, on today's scale.
    assert ledger.stop_in_force(signal.id, F).us == Decimal(94)
    _fill(ledger, "sell", "2", "43.00", date(2026, 6, 9), cdr="ZNVD")
    [trade] = ledger.closed_trades()
    assert trade.r == Decimal("5.60") / Decimal("4.80")  # the R of the unsplit fixture trade


def test_voiding_a_us_split_writes_a_row_that_undoes_it(ledger: Ledger, session: Session) -> None:
    signal = _open(ledger)
    assert signal.id is not None
    action = _split(ledger, "us_split", "NVDA", "2")
    assert ledger.current_stop(signal.id).us == Decimal(94)
    ledger.void_corporate_action(action, "yfinance listed a split that did not happen")
    assert ledger.current_stop(signal.id) == StopLevels(Decimal(188), Decimal("37.60"))
    assert ledger.signal_levels(signal.id).us_signal_close == Decimal(200)
    rows = session.exec(select(StopUpdateRow).order_by(col(StopUpdateRow.id))).all()
    assert [(r.old_us_stop, r.new_us_stop) for r in rows] == [
        (Decimal(188), Decimal(94)), (Decimal(94), Decimal(188))
    ]
    with pytest.raises(LedgerError, match=f"no corporate action {action} to void"):
        ledger.void_corporate_action(action, "twice")


def test_a_split_recorded_before_the_position_opened_still_rescales_its_stop(
    ledger: Ledger, session: Session
) -> None:
    signal = send_signal(ledger, D)
    assert signal.id is not None
    _split(ledger, "us_split", "NVDA", "4", E)  # while the signal was only sent
    _fill(ledger, "buy", "1", "40.20", E, cdr="ZNVD", signal_id=signal.id)
    assert ledger.current_stop(signal.id).us == Decimal(47)  # 188 / 4, with no split row
    assert session.exec(select(StopUpdateRow)).all() == []


def test_a_cdr_split_multiplies_units_rescales_the_cdr_stop_and_keeps_r(
    ledger: Ledger, session: Session
) -> None:
    signal = _open(ledger)
    assert signal.id is not None
    _split(ledger, "cdr_split", "ZNVD", "2")
    assert ledger.books()["ZNVD"].units == Decimal(4)
    assert ledger.current_stop(signal.id) == StopLevels(Decimal(188), Decimal("18.80"))
    assert ledger.signal_levels(signal.id).cdr_signal_close == Decimal(20)
    _fill(ledger, "sell", "4", "21.50", date(2026, 6, 9), cdr="ZNVD")  # post-split units
    [trade] = ledger.closed_trades()
    assert (trade.pnl, trade.r) == (Decimal("5.60"), Decimal("5.60") / Decimal("4.80"))


def test_a_cdr_split_withdraws_a_sent_signal_from_before_its_ex_date(ledger: Ledger) -> None:
    signal = send_signal(ledger, date(2026, 6, 5))
    assert signal.id is not None
    _split(ledger, "cdr_split", "ZNVD", "2")  # allowed: a sent signal, no position
    assert ledger.signal(signal.id).status == "withdrawn"


def _dividend(session: Session, symbol_id: object, day: date, adj: str) -> None:
    [row] = session.exec(select(Price).where(Price.ticker_id == symbol_id, Price.date == day)).all()
    row.adj_close = Decimal(adj)
    session.add(row)
    session.commit()


def test_the_scale_check_holds_an_unrecorded_split_and_passes_dividends(session: Session) -> None:
    stock, receipt = add_pair(session)
    add_prices(session, stock, [D, E], [200, 201])
    add_prices(session, receipt, [D, E], [40, 40.2])
    ledger = make_ledger(session)
    signal = send_signal(ledger, D)
    assert signal.id is not None
    assert ledger.scale_check(signal.id) is None
    _dividend(session, stock.id, D, "198.50")  # a later dividend lowers the adjusted close only
    assert ledger.scale_check(signal.id) is None
    for row in session.exec(select(Price).where(Price.ticker_id == stock.id)).all():
        row.close = row.close / 2  # yfinance rescaled the history for a split we have not recorded
        session.add(row)
    session.commit()
    problem = ledger.scale_check(signal.id)
    assert problem is not None and "the stored US close 100.0000 on 2026-06-01" in problem
    ledger.record_split(kind="us_split", symbol="NVDA", ex_date=E, ratio=Decimal(2),
                        source="yfinance")
    assert ledger.scale_check(signal.id) is None
