"""Each setup fires on a known date and exits for a known reason on a known date."""

from datetime import date

from pytest import approx

from signalbench.backtest.simulator import SimulationResult, TradeRecord, simulate
from signalbench.market.bars import AdjustedBar
from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    load_test_config,
    make_market,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
    with_bar,
)

DAYS = weekdays(date(2023, 1, 2), 300)
PULLBACK = load_test_config().with_setups(("pullback",))
BREAKOUT = load_test_config().with_setups(("breakout",))
COST = 0.002
# Pullback fixture: signal on 251 (close 146), entry on 252 at the open.
PB_STOP = 145.0 - 0.5 * (30 / 14)
PB_RISK = 146.0 - PB_STOP  # R is planned at the signal: signal close - stop, about 2.071
PB_FILL = 147.0 * (1 + COST)  # every pullback scenario opens 252 at 147
PB_TARGET = PB_FILL + 2 * PB_RISK  # about 151.44


def _run(
    bars: list[AdjustedBar], config: StrategyConfig, earnings: list[date] | None = None
) -> SimulationResult:
    market = make_market(
        {"AAA": bars}, trend_bars(DAYS, 300.0, 0.5), DAYS, config,
        earnings={"AAA": earnings or []},
    )
    return simulate(market, NullReadingsView(), config, DAYS[240], DAYS[290])


def _pullback(after: dict[int, float]) -> list[AdjustedBar]:
    """The pullback fixture; from each index in `after`, closes hold that value."""
    closes = pullback_closes(len(DAYS))
    for start in sorted(after):
        closes[start:] = [after[start]] * (len(DAYS) - start)
    return series(DAYS, closes)


def _first(result: SimulationResult) -> TradeRecord:
    return result.trades[0]


def _dates(trade: TradeRecord) -> tuple[int, int, int, int]:
    return (
        DAYS.index(trade.signal_date),
        DAYS.index(trade.entry_date),
        DAYS.index(trade.exit_signal_date),
        DAYS.index(trade.exit_date),
    )


def test_pullback_stop() -> None:
    trade = _first(_run(_pullback({252: 147.0, 253: 143.5}), PULLBACK))
    assert trade.reason == "stop"
    assert _dates(trade) == (251, 252, 253, 254)
    assert (trade.entry_price, trade.initial_stop) == (approx(PB_FILL), approx(PB_STOP))
    assert trade.exit_price == approx(143.5 * (1 - COST))
    assert trade.r == approx((143.5 * (1 - COST) - PB_FILL) / PB_RISK)  # about -1.97


def test_pullback_target() -> None:
    trade = _first(_run(_pullback({252: 147.0, 255: 155.0}), PULLBACK))
    assert trade.reason == "target"
    assert _dates(trade) == (251, 252, 255, 256)
    assert 147.0 < PB_TARGET <= 155.0  # closes of 147 on 252-254 stay below it
    assert trade.r == approx((155.0 * (1 - COST) - PB_FILL) / PB_RISK)  # about 3.57
    assert trade.sessions_held == 4


def test_pullback_time_after_ten_sessions() -> None:
    trade = _first(_run(_pullback({252: 147.0}), PULLBACK))
    assert trade.reason == "time"
    assert _dates(trade) == (251, 252, 261, 262)  # 252 is session 1, 261 is session 10
    assert trade.sessions_held == 10


def test_pullback_earnings_exit_two_sessions_ahead() -> None:
    trade = _first(_run(_pullback({252: 147.0}), PULLBACK, earnings=[DAYS[256]]))
    assert trade.reason == "earnings"
    assert _dates(trade) == (251, 252, 254, 255)  # at 254 the event is D+2


def _breakout_bars() -> list[AdjustedBar]:
    """Uptrend 0.2/day, volume spike on 250 (breakout), +1/day to 160 on 260, then -3/day."""
    closes = [100.0 + 0.2 * i for i in range(251)]
    closes += [151.0 + k for k in range(10)]
    closes += [157.0 - 3.0 * j for j in range(len(DAYS) - 261)]
    return with_bar(series(DAYS, [max(c, 5.0) for c in closes]), 250, volume=2_000_000)


def test_breakout_trailing_stop_ratchets_then_stops_out() -> None:
    result = _run(_breakout_bars(), BREAKOUT)
    trade = _first(result)
    assert trade.setup == "breakout"
    assert trade.reason == "stop"
    assert trade.initial_stop == approx(150.0 - 2 * 2.0)  # close - 2 x ATR(14) of 2.0
    assert _dates(trade) == (250, 251, 262, 263)
    updates = [e for e in result.events if e["event"] == "stop_update"]
    stops = [e["new_stop"] for e in updates]
    # highest close - 3 x ATR: 153 - 6 = 147 on 253, then +1 a day up to 160 - 6 = 154 on 260
    assert stops == [approx(147.0 + k) for k in range(8)]
    assert [DAYS.index(date.fromisoformat(str(e["date"]))) for e in updates] == list(range(253, 261))
    assert all(e["new_stop"] > e["old_stop"] for e in updates)  # it never loosens
    assert trade.exit_price == approx(151.0 * (1 - COST))  # close 154 <= stop 154 on 262
