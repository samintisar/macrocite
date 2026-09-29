"""Spec 07: the day loop as step(), with SimState saved to JSON and reloaded between nights.

Stepping night by night, each night on a market that holds only the bars known by then, and
saving and reloading the state in between, must give exactly what one simulate() gives.
"""

import json
import random
from dataclasses import replace
from datetime import date

import pytest

from signalbench.backtest.sim_state import state_from_json, state_to_json
from signalbench.backtest.simulator import (
    EquityPoint,
    Event,
    SimState,
    TradeRecord,
    initial_state,
    simulate,
    step,
)
from signalbench.market.bars import AdjustedBar
from signalbench.strategy.config import CashVehicle, StrategyConfig
from signalbench.strategy.market_view import MarketView
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    load_test_config,
    make_bar,
    make_market,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)

PULLBACK = load_test_config().with_setups(("pullback",))
BOTH = load_test_config().with_setups(("pullback", "breakout"))
QQQ = CashVehicle(symbol="QQQ", cost_per_side=0.002)
DAYS = weekdays(date(2023, 1, 2), 280)
START, ENTRY, EXIT, END = 240, 252, 262, 270
Bars = dict[str, list[AdjustedBar]]


def _with_qqq(config: StrategyConfig) -> StrategyConfig:
    return replace(config, cash_vehicle=QQQ)


def _pullback() -> Bars:
    return {"AAA": series(DAYS, pullback_closes(len(DAYS)))}


def _crash() -> Bars:
    """The pullback fixture, then a fall to 60 after the entry: a stop exit, a pause, a resume."""
    closes = pullback_closes(len(DAYS))
    closes[ENTRY + 1 :] = [60.0] * (len(DAYS) - ENTRY - 1)
    return {"AAA": series(DAYS, closes)}


def _no_bar_on_exit() -> Bars:
    """AAA has no bar on EXIT, when its time exit is due: the exit is deferred a session."""
    bars = series(DAYS, pullback_closes(len(DAYS)))
    return {"AAA": bars[:EXIT] + bars[EXIT + 1 :]}


def _until(bars: list[AdjustedBar], day: date) -> list[AdjustedBar]:
    return [bar for bar in bars if bar.date <= day]


def _nightly(
    bars: Bars, benchmark: list[AdjustedBar], config: StrategyConfig, start: date, end: date,
    days: list[date],
) -> tuple[list[Event], list[TradeRecord], list[EquityPoint], SimState]:
    """One step per night, each on a market built from the bars known that night, with the
    state saved to JSON text and read back before the next night."""
    state = initial_state(config)
    events: list[Event] = []
    trades: list[TradeRecord] = []
    curve: list[EquityPoint] = []
    for day in [d for d in days if start <= d <= end]:
        known = {symbol: _until(series_, day) for symbol, series_ in bars.items()}
        market = make_market(known, _until(benchmark, day), days, config)
        result = step(state, market, NullReadingsView(), config, day)
        saved = json.dumps(state_to_json(result.state))
        state = state_from_json(json.loads(saved))
        events.extend(result.events)
        trades.extend(result.trades)
        curve.append(result.point)
    return events, trades, curve, state


CASES = {
    "pullback": (_pullback, PULLBACK),
    "pullback-qqq": (_pullback, _with_qqq(PULLBACK)),
    "pause": (_crash, PULLBACK),
    "pause-qqq": (_crash, _with_qqq(PULLBACK)),
    "deferred-exit": (_no_bar_on_exit, PULLBACK),
    "deferred-exit-qqq": (_no_bar_on_exit, _with_qqq(PULLBACK)),
}


@pytest.mark.parametrize("case", list(CASES))
def test_stepping_night_by_night_with_save_and_reload_equals_one_simulate(case: str) -> None:
    make, config = CASES[case]
    bars, benchmark = make(), trend_bars(DAYS, 300.0, 0.5)
    whole = simulate(
        make_market(bars, benchmark, DAYS, config), NullReadingsView(), config,
        DAYS[START], DAYS[END],
    )
    events, trades, curve, state = _nightly(bars, benchmark, config, DAYS[START], DAYS[END], DAYS)
    assert (events, trades, curve) == (whole.events, whole.trades, whole.equity_curve)
    assert list(state.positions) == whole.open_positions
    assert state.last_session == DAYS[END]
    kinds = {str(event["event"]) for event in events}
    expected = {"pause": {"pause", "resume"}, "deferred-exit": {"exit_deferred"}}
    assert expected.get(case.removesuffix("-qqq"), set()) <= kinds
    assert ("vehicle_buy" in kinds) == case.endswith("-qqq")
    assert len(trades) == 1


