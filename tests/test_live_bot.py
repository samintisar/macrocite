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
        "What was your average fill price for the 3 units of ZNVD? Reply with the price from "
        "Wealthsimple's confirmation, like `10.10`, or `UNITS PRICE` if you bought a different "
        "number of units. The date is the entry session, 2026-10-06; a YYYY-MM-DD token after "
        "the price changes it."
    )
    assert ask.buttons == ()  # no size or price is ever assumed
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


def test_a_whole_unit_order_needs_only_the_actual_fill_price(world: World) -> None:
    bot = bot_brain(world)
    sent = entry(world)
    assert texts(bot.handle_callback(OWNER, "u:1", sent.message_id, sent.text)) == [
        "⚠️ That button is no longer valid."  # the old [Use suggested] button assumed a price
    ]
    bot.handle_callback(OWNER, "b:1", sent.message_id, sent.text)
    confirm, _ = bot.handle_text(OWNER, "10.12")
    assert confirm.text.startswith("✅ Bought 3 units ZNVD @ C$10.12 on 2026-10-06 (fill 1)")


def test_the_date_defaults_to_the_entry_session_and_must_be_in_the_signal_window(
    world: World,
) -> None:
    bot = bot_brain(world, B + 3)  # reported two sessions late
    sent = entry(world)
    bot.handle_callback(OWNER, "b:1", sent.message_id, sent.text)
    assert texts(bot.handle_text(OWNER, "3 10.12 2026-10-07")) == [
        (
            "⚠️ 2026-10-07 is outside signal 1's entry window (after 2026-10-05, through "
            "2026-10-06). If Wealthsimple shows that date, add `force`."
        )
    ]
    confirm, _ = bot.handle_text(OWNER, "10.12")
    assert "on 2026-10-06 (fill 1)" in confirm.text  # the entry session, not today
    world.session.expire_all()
    assert world.session.exec(select(Fill)).one().trade_date == DAYS[B + 1]


def test_a_date_outside_the_window_is_recorded_with_force(world: World) -> None:
    bot = bot_brain(world, B + 3)
    sent = entry(world)
    bot.handle_callback(OWNER, "b:1", sent.message_id, sent.text)
    confirm, _ = bot.handle_text(OWNER, "3 10.12 2026-10-07 force")
    assert "on 2026-10-07 (fill 1)" in confirm.text


def test_a_fractional_order_needs_both_the_units_and_the_price(world: World) -> None:
    signal = world.session.exec(select(TradeSignal)).one()
    signal.order_type = "market"
    world.session.add(signal)
    world.session.commit()
    bot = bot_brain(world)
    sent = entry(world)
    [ask] = bot.handle_callback(OWNER, "b:1", sent.message_id, sent.text)
    assert ask.text.startswith("How many units of ZNVD did you buy, and at what average price?")
    assert texts(bot.handle_text(OWNER, "10.12")) == [
        "⚠️ A fractional order needs both: reply `UNITS PRICE`, like `3 10.10`."
    ]
    confirm, _ = bot.handle_text(OWNER, "2.97 10.12")
    assert confirm.text.startswith("✅ Bought 2.97 units ZNVD @ C$10.12")


def test_a_price_far_from_the_signal_or_too_many_units_asks_first(world: World) -> None:
    bot = bot_brain(world)
    sent = entry(world)
    bot.handle_callback(OWNER, "b:1", sent.message_id, sent.text)
    [check] = bot.handle_text(OWNER, "7 11.20")
    assert check.text == (
        "⚠️ Check this before I record it:\n"
        "• C$11.20 is 10.9% above the signal's CDR reference C$10.10\n"
        "• 7 units is more than 2× the suggested 3\n"
        "Record: buy 7 units ZNVD @ C$11.20 on 2026-10-06 for signal 1?"
    )
    [[ok, no]] = check.buttons
    assert (ok.text, ok.data, no.text, no.data) == ("✅ Confirm", "ok:1", "Cancel", "no:1")
    assert world.session.exec(select(Fill)).all() == []
    [cancelled] = bot.handle_callback(OWNER, "no:1", 77, check.text)
    assert (cancelled.edit, cancelled.text) == (77, f"{check.text}\nCancelled: nothing recorded.")
    assert texts(bot.handle_callback(OWNER, "ok:1", 77, check.text)) == [
        "⚠️ That button is no longer valid."
    ]
    [check] = bot.handle_text(OWNER, "3 11.20")  # awaiting still stands: a corrected reply
    confirm, entry_edit, check_edit = bot.handle_callback(OWNER, "ok:2", 78, check.text)
    assert confirm.text.startswith("✅ Bought 3 units ZNVD @ C$11.20 on 2026-10-06 (fill 1)")
    assert (entry_edit.edit, check_edit.edit) == (sent.message_id, 78)
    assert check_edit.text == f"{check.text}\n✅ Recorded (fill 1)."
    assert bot.awaiting is None and bot.confirming is None


