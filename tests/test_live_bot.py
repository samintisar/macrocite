"""Spec 05, Commands and the buttons: the bot's brain on the Breakout world, without Telegram.
Every command's parsing and error replies, the allowlist, and each button."""

import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, select

from scan_helpers import DAYS, OWNER, B, World, bot_brain, make_world
from signalbench.db.models import ExitAlert, Fill, TradeSignal
from signalbench.live.bot import HELP, Reply
from signalbench.live.ledger import Ledger
from signalbench.live.messenger import Sent
from strategy_helpers import WeekdaySessions


@pytest.fixture
def world(session: Session, tmp_path: Path) -> World:
    world = make_world(session, tmp_path)
    world.scan(B)  # the NVDA entry: 3 ZNVD at C$10.10, stop C$9.70
    return world


def texts(replies: list[Reply]) -> list[str]:
    return [reply.text for reply in replies]


def entry(world: World) -> Sent:
    return world.messenger.sent[0]


def test_updates_from_any_other_chat_are_ignored_and_logged(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    bot = bot_brain(world)
    with caplog.at_level(logging.WARNING, logger="signalbench.live.bot"):
        assert bot.handle_text(999, "/deposit 100") == []
        assert bot.handle_callback(999, "b:1", entry(world).message_id, entry(world).text) == []
    assert caplog.messages == ["ignored an update from chat 999 (not TELEGRAM_CHAT_ID)"] * 2
    assert bot_brain(world).handle_text(OWNER, "/portfolio")[0].text.startswith("💼")


def test_help_and_unknown_input(world: World) -> None:
    bot = bot_brain(world)
    assert texts(bot.handle_text(OWNER, "/help")) == [HELP]
    assert texts(bot.handle_text(OWNER, "/start@signalbench_bot")) == [HELP]
    assert texts(bot.handle_text(OWNER, "/frobnicate")) == [
        "⚠️ unknown command /frobnicate. Send /help for the commands."
    ]
    assert texts(bot.handle_text(OWNER, "hello")) == ["Send /help for the commands."]
    for command in ("/signals", "/portfolio", "/pnl", "/buy", "/sell", "/void", "/deposit",
                    "/withdraw", "/resume", "/status", "/tax", "/help"):
        assert command in HELP


def test_i_bought_asks_for_units_and_price_then_records_the_linked_fill(world: World) -> None:
    bot = bot_brain(world)
    sent = entry(world)
    [ask] = bot.handle_callback(OWNER, "b:1", sent.message_id, sent.text)
    assert ask.text == (
        "How many units of ZNVD did you buy, and at what price? Reply like `3 10.1` (a third "
        "token sets the date, YYYY-MM-DD)."
    )
    assert [(b.text, b.data) for row in ask.buttons for b in row] == [
        ("Use suggested: 3 units @ C$10.10", "u:1")
    ]
    assert texts(bot.handle_text(OWNER, "3 ten")) == ["⚠️ the price must be a number, not 'ten'"]
    confirm, edit = bot.handle_text(OWNER, "3 10.45")
    assert confirm.text == (
        "✅ Bought 3 units ZNVD @ C$10.45 on 2026-10-06 (fill 1), for signal 1. Stop C$9.70 / "
        "US$97.00; the evening scan manages it from here."
    )
    assert (edit.edit, edit.buttons) == (sent.message_id, ())
    assert edit.text == f"{sent.text}\n✅ Bought 3 units @ C$10.45 (fill 1)"
    [fill] = world.session.exec(select(Fill)).all()
    assert (fill.signal_id, fill.quantity, fill.price_cad, fill.trade_date) == (
        1, Decimal(3), Decimal("10.45"), DAYS[B + 1]
    )
    assert bot.awaiting is None
    assert texts(bot.handle_callback(OWNER, "b:1", sent.message_id, sent.text)) == [
        "Already logged: signal 1 is taken."
    ]


def test_use_suggested_records_the_suggested_size_at_the_signal_close(world: World) -> None:
    sent = entry(world)
    replies = bot_brain(world).handle_callback(OWNER, "u:1", sent.message_id, sent.text)
    assert replies[0].text.startswith("✅ Bought 3 units ZNVD @ C$10.10 on 2026-10-06 (fill 1)")


def test_skip_asks_for_a_reason_and_records_it(world: World) -> None:
    bot = bot_brain(world)
    sent = entry(world)
    [reasons] = bot.handle_callback(OWNER, "s:1", sent.message_id, sent.text)
    assert (reasons.edit, reasons.text) == (sent.message_id, sent.text)
    assert reasons.buttons[0][0].data == "k:1:disagree"
    [skipped] = bot.handle_callback(OWNER, "k:1:wide_spread", sent.message_id, sent.text)
    assert skipped.edit == sent.message_id
    assert skipped.text == f"{sent.text}\n⏭ Skipped (wide spread)"
    signal = world.session.exec(select(TradeSignal)).one()
    world.session.refresh(signal)
    assert (signal.status, signal.skip_reason) == ("skipped", "wide_spread")
    assert texts(bot.handle_callback(OWNER, "k:1:other", sent.message_id, sent.text)) == [
        "Already logged: signal 1 is skipped."
    ]
    assert texts(bot.handle_callback(OWNER, "zz:1", 1, "")) == [
        "⚠️ That button is no longer valid."
    ]


def _exit_alert(world: World) -> Sent:
    """Bought on B+1, then the scans to the stop exit on B+4."""
    bot = bot_brain(world)
    sent = entry(world)
    bot.handle_callback(OWNER, "b:1", sent.message_id, sent.text)
    bot.handle_text(OWNER, "3 10.45")
    for index in range(B + 1, B + 5):
        world.scan(index)
    alert = next(m for m in world.messenger.sent if m.text.startswith("🔴"))
    return alert


def test_sold_asks_for_units_and_price_and_closes_the_position(world: World) -> None:
    alert = _exit_alert(world)
    bot = bot_brain(world, B + 5)
    [ask] = bot.handle_callback(OWNER, "x:1", alert.message_id, alert.text)
    assert ask.text.startswith("How many units did you sell, and at what price?")
    sold, edit = bot.handle_text(OWNER, "9.70")
    assert sold.text == (
        "✅ Sold 3 units ZNVD @ C$9.70 on 2026-10-12 (fill 2). Position closed: P&L −C$2.25 · "
        "−1.88R on planned risk."
    )
    assert edit.text == f"{alert.text}\n✅ Sold 3 units @ C$9.70 (fill 2)"
    exit_alert = world.session.exec(select(ExitAlert)).one()
    world.session.refresh(exit_alert)
    assert exit_alert.status == "done"
    assert texts(bot.handle_callback(OWNER, "x:1", alert.message_id, alert.text)) == [
        "Already logged: exit alert 1 is done."
    ]


def test_ignore_records_a_miss(world: World) -> None:
    alert = _exit_alert(world)
    [ignored] = bot_brain(world, B + 5).handle_callback(OWNER, "i:1", alert.message_id, alert.text)
    assert ignored.text.endswith(
        "Ignored: this counts as a miss in the scale-up check, and the position is managed "
        "again from the next session."
    )


def test_the_sell_command_is_linked_to_the_open_exit_alert(world: World) -> None:
    _exit_alert(world)
    replies = bot_brain(world, B + 5).handle_text(OWNER, "/sell nvda all 9.70")
    [sold] = texts(replies)
    assert sold == (
        "✅ Sold 3 units ZNVD @ C$9.70 on 2026-10-12 (fill 2). Position closed: P&L −C$2.25 · "
        "−1.88R on planned risk."
    )
    [fill] = world.session.exec(select(Fill).where(Fill.side == "sell")).all()
    assert fill.exit_alert_id == 1

def test_buy_and_sell_commands_and_their_errors(world: World) -> None:
    bot = bot_brain(world)
    assert texts(bot.handle_text(OWNER, "/buy ZNVD 2")) == [
        "⚠️ Usage: /buy SYMBOL QTY PRICE [YYYY-MM-DD] [force]"
    ]
    assert texts(bot.handle_text(OWNER, "/buy ZZZZ 1 10")) == [
        "⚠️ unknown symbol 'ZZZZ': use a CDR symbol, like ZNVD"
    ]
    assert texts(bot.handle_text(OWNER, "/buy xom one 5")) == [
        "⚠️ the units must be a number, not 'one'"
    ]
    assert texts(bot.handle_text(OWNER, "/buy xom 1 5 2026-13-01")) == [
        "⚠️ the date must be YYYY-MM-DD, not '2026-13-01'"
    ]
    [refused] = texts(bot.handle_text(OWNER, "/buy XOM 20 5.10"))  # a US symbol, one CDR
    assert refused.startswith("⚠️ a buy of C$102.00 on 2026-10-06 takes cash to C$-2.00")
    assert texts(bot.handle_text(OWNER, "/buy XOM 20 5.10 force")) == [
        "✅ Bought 20 units ZXOM @ C$5.10 on 2026-10-06 (fill 1), manual: no stop or exit alerts."
    ]
    assert texts(bot.handle_text(OWNER, "/sell ZXOM 25 5.20")) == [
        "⚠️ ZXOM: a sale of 25 on 2026-10-06 is more than the 20 units held"
    ]
    assert texts(bot.handle_text(OWNER, "/sell ZXOM 5 5.20")) == [
        "✅ Sold 5 units ZXOM @ C$5.20 on 2026-10-06 (fill 2). Still holding 15 units."
    ]
    assert texts(bot.handle_text(OWNER, "/sell ZXOM all 5.00 2026-10-06")) == [
        "✅ Sold 15 units ZXOM @ C$5.00 on 2026-10-06 (fill 3). Position closed: P&L −C$1.00."
    ]
    assert texts(bot.handle_text(OWNER, "/sell ZXOM all 5.00")) == [
        "⚠️ no ZXOM units are held"
    ]


def test_cash_void_and_the_read_only_commands(world: World) -> None:
    bot = bot_brain(world)
    assert texts(bot.handle_text(OWNER, "/deposit 100")) == [
        "Deposit of C$100.00 recorded on 2026-10-06. Cash is now C$200.00."
    ]
    assert texts(bot.handle_text(OWNER, "/withdraw 0")) == ["⚠️ the amount must be above 0"]
    assert texts(bot.handle_text(OWNER, "/withdraw 50.005")) == [
        "⚠️ amount -50.005 has more than 2 decimals"
    ]
    bot.handle_text(OWNER, "/buy ZXOM 2 5")
    assert texts(bot.handle_text(OWNER, "/void 1 typo")) == [
        "Fill 1 voided: typo. Cash is now C$200.00."
    ]
    assert texts(bot.handle_text(OWNER, "/void x")) == ["⚠️ Usage: /void FILL_ID REASON"]
    [signals] = texts(bot.handle_text(OWNER, "/signals"))
    assert signals == (
        "Open signals:\n• 1 ZNVD (NVDA) 3 units · LIMIT C$10.20 · stop C$9.70 · until Tue 06 Oct "
        "16:00 New York"
    )
    assert texts(bot_brain(world, B + 2).handle_text(OWNER, "/signals")) == ["No open signals."]
    [portfolio] = texts(bot.handle_text(OWNER, "/portfolio"))
    assert portfolio.splitlines()[1] == "No open positions."
    [pnl] = texts(bot.handle_text(OWNER, "/pnl"))
    assert pnl.splitlines()[-1] == (
        "No closed managed trades yet (no stored backtest run of the live config to compare)"
    )
    [tax] = texts(bot.handle_text(OWNER, "/tax 2026"))
    assert tax.splitlines()[0] == "ACB report 2026: 0 dispositions"
    assert tax.splitlines()[-1] == "Every sale: signalbench ledger tax 2026 --csv PATH"
    assert texts(bot.handle_text(OWNER, "/tax soon")) == ["⚠️ Usage: /tax YEAR"]


def test_status_shows_the_last_scan_the_config_sha_and_the_code_version(world: World) -> None:
    [status] = texts(bot_brain(world).handle_text(OWNER, "/status"))
    lines = status.splitlines()
    assert lines[0] == "Last scan: 2026-10-05 ok, started 2026-10-05 18:00 New York"
    assert lines[1] == "Next scheduled scan: 2026-10-06 15:00 (local)"
    assert lines[2].startswith("Live config: data/strategy_v2-none-cash.yaml · sha256 a9579593 · "
                               "code ")
    assert len(lines[2].rsplit(" ", 1)[1]) == 8
    assert lines[3] == "New entries: not paused"


def test_resume_shows_the_review_and_confirm_resumes(world: World) -> None:
    bot = bot_brain(world)
    assert texts(bot.handle_text(OWNER, "/resume")) == ["New entries are not paused."]
    with Session(world.session.get_bind()) as session:
        risk = Ledger(session, calendar=WeekdaySessions(), today=DAYS[B + 1]).risk_state()
        risk.paused, risk.paused_at, risk.paused_reason = True, DAYS[B], "a test pause"
        session.add(risk)
        session.commit()
    [review] = bot.handle_text(OWNER, "/resume")
    assert review.text.splitlines()[0] == (
        "⏸ New entries are paused since the close of 2026-10-05: a test pause."
    )
    assert review.text.splitlines()[-1] == "/resume to continue (resets peak)"
    assert [b.data for row in review.buttons for b in row] == ["rc"]
    assert texts(bot.handle_callback(OWNER, "rc", 7, review.text)) == [
        "▶️ Resumed: new entries are allowed again; the peak counts from 2026-10-06."
    ]
    assert texts(bot.handle_callback(OWNER, "rc", 7, review.text)) == [
        "⚠️ new entries are not paused"
    ]


def test_a_date_token_backdates_a_reply(world: World) -> None:
    bot = bot_brain(world, B + 2)
    sent = entry(world)
    bot.handle_callback(OWNER, "b:1", sent.message_id, sent.text)
    confirm, _ = bot.handle_text(OWNER, "3 10.45 2026-10-06")
    assert "on 2026-10-06 (fill 1)" in confirm.text
    assert world.session.exec(select(Fill)).one().trade_date == date(2026, 10, 6)
