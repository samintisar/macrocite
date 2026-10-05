from dataclasses import replace
from datetime import date

from signalbench.strategy.exits import exit_reason, trailed_stop
from signalbench.strategy.portfolio import Position
from strategy_helpers import load_test_config, make_snapshot

CONFIG = load_test_config()
POSITION = Position(
    id="P00001",
    symbol="AAA",
    setup="pullback",
    sector="Information Technology",
    units=1.0,
    entry_price=100.0,
    entry_date=date(2024, 1, 2),
    stop=95.0,
    target=110.0,
    time_limit=10,
    sessions_held=3,
    highest_close=104.0,
)


def test_no_exit_inside_the_band() -> None:
    assert exit_reason(POSITION, make_snapshot(close=100.0), earnings_soon=False) is None


def test_stop_on_close_at_or_below_stop() -> None:
    assert exit_reason(POSITION, make_snapshot(close=95.0), earnings_soon=False) == "stop"


def test_target_on_close_at_or_above_target() -> None:
    assert exit_reason(POSITION, make_snapshot(close=110.0), earnings_soon=False) == "target"
    no_target = replace(POSITION, target=None)
    assert exit_reason(no_target, make_snapshot(close=500.0), earnings_soon=False) is None


def test_time_when_sessions_held_reaches_the_limit() -> None:
    assert exit_reason(replace(POSITION, sessions_held=9), make_snapshot(), False) is None
    assert exit_reason(replace(POSITION, sessions_held=10), make_snapshot(), False) == "time"


def test_order_is_stop_then_earnings_then_target_then_time() -> None:
    late = replace(POSITION, sessions_held=10)
    assert exit_reason(late, make_snapshot(close=94.0), earnings_soon=True) == "stop"
    assert exit_reason(late, make_snapshot(close=111.0), earnings_soon=True) == "earnings"
    assert exit_reason(late, make_snapshot(close=111.0), earnings_soon=False) == "target"


def test_trailing_stop_ratchets_up_only() -> None:
    held = replace(POSITION, setup="breakout", target=None, stop=95.0, highest_close=110.0)
    assert trailed_stop(held, make_snapshot(atr=2.0), CONFIG) == 104.0  # 110 - 3 * 2
    assert trailed_stop(held, make_snapshot(atr=6.0), CONFIG) is None  # 92 would loosen it
    assert trailed_stop(replace(held, stop=104.0), make_snapshot(atr=2.0), CONFIG) is None


def test_only_breakout_trails() -> None:
    assert trailed_stop(replace(POSITION, highest_close=200.0), make_snapshot(), CONFIG) is None


def test_a_position_with_no_time_limit_never_exits_for_time() -> None:
    open_ended = replace(
        POSITION, setup="breakout", target=None, time_limit=None, sessions_held=10_000
    )
    assert exit_reason(open_ended, make_snapshot(), earnings_soon=False) is None
    assert exit_reason(open_ended, make_snapshot(close=95.0), earnings_soon=False) == "stop"
    assert exit_reason(open_ended, make_snapshot(), earnings_soon=True) == "earnings"
