"""Spec 05, Testing: the end-to-end fixture test and the catch-up case.

A Breakout fires, the owner taps ✅ and replies, a new high raises the trailing stop, the close
falls to the raised stop, and ✅ Sold closes the position with its P&L, ACB, and R on planned
risk. Then two missed evenings: exits and raises are caught up in order and sent marked late,
and only the target's entries are sent. FakeMessenger and stored prices: no network.
"""

from decimal import Decimal
from pathlib import Path

from sqlmodel import Session, col, select

from scan_helpers import DAYS, NVDA, OWNER, B, bot_brain, make_world
from signalbench.db.models import ExitAlert, Fill, StopUpdateRow, TradeSignal

WHY = (
    "Why: Closed at a 20-session high (US$101.00) on 2.0× its 50-session average volume, above "
    "its 50-session average."
)


def test_breakout_fill_raise_stop_hit_and_sale(session: Session, tmp_path: Path) -> None:
    world = make_world(session, tmp_path)

    # D: the Breakout fires. The entry says how the position is managed, and why.
    world.scan(B)
    entry = world.messenger.sent[0]
    assert "Trailing stop, no target, no time limit" in entry.text
    assert entry.text.splitlines()[-1] == WHY
    signal = session.exec(select(TradeSignal)).one()
    assert (signal.as_of, signal.us_symbol, signal.status) == (DAYS[B], "NVDA", "sent")

    # D+1: ✅ I bought, then "3 10.45": a fill linked to the signal.
    bot = bot_brain(world, B + 1)
    bot.handle_callback(OWNER, f"b:{signal.id}", entry.message_id, entry.text)
    bot.handle_text(OWNER, "3 10.45")
    fill = session.exec(select(Fill)).one()
    assert (fill.signal_id, fill.side, fill.quantity, fill.price_cad) == (
        signal.id, "buy", Decimal(3), Decimal("10.45")
    )

    # D+1 and D+2: 102 - 6 and 103 - 6 are not above the initial stop of 97.
    world.scan(B + 1)
    world.scan(B + 2)
    assert session.exec(select(StopUpdateRow)).all() == []

    # D+3: the new high of 104 raises the stop to 104 - 3 x 2 = 98, shown in both currencies.
    before = len(world.messenger.sent)
    world.scan(B + 3)
    [raised] = [m.text for m in world.messenger.sent[before:] if m.text.startswith("⬆️")]
    assert raised == (
        "⬆️ NVDA (CDR ZNVD) stop raised C$9.70 → C$9.80 (US$97.00 → US$98.00) · still holding, "
        "no action"
    )
    [row] = session.exec(select(StopUpdateRow)).all()
    assert (row.reason, row.session, row.old_us_stop, row.new_us_stop, row.late) == (
        "trail", DAYS[B + 3], Decimal(97), Decimal(98), False
    )

    # D+4: the close of 97.50 is at or below the raised stop: an exit alert.
    before = len(world.messenger.sent)
    world.scan(B + 4)
    alert_message = next(m for m in world.messenger.sent[before:] if m.text.startswith("🔴"))
    assert alert_message.text == (
        "🔴 SELL NVDA (CDR ZNVD) — stop hit (US close US$97.50 ≤ stop US$98.00 · CDR ~C$9.75 vs "
        "stop C$9.80)\n"
        "Held 4 sessions · unrealized −C$2.10 (−6.7%)\n"
        "Sell at the open."
    )
    alert = session.exec(select(ExitAlert)).one()
    assert (alert.as_of, alert.reason, alert.late) == (DAYS[B + 4], "stop", False)

    # D+5, not sold yet: one open exit alert per position, so no new alert and no raise, and
    # the summary repeats the open alert.
    before = len(world.messenger.sent)
    world.scan(B + 5)
    [summary] = world.messenger.sent[before:]
    assert "• ZNVD stop exit since 2026-10-09: sell, or tap Ignore" in summary.text.splitlines()
    assert len(session.exec(select(ExitAlert)).all()) == 1
    assert len(session.exec(select(StopUpdateRow)).all()) == 1

    # D+6: ✅ Sold, "9.70": the position closes with the right P&L, ACB, and R.
    bot = bot_brain(world, B + 6)
    bot.handle_callback(OWNER, f"x:{alert.id}", alert_message.message_id, alert_message.text)
    [check] = bot.handle_text(OWNER, "9.70")
    assert check.text == "Sell ALL 3 units ZNVD @ C$9.70 on 2026-10-13?"
    [sold, _, _] = bot.handle_callback(OWNER, "ok:1", 99, check.text)
    assert sold.text == (
        "✅ Sold 3 units ZNVD @ C$9.70 on 2026-10-13 (fill 2). Position closed: P&L −C$2.25 · "
        "−1.88R on planned risk."
    )
    ledger = world.ledger(B + 6)
    [trade] = ledger.closed_trades()
    assert (trade.pnl, trade.planned_risk, trade.r) == (
        Decimal("-2.25"), Decimal("1.20"), Decimal("-1.875")
    )  # 29.10 - 31.35; 3 x (10.10 - 9.70)
    [sale] = ledger.books()["ZNVD"].dispositions
    assert (sale.proceeds, sale.acb, sale.gain) == (
        Decimal("29.10"), Decimal("31.35"), Decimal("-2.25")
    )
    session.refresh(alert)
    assert alert.status == "done"

    # The next scan runs the scale-up check after the close of a managed position.
    world.scan(B + 6)
    assert world.messenger.sent[-1].text.splitlines()[-1] == (
        "Scale-up check: 1 of 10 managed trades closed."
    )


