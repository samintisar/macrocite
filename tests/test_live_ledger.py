"""Spec 04: fills, cash, and voids in the ledger, on the hand-checked examples and the rules."""

import logging
from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from live_helpers import add_pair, load_example, make_ledger, record_example
from signalbench.db.models import Fill
from signalbench.live.ledger import Ledger, LedgerError

DAY = date(2026, 5, 4)


@pytest.fixture
def ledger(session: Session) -> Ledger:
    add_pair(session, "TST", "ZTST")
    add_pair(session, "OTH", "ZOTH")
    return make_ledger(session)


def _buy(ledger: Ledger, quantity: str, price: str, day: date = DAY, **extra: object) -> Fill:
    return ledger.record_fill(cdr_symbol="ZTST", side="buy", quantity=Decimal(quantity),
                              price_cad=Decimal(price), trade_date=day, **extra)


def _sell(ledger: Ledger, quantity: str, price: str, day: date = DAY) -> Fill:
    return ledger.record_fill(cdr_symbol="ZTST", side="sell", quantity=Decimal(quantity),
                              price_cad=Decimal(price), trade_date=day)


@pytest.mark.parametrize("name", ["acb", "acb_rebuy", "fractional"])
def test_the_hand_checked_examples_through_the_database(ledger: Ledger, name: str) -> None:
    example = load_example(name)
    record_example(ledger, example)
    book = ledger.books()["ZTST"]
    assert [(step.units, step.acb) for step in book.steps] == [
        (Decimal(e["units"]), Decimal(e["acb"])) for e in example["events"]
    ]
    assert [(d.gain, d.denied) for d in book.dispositions] == [
        (Decimal(d["gain"]), Decimal(d["denied"])) for d in example["dispositions"]
    ]
    assert ledger.cash() == Decimal(example["cash"])


def test_cash_counts_movements_and_fills_through_a_date(ledger: Ledger) -> None:
    ledger.record_cash(Decimal("100.00"), DAY, "deposit")
    _buy(ledger, "2", "30.00")
    _sell(ledger, "1", "31.50", date(2026, 5, 6))
    ledger.record_cash(Decimal("-20.00"), date(2026, 5, 7), "withdrawal")
    assert ledger.cash(DAY) == Decimal("40.00")
    assert ledger.cash(date(2026, 5, 6)) == Decimal("71.50")
    assert ledger.cash() == Decimal("51.50")


def test_a_void_reverses_cash_and_units_and_keeps_the_row(ledger: Ledger, session: Session) -> None:
    ledger.record_cash(Decimal("100.00"), DAY, "deposit")
    fill = _buy(ledger, "2", "30.00")
    assert fill.id is not None
    ledger.void_fill(fill.id, "typed the wrong price")
    assert ledger.cash() == Decimal("100.00")
    assert "ZTST" not in ledger.books()
    [row] = session.exec(select(Fill)).all()
    assert (row.voided, row.void_reason) == (True, "typed the wrong price")
    with pytest.raises(LedgerError, match=f"no fill {fill.id} to void"):
        ledger.void_fill(fill.id, "again")