RANDOM_DAYS = weekdays(date(2021, 1, 4), 520)
SECTORS = {"AAA": "Energy", "BBB": "Energy", "CCC": "Energy", "DDD": "Utilities", "EEE": "Financials"}


def _walk(rng: random.Random) -> list[AdjustedBar]:
    """The random walk of test_v1_regression.py (uniform draws only)."""
    bars: list[AdjustedBar] = []
    close = 100.0
    for day in RANDOM_DAYS:
        close = max(5.0, close * (1.0008 + 0.07 * (rng.random() - 0.5)))
        volume = int(1_000_000 * (3.0 if rng.random() < 0.05 else 1.0))
        spread = close * 0.01
        bars.append(make_bar(day, close, open_=close * (1 + 0.017 * (rng.random() - 0.5)),
                             high=close + spread, low=close - spread, volume=volume))
    return bars


@pytest.mark.parametrize("config", [BOTH, _with_qqq(BOTH)], ids=["cash", "qqq"])
def test_a_long_two_setup_run_steps_to_the_same_result(config: StrategyConfig) -> None:
    rng = random.Random(20260928)
    bars = {name: _walk(rng) for name in SECTORS}
    market = make_market(
        bars, trend_bars(RANDOM_DAYS, 300.0, 0.3), RANDOM_DAYS, config, sectors=SECTORS
    )
    start, end = RANDOM_DAYS[210], RANDOM_DAYS[510]
    whole = simulate(market, NullReadingsView(), config, start, end)
    state = initial_state(config)
    curve: list[EquityPoint] = []
    events: list[Event] = []
    for day in market.sessions_between(start, end):
        result = step(state, market, NullReadingsView(), config, day)
        state = state_from_json(json.loads(json.dumps(state_to_json(result.state))))
        curve.append(result.point)
        events.extend(result.events)
    assert (events, curve) == (whole.events, whole.equity_curve)
    assert len(whole.trades) >= 10


def _states(market: MarketView, config: StrategyConfig, start: date, end: date) -> list[SimState]:
    state = initial_state(config)
    states = [state]
    for day in market.sessions_between(start, end):
        state = step(state, market, NullReadingsView(), config, day).state
        states.append(state)
    return states


@pytest.mark.parametrize("case", list(CASES))
def test_every_nightly_state_round_trips_through_json_exactly(case: str) -> None:
    make, config = CASES[case]
    market = make_market(make(), trend_bars(DAYS, 300.0, 0.5), DAYS, config)
    states = _states(market, config, DAYS[START], DAYS[END])
    for state in states:
        assert state_from_json(json.loads(json.dumps(state_to_json(state)))) == state
    assert any(state.positions for state in states)
    assert any(state.orders is not None and state.orders.entries for state in states)
    assert any(state.orders is not None and state.orders.exits for state in states)


def test_the_initial_state_holds_the_start_equity_and_nothing_else() -> None:
    state = initial_state(_with_qqq(PULLBACK))
    assert (state.cash, state.vehicle_units, state.vehicle_mark) == (100.0, 0.0, 0.0)
    assert (state.positions, state.opened, state.orders) == ((), {}, None)
    assert (state.risk.peak, state.risk.paused, state.next_id, state.sessions) == (100.0, False, 1, 0)
    assert state.last_session is None


def test_step_refuses_a_session_it_has_already_processed() -> None:
    market = make_market(_pullback(), trend_bars(DAYS, 300.0, 0.5), DAYS, PULLBACK)
    state = step(initial_state(PULLBACK), market, NullReadingsView(), PULLBACK, DAYS[START]).state
    with pytest.raises(ValueError, match="already processed"):
        step(state, market, NullReadingsView(), PULLBACK, DAYS[START])


def test_a_json_state_of_another_format_is_refused() -> None:
    data = state_to_json(initial_state(PULLBACK))
    data["format"] = 2
    with pytest.raises(ValueError, match="format 2"):
        state_from_json(data)