def test_a_sale_price_far_from_the_mark_asks_first(world: World) -> None:
    _exit_alert(world)
    bot = bot_brain(world, B + 5)
    [check] = bot.handle_text(OWNER, "/sell ZNVD all 8.00")
    assert check.text.splitlines()[1] == "• C$8.00 is 17.9% below the latest CDR mark C$9.75"
    assert check.text.splitlines()[-1] == "Record: sell 3 units ZNVD @ C$8.00 on 2026-10-12?"
    [sold] = texts(bot.handle_callback(OWNER, "ok:1", 80, check.text))[:1]
    assert sold.startswith("✅ Sold 3 units ZNVD @ C$8.00")


def test_a_manual_buy_far_from_the_mark_asks_first(world: World) -> None:
    bot = bot_brain(world)
    [check] = bot.handle_text(OWNER, "/buy ZXOM 2 5")
    assert check.text.splitlines()[1] == "• C$5.00 is 50.0% below the latest CDR mark C$10.00"
    assert world.session.exec(select(Fill)).all() == []
    assert texts(bot.handle_callback(OWNER, "ok:1", 81, check.text))[0].startswith(
        "✅ Bought 2 units ZXOM @ C$5.00"
    )


def test_a_redelivered_command_is_recorded_once(world: World) -> None:
    bot = bot_brain(world)
    assert texts(bot.handle_text(OWNER, "/deposit 50", update_id=900)) == [
        "Deposit of C$50.00 recorded on 2026-10-06. Cash is now C$150.00."
    ]
    redelivered = "⚠️ Already recorded: Telegram delivered update 900 again."
    assert texts(bot.handle_text(OWNER, "/deposit 50", update_id=900)) == [redelivered]
    bot.handle_text(OWNER, "/buy ZXOM 2 10", update_id=901)
    assert texts(bot_brain(world).handle_text(OWNER, "/buy ZXOM 2 10", update_id=901)) == [
        "⚠️ Already recorded: Telegram delivered update 901 again."
    ]
    bot.handle_text(OWNER, "/sell ZXOM 1 10", update_id=902)
    bot.handle_text(OWNER, "/withdraw 5", update_id=903)
    for update_id, command in ((902, "/sell ZXOM 1 10"), (903, "/withdraw 5")):
        assert texts(bot.handle_text(OWNER, command, update_id=update_id)) == [
            f"⚠️ Already recorded: Telegram delivered update {update_id} again."
        ]
    world.session.expire_all()
    assert len(world.session.exec(select(Fill)).all()) == 2
    assert world.ledger(B + 1).cash() == Decimal("135.00")  # 100 + 50 - 20 + 10 - 5
    assert texts(bot.handle_text(OWNER, "/deposit 50", update_id=904))[0].endswith("C$185.00.")


def test_a_withdrawal_cannot_leave_cash_negative_without_force(world: World) -> None:
    bot = bot_brain(world)
    assert texts(bot.handle_text(OWNER, "/withdraw 100.01")) == [
        (
            "⚠️ a withdrawal of C$100.01 on 2026-10-06 takes cash to C$-0.01. If Wealthsimple "
            "shows it, record it with force"
        )
    ]
    assert texts(bot.handle_text(OWNER, "/withdraw 100.01 force")) == [
        "Withdrawal of C$100.01 recorded on 2026-10-06. Cash is now −C$0.01."
    ]


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
    [check] = bot.handle_text(OWNER, "9.70")  # one token: never silently "all"
    assert check.text == "Sell ALL 3 units ZNVD @ C$9.70 on 2026-10-12?"
    assert [b.data for row in check.buttons for b in row] == ["ok:1", "no:1"]
    sold, edit, _ = bot.handle_callback(OWNER, "ok:1", 90, check.text)
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
    [refused] = texts(bot.handle_text(OWNER, "/buy XOM 10 10.20"))  # a US symbol, one CDR
    assert refused.startswith("⚠️ a buy of C$102.00 on 2026-10-06 takes cash to C$-2.00")
    assert texts(bot.handle_text(OWNER, "/buy XOM 10 10.20 force")) == [
        "✅ Bought 10 units ZXOM @ C$10.20 on 2026-10-06 (fill 1), manual: no stop or exit alerts."
    ]
    assert texts(bot.handle_text(OWNER, "/sell ZXOM 25 10.40")) == [
        "⚠️ ZXOM: a sale of 25 on 2026-10-06 is more than the 10 units held"
    ]
    assert texts(bot.handle_text(OWNER, "/sell ZXOM 5 10.40")) == [
        "✅ Sold 5 units ZXOM @ C$10.40 on 2026-10-06 (fill 2). Still holding 5 units."
    ]
    assert texts(bot.handle_text(OWNER, "/sell ZXOM all 9.80 2026-10-06")) == [
        "✅ Sold 5 units ZXOM @ C$9.80 on 2026-10-06 (fill 3). Position closed: P&L −C$1.00."
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
    bot.handle_text(OWNER, "/buy ZXOM 2 10")
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