def test_a_void_that_would_leave_an_oversell_is_refused(ledger: Ledger) -> None:
    ledger.record_cash(Decimal("100.00"), DAY, "deposit")
    buy = _buy(ledger, "2", "30.00")
    _sell(ledger, "2", "31.00", date(2026, 5, 5))
    assert buy.id is not None
    with pytest.raises(LedgerError, match="record the corrected fill first"):
        ledger.void_fill(buy.id, "wrong quantity")
    _buy(ledger, "2", "30.00", force=True)  # the corrected buy first (cash is short until the void)
    ledger.void_fill(buy.id, "wrong quantity")
    assert ledger.books()["ZTST"].units == 0


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"quantity": Decimal(0)}, "quantity must be above 0"),
        ({"quantity": Decimal("0.0000001")}, "quantity 1E-7 has more than 6 decimals"),
        ({"price_cad": Decimal(-1)}, "price must be above 0"),
        ({"price_cad": Decimal("30.00001")}, "price 30.00001 has more than 4 decimals"),
        ({"fee_cad": Decimal("-0.01")}, "fee cannot be negative"),
        ({"trade_date": date(2027, 1, 1)}, "trade date 2027-01-01 is after today"),
        ({"trade_date": date(2026, 5, 1)}, "before the first cash movement"),
        ({"cdr_symbol": "ZZZZ"}, "ZZZZ is not a known CDR"),
        ({"side": "short"}, "side must be one of buy, sell"),
    ],
)
def test_a_bad_fill_is_refused(ledger: Ledger, changes: dict[str, object], message: str) -> None:
    ledger.record_cash(Decimal("100.00"), DAY, "deposit")
    fill: dict[str, object] = {
        "cdr_symbol": "ZTST", "side": "buy", "quantity": Decimal(1), "price_cad": Decimal(30),
        "trade_date": DAY,
    }
    with pytest.raises(LedgerError, match=message):
        ledger.record_fill(**{**fill, **changes})


def test_a_fill_needs_a_deposit_first(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="record the first deposit before any fill"):
        _buy(ledger, "1", "30.00")


def test_a_sale_of_more_than_is_held_is_refused(ledger: Ledger, session: Session) -> None:
    ledger.record_cash(Decimal("100.00"), DAY, "deposit")
    _buy(ledger, "2", "30.00")
    with pytest.raises(LedgerError, match="ZTST: a sale of 3 on 2026-05-04 is more than the 2"):
        _sell(ledger, "3", "31.00")
    with pytest.raises(LedgerError, match="more than the 0 units held"):
        ledger.record_fill(cdr_symbol="ZOTH", side="sell", quantity=Decimal(1),
                           price_cad=Decimal(10), trade_date=DAY)
    assert len(session.exec(select(Fill)).all()) == 1  # nothing half-written


def test_a_buy_beyond_the_cash_is_refused_unless_forced_and_then_logged(
    ledger: Ledger, caplog: pytest.LogCaptureFixture
) -> None:
    ledger.record_cash(Decimal("50.00"), DAY, "deposit")
    with pytest.raises(LedgerError, match="takes cash to C\\$-10.00; there is no margin"):
        _buy(ledger, "2", "30.00")
    with caplog.at_level(logging.WARNING, logger="signalbench.live.book"):
        fill = _buy(ledger, "2", "30.00", force=True)
    assert fill.forced
    assert "forced buy: ZTST 2 x 30.00 on 2026-05-04 takes cash to C$-10.00" in caplog.text
    assert ledger.cash() == Decimal("-10.00")


def test_a_deposit_on_the_trade_date_pays_for_that_day_s_buy(ledger: Ledger) -> None:
    ledger.record_cash(Decimal("10.00"), DAY, "deposit")
    first = _buy(ledger, "1", "10.00")
    ledger.record_cash(Decimal("50.00"), date(2026, 5, 5), "deposit")
    second = _buy(ledger, "1", "50.00", date(2026, 5, 5))
    assert (first.forced, second.forced, ledger.cash()) == (False, False, Decimal(0))


def test_a_later_withdrawal_that_the_buy_would_overdraw_refuses_the_buy(ledger: Ledger) -> None:
    ledger.record_cash(Decimal("100.00"), DAY, "deposit")
    ledger.record_cash(Decimal("-60.00"), date(2026, 5, 6), "withdrawal")
    with pytest.raises(LedgerError, match="takes cash to C\\$-20.00"):
        _buy(ledger, "2", "30.00", date(2026, 5, 5))


@pytest.mark.parametrize(
    ("amount", "day", "message"),
    [
        (Decimal(0), DAY, "cannot be 0"),
        (Decimal("10.001"), DAY, "amount 10.001 has more than 2 decimals"),
        (Decimal(10), date(2027, 1, 1), "after today"),
    ],
)
def test_a_bad_cash_movement_is_refused(
    ledger: Ledger, amount: Decimal, day: date, message: str
) -> None:
    with pytest.raises(LedgerError, match=message):
        ledger.record_cash(amount, day, "note")
