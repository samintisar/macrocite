"""Spec 05, step 9 and Messages: every unsent row goes out once, in order, rendered from the
ledger; and the texts built from the ledger (the summary, the pause review, /portfolio, /pnl)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from live_helpers import add_pair, add_prices, make_ledger, send_signal
from paper_helpers import evening
from signalbench.db.models import ExitAlert, StopUpdateRow, TradeSignal
from signalbench.live.ledger import Ledger
from signalbench.live.messenger import Buttons, FakeMessenger
from signalbench.live.outbox import send_unsent
from signalbench.live.summary import (
    BacktestR,
    Tonight,
    evening_summary,
    pause_review,
    pnl_text,
    portfolio_text,
)
from strategy_helpers import WeekdaySessions, weekdays

DAYS = weekdays(date(2026, 6, 1), 8)  # Monday 2026-06-01 to Wednesday 2026-06-10
D0, D1, D2, D3, D4, D5 = DAYS[:6]
US = [200, 201, 204, 190, 190, 190, 190, 190]  # 190 on D3 is below the stop raised at D2
CALENDAR = WeekdaySessions()
BACKTEST = BacktestR(mean_r=Decimal("0.505"), version="v2-none-cash",
                     run_id="4123c177-1ee5-4366-9f31-993a80329e0e")


@pytest.fixture
def ledger(session: Session) -> Ledger:
    nvda, znvd = add_pair(session)
    add_prices(session, nvda, DAYS, US)
    add_prices(session, znvd, DAYS, [close / 5 for close in US], volume=100)  # ratio 0.2
    xom, zxom = add_pair(session, "XOM", "ZXOM", "Energy")
    add_prices(session, xom, DAYS, [50] * 8)
    add_prices(session, zxom, DAYS, [10] * 8, volume=100)
    ledger = make_ledger(session, today=DAYS[-1])
    ledger.record_cash(Decimal("100.00"), D0, "deposit")
    return ledger


def _trade(ledger: Ledger) -> tuple[int, int]:
    """Signal on D0 (stop 188, CDR 40 / 37.60), 2 units bought on D1, a raise to 192 at D2's
    close, and the stop exit at D3 (190 <= 192). Returns the signal and exit alert ids."""
    signal = send_signal(ledger, D0)
    assert signal.id is not None
    ledger.record_fill(cdr_symbol="ZNVD", side="buy", quantity=Decimal(2),
                       price_cad=Decimal("40.20"), trade_date=D1, signal_id=signal.id)
    for day in (D1, D2):
        ledger.record_equity(day, pause_drawdown=Decimal("0.15"))
    ledger.record_stop_update(signal_id=signal.id, session=D2, new_us_stop=Decimal(192))
    alert = ledger.record_exit_alert(signal_id=signal.id, as_of=D3, reason="stop")
    assert alert.id is not None
    return signal.id, alert.id


def _send(session: Session, ledger: Ledger, messenger: FakeMessenger, day: date) -> int:
    return send_unsent(session, ledger, messenger, nyse=CALENDAR, cboe=CALENDAR,
                       now=evening(day))


def test_unsent_rows_go_out_once_in_order_with_their_buttons(
    ledger: Ledger, session: Session
) -> None:
    signal_id, alert_id = _trade(ledger)
    messenger = FakeMessenger()
    assert _send(session, ledger, messenger, D0) == 3
    entry, exit_, raised = messenger.sent
    assert entry.text == (
        "🟢 BUY NVDA (CDR ZNVD) — Breakout · NVDA\n"
        "Signal C$40.00 · Stop C$37.60 (−6.0%) · Trailing stop, no target, no time limit\n"
        "Size 1 unit (~C$40.00) · risk C$2.40 · LIMIT C$40.40\n"
        "Skip if price > C$40.40, price ≤ the stop C$37.60, or bid/ask spread > 0.5%\n"
        "Why: Closed at a 20-session high."
    )
    assert [b.data for row in entry.buttons for b in row] == [f"b:{signal_id}", f"s:{signal_id}"]
    assert exit_.text == (
        "🔴 SELL NVDA (CDR ZNVD) — stop hit (US close US$190.00 ≤ stop US$192.00 · CDR ~C$38.00 "
        "vs stop C$38.40)\n"
        "Held 3 sessions · unrealized −C$4.40 (−5.5%)\n"
        "Sell at the open."
    )
    assert [b.data for row in exit_.buttons for b in row] == [f"x:{alert_id}", f"i:{alert_id}"]
    assert raised.text == (
        "⬆️ NVDA (CDR ZNVD) stop raised C$37.60 → C$38.40 (US$188.00 → US$192.00) · still "
        "holding, no action"
    )
    signal, alert = session.get(TradeSignal, signal_id), session.get(ExitAlert, alert_id)
    assert signal is not None and alert is not None
    assert (signal.telegram_message_id, alert.telegram_message_id) == (
        entry.message_id, exit_.message_id
    )
    assert _send(session, ledger, messenger, D0) == 0  # nothing is sent twice


def test_a_failed_send_leaves_the_rest_for_the_next_scan(ledger: Ledger, session: Session) -> None:
    _trade(ledger)

    class Flaky(FakeMessenger):
        def send(self, text: str, buttons: Buttons = ()) -> int:
            if len(self.sent) == 1:
                raise ConnectionError("Telegram is unreachable")
            return super().send(text, buttons)

    flaky = Flaky()
    with pytest.raises(ConnectionError):
        _send(session, ledger, flaky, D0)
    later = FakeMessenger()
    assert _send(session, ledger, later, D0) == 2  # the exit alert and the raise
    assert [m.text.split()[0] for m in later.sent] == ["🔴", "⬆️"]


def test_an_entry_sent_after_its_expiry_says_not_to_place_it(
    ledger: Ledger, session: Session
) -> None:
    send_signal(ledger, D0)  # expires at 16:00 New York on D1
    messenger = FakeMessenger()
    _send(session, ledger, messenger, D1)
    [late] = messenger.sent
    assert late.buttons == ()
    assert late.text == (
        "ℹ️ The NVDA (CDR ZNVD) entry of 2026-06-01 was not sent in time and expired at "
        "2026-06-02 16:00 New York time: do not place it."
    )


def test_an_unsent_entry_withdrawn_by_a_split_is_not_offered(
    ledger: Ledger, session: Session
) -> None:
    send_signal(ledger, D0)
    ledger.record_split(kind="cdr_split", symbol="ZNVD", ex_date=D1, ratio=Decimal(2),
                        source="owner")
    messenger = FakeMessenger()
    _send(session, ledger, messenger, D0)
    assert messenger.sent[-1].buttons == ()
    assert messenger.texts()[-1] == (
        "ℹ️ The NVDA (CDR ZNVD) entry of 2026-06-01 was not sent in time and is withdrawn: do not "
        "place it."
    )


def test_split_notices_for_a_us_split_and_a_cdr_split(ledger: Ledger, session: Session) -> None:
    signal_id, _ = _trade(ledger)
    ledger.record_fill(cdr_symbol="ZXOM", side="buy", quantity=Decimal(1),
                       price_cad=Decimal(10), trade_date=D1)
    ledger.record_split(kind="us_split", symbol="NVDA", ex_date=D4, ratio=Decimal(2),
                        source="yfinance")
    ledger.record_split(kind="cdr_split", symbol="ZXOM", ex_date=D5, ratio=Decimal(2),
                        source="yfinance")
    messenger = FakeMessenger()
    _send(session, ledger, messenger, D0)
    us, cdr = messenger.texts()[:2]
    assert us == (
        "ℹ️ NVDA split 2-for-1 (ex-date 2026-06-05): stop US$192.00 → US$96.00 · no action"
    )
    assert cdr == (
        "ℹ️ ZXOM split 2-for-1 (ex-date 2026-06-08): 1 unit → 2, ACB per unit C$10.00 → C$5.00 "
        "(the total ACB is unchanged) · check that Wealthsimple shows 2 units · no action"
    )
    [row] = session.exec(select(StopUpdateRow).where(StopUpdateRow.reason == "split")).all()
    assert (row.signal_id, row.telegram_message_id) == (signal_id, messenger.sent[0].message_id)


def test_the_portfolio_and_the_evening_summary(ledger: Ledger, session: Session) -> None:
    _trade(ledger)
    ledger.record_fill(cdr_symbol="ZXOM", side="buy", quantity=Decimal(1),
                       price_cad=Decimal(10), trade_date=D2)
    position = (
        "• ZNVD 2 units · ACB C$40.20/unit · last C$38.00 · −5.5% · stop C$38.40 / US$192.00 · "
        "3 sessions"
    )
    manual = "• ZXOM 1 unit · ACB C$10.00/unit · last C$10.00 · 0.0% · manual (no stop) · 2 sessions"
    money = (
        "Cash C$9.60 · equity C$95.60 · peak C$101.20 · drawdown −5.5% · new entries not paused"
    )
    assert portfolio_text(ledger, D3).splitlines() == [
        "💼 Portfolio at the close of 2026-06-04", position, manual, money
    ]
    tonight = Tonight(
        as_of=D3, regime="QQQ above its 200-session average: new entries allowed", signals=0,
        skips={"held": 1, "no_cdr_price": 1}, exits=1, raises=0, caught_up=(D2,),
        warnings=("earnings calendar FAILED (ConnectError: down); used the stored dates",),
        scale_up=None,
    )
    assert evening_summary(ledger, session, tonight).splitlines() == [
        "📊 Evening summary · 2026-06-04 (Thu)",
        "QQQ above its 200-session average: new entries allowed",
        "Signals sent 0 · skipped: held 1, no_cdr_price 1 · exits 1 · stop raises 0",
        "Caught up (exits and stop raises only, sent marked late): 2026-06-03",
        "Positions:", position, manual,
        "Open exit alerts:",
        "• ZNVD stop exit since 2026-06-04: sell, or tap Ignore",
        money,
        "⚠️ earnings calendar FAILED (ConnectError: down); used the stored dates",
    ]


def test_pnl_and_the_pause_review_compare_live_r_with_the_backtest(
    ledger: Ledger, session: Session
) -> None:
    _, alert_id = _trade(ledger)
    ledger.record_fill(cdr_symbol="ZNVD", side="sell", quantity=Decimal(2),
                       price_cad=Decimal("38.10"), trade_date=D4, exit_alert_id=alert_id)
    ledger.record_cash(Decimal("-10.00"), D4, "withdrawal")
    assert pnl_text(ledger, D5, BACKTEST).splitlines() == [
        "💰 P&L to 2026-06-08",
        "Realized: all time −C$4.20 · June 2026 −C$4.20",
        "Unrealized: C$0.00 on 0 open positions",
        "Closed managed trades: 1 · win rate 0%",
        (
            "Live mean R −0.88 over 1 closed managed trades vs the backtest's 0.505 "
            "(v2-none-cash, run 4123c177), both R on planned risk"
        ),
    ]
    risk = ledger.risk_state()
    risk.paused, risk.paused_at = True, D4
    risk.paused_reason = "equity C$85.99 is below 0.85 x the peak C$101.20"
    session.add(risk)
    session.commit()
    assert pause_review(ledger, session, None).splitlines() == [
        (
            "⏸ New entries are paused since the close of 2026-06-05: equity C$85.99 is below "
            "0.85 x the peak C$101.20."
        ),
        "Last 10 managed trades (R on planned risk):",
        "• ZNVD 2026-06-02 → 2026-06-05 · −C$4.20 · −0.88R",
        (
            "Live mean R −0.88 over 1 closed managed trades (no stored backtest run of the live "
            "config to compare)"
        ),
        "Your skips: none · missed exit alerts: 0",
        (
            "Withdrawals since the peak on 2026-06-03: C$10.00. A withdrawal lowers equity like "
            "a loss (spec 04), so part of this drawdown is your own cash."
        ),
        "/resume to continue (resets peak)",
    ]
