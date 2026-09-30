"""Spec 04: the scale-up check, each criterion at its threshold, and on the ledger."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlmodel import Session

from live_helpers import add_pair, make_ledger, send_signal
from signalbench.live.ledger import Ledger
from signalbench.live.scaleup import (
    AlertOutcome,
    ScaleUpResult,
    SignalBuy,
    is_miss,
    scale_up,
)
from strategy_helpers import weekdays

DAYS = weekdays(date(2026, 6, 1), 60)
MON, TUE, WED = date(2026, 6, 8), date(2026, 6, 9), date(2026, 6, 10)


def _result(**changes: object) -> ScaleUpResult:
    inputs: dict[str, object] = {
        "closed_managed": 10, "statuses": [("taken", None)] * 10,
        "buys": [SignalBuy(1, Decimal("40.00"), Decimal(40))], "alerts": [], "today": WED,
    }
    return scale_up(**{**inputs, **changes})


@pytest.mark.parametrize(
    ("statuses", "rate", "ok"),
    [
        # 8 / (8 + 1 expired + 1 disagree) = 0.80; price_moved, wide_spread, withdrawn, and
        # still-sent signals are left out.
        ([("taken", None)] * 8 + [("expired", None), ("skipped", "disagree"),
          ("skipped", "price_moved"), ("skipped", "wide_spread"), ("withdrawn", None),
          ("sent", None)], Decimal("0.8"), True),
        ([("taken", None)] * 7 + [("skipped", "no_time"), ("skipped", "other")],
         Decimal(7) / 9, False),
    ],
)
def test_the_take_rate_at_its_threshold(
    statuses: list[tuple[str, str | None]], rate: Decimal, ok: bool
) -> None:
    result = _result(statuses=statuses)
    assert (result.take_rate, result.checks["take_rate"]) == (rate, ok)


@pytest.mark.parametrize(
    ("price", "slippage_ok", "violations"),
    [("40.20", True, ()), ("40.24", False, ()), ("40.40", False, ()), ("40.41", False, (7,))],
)
def test_slippage_and_the_1_percent_limit_at_their_thresholds(
    price: str, slippage_ok: bool, violations: tuple[int, ...]
) -> None:
    result = _result(buys=[SignalBuy(7, Decimal(price), Decimal(40))])
    assert result.checks["slippage"] == slippage_ok
    assert result.violations == violations


def test_an_exit_alert_misses_when_ignored_or_its_window_passes_without_a_sale() -> None:
    def alert(status: str, sold_on: date | None) -> AlertOutcome:
        return AlertOutcome(1, status, TUE, sold_on)  # the alert was for MON: sell by TUE

    assert not is_miss(alert("done", TUE), WED)
    assert is_miss(alert("done", WED), WED)
    assert not is_miss(alert("sent", None), TUE)  # still inside its window
    assert is_miss(alert("sent", None), WED)
    assert is_miss(alert("ignored", TUE), WED)


def test_fewer_than_10_closed_managed_positions_is_not_active() -> None:
    result = _result(closed_managed=9)
    assert (result.active, all(result.checks.values()), result.passed) == (False, True, False)


def _round_trip(ledger: Ledger, index: int, buy: str = "40.00") -> int:
    signal = send_signal(ledger, DAYS[index])
    assert signal.id is not None
    ledger.record_fill(cdr_symbol="ZNVD", side="buy", quantity=Decimal(1),
                       price_cad=Decimal(buy), trade_date=DAYS[index + 1], signal_id=signal.id)
    return signal.id


def test_ten_closed_trades_that_followed_the_signals_pass_and_are_recorded(
    session: Session,
) -> None:
    add_pair(session)
    ledger = make_ledger(session, today=DAYS[-1])
    ledger.record_cash(Decimal("1000.00"), DAYS[0], "deposit")
    for n in range(10):
        signal_id = _round_trip(ledger, 3 * n)
        alert = ledger.record_exit_alert(signal_id=signal_id, as_of=DAYS[3 * n + 1],
                                         reason="stop", late=n == 9)
        if n == 9:  # sent late, on 2026-07-14 for 07-09: the window counts from the send
            alert.created_at = datetime(2026, 7, 14, 22, 0, tzinfo=UTC)  # 18:00 New York
            session.add(alert)
            session.commit()
        sold = DAYS[3 * n + 2] if n < 9 else date(2026, 7, 15)
        ledger.record_fill(cdr_symbol="ZNVD", side="sell", quantity=Decimal(1),
                           price_cad=Decimal("39.00"), trade_date=sold, exit_alert_id=alert.id)
    result = ledger.scale_up_check()
    assert (result.closed_managed, result.passed, result.misses) == (10, True, ())
    ledger.record_scale_up(result)
    [record] = ledger.risk_state().scale_up_history
    assert (record["passed"], record["take_rate"], record["closed_managed"]) == (True, "1", 10)
