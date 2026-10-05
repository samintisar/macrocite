"""Spec 04: the ACB engine on the hand-checked examples, superficial losses, and CDR splits."""

from datetime import date
from decimal import Decimal

import pytest

from live_helpers import example_events, load_example
from signalbench.live.acb import AcbError, SplitEvent, Trade, replay

EXAMPLES = ["acb", "acb_rebuy", "cdr_split", "fractional"]
LATER = date(2026, 12, 31)


def _buy(number: int, day: date, quantity: str, price: str) -> Trade:
    return Trade(number, day, "buy", Decimal(quantity), Decimal(price))


def _sell(number: int, day: date, quantity: str, price: str) -> Trade:
    return Trade(number, day, "sell", Decimal(quantity), Decimal(price))


@pytest.mark.parametrize("name", EXAMPLES)
def test_every_hand_checked_holding_and_disposition(name: str) -> None:
    example = load_example(name)
    trades, splits = example_events(example)
    book = replay(trades, splits, LATER)
    assert [(step.units, step.acb) for step in book.steps] == [
        (Decimal(e["units"]), Decimal(e["acb"])) for e in example["events"]
    ]
    assert [(d.trade.day, d.proceeds, d.acb, d.gain, d.denied) for d in book.dispositions] == [
        (d["date"], Decimal(d["proceeds"]), Decimal(d["acb"]), Decimal(d["gain"]),
         Decimal(d["denied"]))
        for d in example["dispositions"]
    ]
    assert not any(d.provisional for d in book.dispositions)


def test_the_rebuy_turns_half_the_loss_superficial_and_the_held_unit_carries_it() -> None:
    trades, splits = example_events(load_example("acb_rebuy"))
    book = replay(trades, splits, LATER)
    loss = book.dispositions[1]
    assert (loss.gain, loss.denied, loss.allowed) == (
        Decimal("-4.20"), Decimal("2.10"), Decimal("-2.10")
    )
    assert (book.units, book.acb, book.per_unit) == (Decimal(1), Decimal("30.10"), Decimal("30.10"))


def test_a_loss_is_provisional_until_its_30th_day_has_passed() -> None:
    trades, splits = example_events(load_example("acb_rebuy"))
    on_day_30 = replay(trades, splits, date(2026, 3, 18))
    after = replay(trades, splits, date(2026, 3, 19))
    assert [d.provisional for d in on_day_30.dispositions] == [False, True]  # a gain never is
    assert [d.provisional for d in after.dispositions] == [False, False]


def test_a_cdr_split_keeps_the_total_acb_and_halves_the_acb_per_unit() -> None:
    trades, splits = example_events(load_example("cdr_split"))
    book = replay(trades, splits, LATER)
    [record] = book.splits
    assert (record.units_before, record.units_after, record.acb) == (
        Decimal(3), Decimal(6), Decimal("90.00")
    )
    assert (record.per_unit_before, record.per_unit_after) == (Decimal(30), Decimal(15))


def test_a_split_inside_the_window_counts_later_units_on_the_sale_scale() -> None:
    # Buy 2 @ 50, sell both @ 40 (a loss of 20), a 2-for-1 split, then buy 2 post-split units @ 21.
    # On the sale's scale: bought 2 + 2/2 = 3, held at day 30 = 2/2 = 1, so
    # denied = 20 x min(2, 3, 1) / 2 = 10. The 2 units held carry ACB 10 + 42 = 52.
    trades = [
        _buy(1, date(2026, 4, 1), "2", "50"),
        _sell(2, date(2026, 4, 10), "2", "40"),
        _buy(3, date(2026, 4, 20), "2", "21"),
    ]
    book = replay(trades, [SplitEvent(1, date(2026, 4, 15), Decimal(2))], LATER)
    assert (book.dispositions[0].gain, book.dispositions[0].denied) == (Decimal(-20), Decimal(10))
    assert (book.units, book.acb) == (Decimal(2), Decimal(52))


def test_a_fill_on_the_ex_date_is_already_in_post_split_units() -> None:
    trades = [_buy(1, date(2026, 3, 2), "3", "30"), _buy(2, date(2026, 3, 9), "1", "15")]
    book = replay(trades, [SplitEvent(1, date(2026, 3, 9), Decimal(2))], LATER)
    assert (book.units, book.acb) == (Decimal(7), Decimal(105))  # 3 x 2 + 1, not (3 + 1) x 2


def test_fills_on_one_date_replay_in_the_order_they_were_recorded() -> None:
    day1, day2 = date(2026, 5, 4), date(2026, 5, 5)
    sell_first = replay(
        [_buy(1, day1, "2", "10"), _sell(2, day2, "2", "12"), _buy(3, day2, "2", "11")], [], LATER
    )
    buy_first = replay(
        [_buy(1, day1, "2", "10"), _sell(3, day2, "2", "12"), _buy(2, day2, "2", "11")], [], LATER
    )
    assert (sell_first.dispositions[0].gain, sell_first.acb) == (Decimal(4), Decimal(22))
    assert (buy_first.dispositions[0].gain, buy_first.acb) == (Decimal(3), Decimal(21))


def test_a_sale_of_more_than_is_held_is_refused_including_across_a_split() -> None:
    split = [SplitEvent(1, date(2026, 3, 9), Decimal(2))]
    buy = _buy(1, date(2026, 3, 2), "3", "30")
    assert replay([buy, _sell(2, date(2026, 3, 16), "6", "16")], split, LATER).units == 0
    with pytest.raises(AcbError, match="a sale of 7 on 2026-03-16 is more than the 6 units held"):
        replay([buy, _sell(2, date(2026, 3, 16), "7", "16")], split, LATER)
    with pytest.raises(AcbError, match="more than the 3 units held"):
        replay([buy, _sell(2, date(2026, 3, 6), "4", "31")], split, LATER)
