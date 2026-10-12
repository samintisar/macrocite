"""Replay a paper portfolio from its start day with the backtest's own step() (spec 11).

Every night the whole paper period is replayed on the stored candles. The days already saved
must come out exactly as saved (store.first_difference); only the new days are appended.
"""

from dataclasses import dataclass
from datetime import date

from signalbench.backtest.simulator import (
    EquityPoint,
    Event,
    Orders,
    SimState,
    TradeRecord,
    initial_state,
    step,
)
from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.market_view import MarketView
from signalbench.strategy.readings import NullReadingsView


@dataclass(frozen=True)
class ReplayDay:
    day: date
    filling: Orders | None  # decided at the previous close, filled at this day's open
    state: SimState  # after this day's close
    events: list[Event]  # step()'s events, then this close's orders (order_exit, order_entry)
    trades: list[TradeRecord]  # closed at this day's open
    point: EquityPoint


def order_events(orders: Orders) -> list[Event]:
    """The orders decided at a close, as events, so the record shows each decision the night
    it was made."""
    day = orders.decided_on.isoformat()
    exits: list[Event] = [
        {"date": day, "event": "order_exit", "position_id": order.position_id,
         "symbol": order.symbol, "reason": order.reason}
        for order in orders.exits
    ]
    entries: list[Event] = [
        {"date": day, "event": "order_entry", "symbol": entry.symbol, "setup": entry.setup,
         "signal_close": entry.signal_close, "stop": entry.stop, "units": entry.units}
        for entry in orders.entries
    ]
    return [*exits, *entries]


def replay(market: MarketView, config: StrategyConfig, start: date, last: date) -> list[ReplayDay]:
    """Every day from `start` to `last`, from the config's start equity in cash."""
    state = initial_state(config)
    days: list[ReplayDay] = []
    for day in market.sessions_between(start, last):
        filling = state.orders
        result = step(state, market, NullReadingsView(), config, day)
        state = result.state
        decided = [] if state.orders is None else order_events(state.orders)
        days.append(
            ReplayDay(
                day=day,
                filling=filling,
                state=state,
                events=[*result.events, *decided],
                trades=result.trades,
                point=result.point,
            )
        )
    return days
