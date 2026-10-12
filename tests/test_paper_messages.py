from dataclasses import replace
from datetime import date

from paper_helpers import config
from signalbench.backtest.simulator import (
    EquityPoint,
    Event,
    Orders,
    TradeRecord,
    initial_state,
)
from signalbench.paper.messages import (
    alert_text,
    closed_texts,
    day_texts,
    fill_texts,
    order_texts,
)
from signalbench.paper.replay import ReplayDay
from signalbench.strategy.decision import EntryOrder, ExitOrder

DAY = date(2026, 10, 20)
BEFORE = date(2026, 10, 19)
ENTRY = EntryOrder(
    symbol="XBTUSD", setup="breakout", sector="crypto", signal_close=62410.0, stop=58900.0,
    target_r=None, time_limit=None, units=0.0005, risk_amount=1.755, catalyst=False, rank=1,
)
EXIT = ExitOrder(position_id="P00001", symbol="ETHUSD", reason="stop")
FILL: Event = {
    "date": DAY.isoformat(), "event": "entry", "position_id": "P00002", "symbol": "XBTUSD",
    "setup": "breakout", "units": 0.000527, "price": 62661.648, "trimmed": False,
}
TRADE = TradeRecord(
    position_id="P00001", symbol="XBTUSD", setup="breakout", sector="crypto",
    signal_date=date(2026, 10, 1), entry_date=date(2026, 10, 2), exit_signal_date=BEFORE,
    exit_date=DAY, signal_close=60000.0, entry_price=60000.0, exit_price=65040.0,
    initial_stop=58000.0, units=0.0005, r=2.31, pnl=2.52, reason="stop", sessions_held=18,
)


def _day(
    *,
    filling: Orders | None = None,
    orders: Orders | None = None,
    events: list[Event] | None = None,
    trades: list[TradeRecord] | None = None,
) -> ReplayDay:
    return ReplayDay(
        day=DAY,
        filling=filling,
        state=replace(initial_state(config()), orders=orders),
        events=events or [],
        trades=trades or [],
        point=EquityPoint(DAY, 100.0, 100.0, 0),
    )


def test_orders_decided_at_the_close() -> None:
    day = _day(orders=Orders(DAY, [EXIT], [ENTRY]))
    assert order_texts("crypto-v1", day) == [
        "PAPER crypto-v1: SELL ETHUSD at the next open (stop).",
        (
            "PAPER crypto-v1: BUY XBTUSD at the next open (breakout). "
            "Signal close 62,410.00, stop 58,900.00."
        ),
    ]


def test_an_entry_filled_at_the_open() -> None:
    day = _day(filling=Orders(BEFORE, [], [ENTRY]), events=[FILL])
    assert fill_texts(day, 0.004) == [
        "PAPER filled: BUY XBTUSD 0.000527 at 62,661.65 (open 62,412.00 + 0.4%), 33.02 spent."
    ]


def test_an_entry_skipped_at_the_open() -> None:
    skip: Event = {
        "date": DAY.isoformat(), "event": "skip", "symbol": "XBTUSD", "setup": "breakout",
        "reason": "gap_up",
    }
    day = _day(filling=Orders(BEFORE, [], [ENTRY]), events=[skip])
    assert fill_texts(day, 0.004) == ["PAPER not filled: BUY XBTUSD (gap_up)."]


def test_a_closed_trade() -> None:
    assert closed_texts(_day(trades=[TRADE])) == [
        "PAPER closed: XBTUSD +2.31R (+8.4%), held 18 days (stop)."
    ]


def test_a_day_reads_closed_then_filled_then_ordered() -> None:
    day = _day(
        filling=Orders(BEFORE, [], [ENTRY]), orders=Orders(DAY, [EXIT], []), events=[FILL],
        trades=[TRADE],
    )
    texts = day_texts("crypto-v1", day, 0.004)
    assert [text.split(":")[0] for text in texts] == [
        "PAPER closed", "PAPER filled", "PAPER crypto-v1",
    ]


def test_nothing_happened_sends_nothing() -> None:
    assert day_texts("crypto-v1", _day(orders=Orders(DAY, [], [])), 0.004) == []


def test_alert() -> None:
    assert alert_text("KrakenError: down") == "PAPER ALERT: KrakenError: down"
