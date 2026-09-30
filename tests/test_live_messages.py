"""Spec 05, Messages: the entry, stop-raised, split, exit, and failure texts and their buttons,
checked against the spec's examples."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from signalbench.live.messages import (
    CdrSplitText,
    EntryText,
    ExitText,
    RaiseText,
    SplitStopText,
    cad,
    cdr_split_message,
    entry_buttons,
    entry_message,
    exit_buttons,
    exit_message,
    failure_message,
    pct,
    raise_message,
    ratio_text,
    skip_buttons,
    split_stop_message,
    usd,
)

WHY = (
    "Closed at a 20-session high (US$181.52) on 2.3× its 50-session average volume, above its "
    "50-session average."
)
ENTRY = EntryText(
    us_symbol="NVDA", cdr_symbol="ZNVD", company="NVIDIA", cdr_close=Decimal("38.40"),
    cdr_stop=Decimal("36.0960"), stop_pct=Decimal("0.06"), units=Decimal(1),
    order_type="limit", limit=Decimal("38.78"), risk=Decimal("2.3040"), why=WHY, note=None,
)
EXIT = ExitText(
    us_symbol="NVDA", cdr_symbol="ZNVD", reason="stop", us_close=Decimal("171.20"),
    us_stop=Decimal(172), cdr_mark=Decimal("35.90"), cdr_stop=Decimal("36.10"),
    earnings_on=None, sessions_held=4, unrealized=Decimal("-2.60"),
    unrealized_pct=Decimal("-0.068"), late_after=None,
)


def test_money_and_percent_formats() -> None:
    assert (cad(Decimal("38.4")), cad(Decimal("-2.604")), cad(Decimal("1234.5"))) == (
        "C$38.40", "−C$2.60", "C$1,234.50"
    )
    assert usd(Decimal(1830)) == "US$1,830.00"
    assert (pct(Decimal("-0.06")), pct(Decimal("0.0214")), pct(Decimal(0))) == (
        "−6.0%", "+2.1%", "0.0%"
    )
    assert [ratio_text(Decimal(r)) for r in ("10", "0.1", "1.5", "2")] == [
        "10-for-1", "1-for-10", "3-for-2", "2-for-1"
    ]


def test_the_entry_message_of_the_spec() -> None:
    assert entry_message(ENTRY) == (
        "🟢 BUY NVDA (CDR ZNVD) — Breakout · NVIDIA\n"
        "Signal C$38.40 · Stop C$36.10 (−6.0%) · Trailing stop, no target, no time limit\n"
        "Size 1 unit (~C$38.40) · risk C$2.30 · LIMIT C$38.78\n"
        "Skip if price > C$38.78, price ≤ the stop C$36.10, or bid/ask spread > 0.5%\n"
        f"Why: {WHY}"
    )


def test_a_fractional_entry_shows_the_dollar_amount_and_the_price_bound() -> None:
    text = entry_message(
        replace(ENTRY, units=Decimal("0.868055"), order_type="market", risk=Decimal(2))
    )
    assert text.splitlines()[2] == (
        "Size C$33.33 (0.868055 units) · risk C$2.00 · MARKET (fractional): only place it if "
        "the price is ≤ C$38.78"
    )


def test_an_entry_moved_by_a_cboe_holiday_says_so() -> None:
    note = "Cboe Canada is closed on Mon 12 Oct: place it on Tue 13 Oct."
    lines = entry_message(replace(ENTRY, units=Decimal(3), note=note)).splitlines()
    assert lines[2].startswith("Size 3 units (~C$115.20)")
    assert lines[4] == note


def test_buttons_carry_short_callback_data() -> None:
    assert [[(b.text, b.data) for b in row] for row in entry_buttons(12)] == [
        [("✅ I bought", "b:12"), ("⏭ Skip", "s:12")]
    ]
    assert [[(b.text, b.data) for b in row] for row in exit_buttons(7)] == [
        [("✅ Sold", "x:7"), ("Ignore", "i:7")]
    ]
    assert [b.data for row in skip_buttons(12) for b in row] == [
        "k:12:disagree", "k:12:no_time", "k:12:price_moved", "k:12:wide_spread", "k:12:other"
    ]
    assert [b.text for row in skip_buttons(12) for b in row] == [
        "Disagree", "No time", "Price moved >1%", "Spread too wide", "Other"
    ]


def test_the_stop_raised_message_in_both_currencies() -> None:
    text = RaiseText(us_symbol="NVDA", cdr_symbol="ZNVD", old_cdr=Decimal("36.10"),
                     new_cdr=Decimal("38.40"), old_us=Decimal(172), new_us=Decimal("183.10"),
                     late_for=None)
    assert raise_message(text) == (
        "⬆️ NVDA (CDR ZNVD) stop raised C$36.10 → C$38.40 (US$172.00 → US$183.10) · still "
        "holding, no action"
    )
    late = replace(text, late_for=date(2026, 10, 6))
    assert raise_message(late).endswith("still holding, no action (late — for 2026-10-06)")


def test_split_messages() -> None:
    us = SplitStopText(symbol="NVDA", ratio=Decimal(10), ex_date=date(2026, 6, 10),
                       old=Decimal(1830), new=Decimal(183), currency="US$")
    assert split_stop_message(us) == (
        "ℹ️ NVDA split 10-for-1 (ex-date 2026-06-10): stop US$1,830.00 → US$183.00 · no action"
    )
    cdr = CdrSplitText(cdr_symbol="ZNVD", ratio=Decimal(2), ex_date=date(2026, 10, 5),
                       units_before=Decimal(3), units_after=Decimal(6),
                       per_unit_before=Decimal(30), per_unit_after=Decimal(15), withdrawn=(12,))
    assert cdr_split_message(cdr) == (
        "ℹ️ ZNVD split 2-for-1 (ex-date 2026-10-05): 3 units → 6, ACB per unit C$30.00 → "
        "C$15.00 (the total ACB is unchanged) · check that Wealthsimple shows 6 units · no action\n"
        "Signal 12 withdrawn: its prices no longer apply."
    )
    none_held = replace(cdr, units_before=Decimal(0), units_after=Decimal(0),
                        per_unit_before=None, per_unit_after=None)
    assert cdr_split_message(none_held) == (
        "ℹ️ ZNVD split 2-for-1 (ex-date 2026-10-05) · no action\n"
        "Signal 12 withdrawn: its prices no longer apply."
    )


def test_the_exit_message_of_the_spec() -> None:
    assert exit_message(EXIT) == (
        "🔴 SELL NVDA (CDR ZNVD) — stop hit (US close US$171.20 ≤ stop US$172.00 · CDR ~C$35.90 "
        "vs stop C$36.10)\n"
        "Held 4 sessions · unrealized −C$2.60 (−6.8%)\n"
        "Sell at the open."
    )


def test_the_earnings_and_late_exit_variants() -> None:
    earnings = exit_message(
        replace(EXIT, reason="earnings", earnings_on=date(2026, 10, 28), sessions_held=1,
                unrealized=Decimal("1.2"), unrealized_pct=Decimal("0.031"))
    )
    assert earnings.splitlines()[:2] == [
        "🔴 SELL NVDA (CDR ZNVD) — earnings on 2026-10-28, sell before them",
        "Held 1 session · unrealized +C$1.20 (+3.1%)",
    ]
    late = exit_message(replace(EXIT, late_after=date(2026, 10, 6)))
    assert late.splitlines()[0].endswith("(late — should have been sent after 2026-10-06)")


def test_the_failure_message() -> None:
    assert failure_message(2, "prices", "QQQ has no bar for 2026-10-06") == (
        "⚠️ Scan failed at step 2 (prices): QQQ has no bar for 2026-10-06"
    )
