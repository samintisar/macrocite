import json
from datetime import date

from paper_helpers import BREAKOUT, CRASH, DAYS, START, all_bars, config, up_to
from signalbench.backtest.crypto_runner import crypto_market
from signalbench.backtest.simulator import simulate
from signalbench.market.calendar import DailySessions
from signalbench.paper.replay import ReplayDay, replay
from signalbench.strategy.market_view import MarketView
from signalbench.strategy.readings import NullReadingsView


def _market(last: date) -> MarketView:
    return crypto_market(up_to(all_bars(), last), config(), DailySessions(), DAYS[START], last)


def _replay(last: date) -> list[ReplayDay]:
    return replay(_market(last), config(), DAYS[START], last)


def test_replay_gives_what_simulate_gives() -> None:
    last = DAYS[-1]
    days = _replay(last)
    result = simulate(_market(last), NullReadingsView(), config(), DAYS[START], last)
    assert [day.point for day in days] == result.equity_curve
    assert [trade for day in days for trade in day.trades] == result.trades
    events = [e for day in days for e in day.events if not str(e["event"]).startswith("order_")]
    assert json.dumps(events) == json.dumps(result.events)


def test_the_breakout_close_orders_an_entry_filled_at_the_next_open() -> None:
    by_day = {day.day: day for day in _replay(DAYS[-1])}
    signal = by_day[DAYS[BREAKOUT]]
    orders = [e for e in signal.events if e["event"] == "order_entry"]
    assert [(o["symbol"], o["setup"], o["signal_close"]) for o in orders] == [
        ("XBTUSD", "breakout", 110.0)
    ]
    filled = by_day[DAYS[BREAKOUT + 1]]
    assert filled.filling is not None
    assert [entry.symbol for entry in filled.filling.entries] == ["XBTUSD"]
    assert [e["symbol"] for e in filled.events if e["event"] == "entry"] == ["XBTUSD"]


def test_the_crash_close_orders_the_exit() -> None:
    by_day = {day.day: day for day in _replay(DAYS[-1])}
    orders = [e for e in by_day[DAYS[CRASH]].events if e["event"] == "order_exit"]
    assert [(o["symbol"], o["reason"]) for o in orders] == [("XBTUSD", "stop")]
    trades = by_day[DAYS[CRASH + 1]].trades
    assert [(t.symbol, t.exit_date) for t in trades] == [("XBTUSD", DAYS[CRASH + 1])]


def test_the_first_day_fills_nothing() -> None:
    first = _replay(DAYS[START + 3])[0]
    assert first.day == DAYS[START]
    assert first.filling is None