def test_catch_up_after_two_missed_evenings(session: Session, tmp_path: Path) -> None:
    world = make_world(
        session, tmp_path,
        closes={"NVDA": NVDA, "XOM": {B + 3: 51.0}, "AAPL": {B + 5: 101.0}},
        spikes={"NVDA": {B: 2_000_000}, "XOM": {B + 3: 2_000_000}, "AAPL": {B + 5: 2_000_000}},
    )
    world.scan(B)
    [entry, _] = world.messenger.sent
    bot = bot_brain(world, B + 1)
    bot.handle_callback(OWNER, "b:1", entry.message_id, entry.text)
    bot.handle_text(OWNER, "3 10.45")
    world.scan(B + 1)
    world.scan(B + 2)

    # The PC was off on D+3 and D+4. The run on D+5 catches them up first, in order.
    before = len(world.messenger.sent)
    outcome = world.scan(B + 5)
    assert outcome.counts == {"exits": 1, "raises": 1, "signals": 1, "sent": 4}
    stop_exit, raised, aapl, summary = (m.text for m in world.messenger.sent[before:])
    # Entries come only from the target: AAPL on D+5, not XOM's stale breakout of D+3.
    assert aapl.startswith("🟢 BUY AAPL (CDR ZAAP) — Breakout")
    signals = session.exec(select(TradeSignal).order_by(col(TradeSignal.id))).all()
    assert [(s.as_of, s.us_symbol) for s in signals] == [(DAYS[B], "NVDA"), (DAYS[B + 5], "AAPL")]
    # D+3's new high raised the stop to 98, and that stop was in force for D+4's 97.50.
    assert raised == (
        "⬆️ NVDA (CDR ZNVD) stop raised C$9.70 → C$9.80 (US$97.00 → US$98.00) · still holding, "
        "no action (late — for 2026-10-08)"
    )
    assert stop_exit.splitlines()[0] == (
        "🔴 SELL NVDA (CDR ZNVD) — stop hit (US close US$97.50 ≤ stop US$98.00 · CDR ~C$9.75 vs "
        "stop C$9.80) (late — should have been sent after 2026-10-09)"
    )
    assert world.ledger(B + 5).stop_in_force(1, DAYS[B + 4]).us == Decimal(98)
    alert = session.exec(select(ExitAlert)).one()
    assert (alert.as_of, alert.late) == (DAYS[B + 4], True)
    assert (
        "Caught up (exits and stop raises only, sent marked late): 2026-10-08, 2026-10-09"
    ) in summary.splitlines()
