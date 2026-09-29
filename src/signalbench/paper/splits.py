"""Stock splits and a paper portfolio's saved state (spec 07 changelog, 2026-09-29).

A saved state holds prices (entry price, stop, target, highest close, a pending entry's signal
close and stop, the cash vehicle's mark) on the scale of the night it was saved. yfinance
divides a stock's whole history by each split, and `ingest prices` refetches the whole history
when older closes change, so after a split the stored bars are all on the new scale while the
saved state is not. Before stepping, the runner rescales the state by every split since its last
session: prices divided by the ratio, units multiplied by it, so values and R are unchanged.

Each saved price is also checked against the stored close it came from: once the known splits
are applied, the two must agree up to later dividends (which lower older adjusted closes a
little). Anything else, such as a split yfinance does not list or one the stored prices do not
show yet, refuses the portfolio instead of stepping on two scales.
"""

import math
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from signalbench.backtest.simulator import Opened, Orders, SimState
from signalbench.ingest.prices import Split
from signalbench.paper.start import PaperRefusedError
from signalbench.strategy.market_view import MarketView

SplitFetcher = Callable[[str, date], list[Split]]  # symbol, since: splits with an ex-date after it
# Stored close / (saved price x the known splits' factor). Dividends after the save lower the
# stored adjusted close by a few percent; a missed 3-for-2 split is 1.5 or 0.67.
SCALE_BAND = (0.75, 1.02)


@dataclass(frozen=True)
class Reference:
    """A price in the saved state, and the stored close it was read from."""

    symbol: str
    day: date
    price: float
    vehicle: bool  # the cash vehicle, marked on the benchmark series


def references(state: SimState, vehicle: str | None) -> list[Reference]:
    """Every symbol whose prices the state holds: open positions (their signal close), pending
    entries (theirs), and the cash vehicle while it holds units (its last mark)."""
    refs = [
        Reference(p.symbol, state.opened[p.id].signal_date, state.opened[p.id].signal_close, False)
        for p in state.positions
    ]
    if state.orders is not None:
        refs += [
            Reference(e.symbol, state.orders.decided_on, e.signal_close, False)
            for e in state.orders.entries
        ]
    if vehicle is not None and state.vehicle_units > 0.0 and state.last_session is not None:
        refs.append(Reference(vehicle, state.last_session, state.vehicle_mark, True))
    return refs


def rescale(state: SimState, symbol: str, ratio: float, vehicle: str | None) -> SimState:
    """The state on the scale after a `ratio`-for-1 split of `symbol`."""
    positions = tuple(
        replace(p, units=p.units * ratio, entry_price=p.entry_price / ratio, stop=p.stop / ratio,
                target=None if p.target is None else p.target / ratio,
                highest_close=p.highest_close / ratio)
        if p.symbol == symbol else p
        for p in state.positions
    )
    changed = {p.id for p in state.positions if p.symbol == symbol}
    opened = {
        key: Opened(meta.signal_date, meta.signal_close / ratio, meta.initial_stop / ratio)
        if key in changed else meta
        for key, meta in state.opened.items()
    }
    orders = state.orders
    if orders is not None:
        orders = Orders(orders.decided_on, orders.exits, [
            replace(e, signal_close=e.signal_close / ratio, stop=e.stop / ratio,
                    units=e.units * ratio)  # risk_amount = units x (close - stop): unchanged
            if e.symbol == symbol else e
            for e in orders.entries
        ])
    units, mark = state.vehicle_units, state.vehicle_mark
    if symbol == vehicle:
        units, mark = units * ratio, mark / ratio
    return replace(state, positions=positions, opened=opened, orders=orders,
                   vehicle_units=units, vehicle_mark=mark)


def adjust_for_splits(
    state: SimState,
    *,
    market: MarketView,
    known: Mapping[str, list[Split]],
    applied: Collection[tuple[str, date]],
    target: date,
    vehicle: str | None,
    where: str,
) -> tuple[SimState, list[dict[str, Any]]]:
    """Apply every known split with an ex-date after the state's last session, up to `target`,
    that is not in `applied` (symbol, ex-date: logged by an earlier run). Returns the new state
    and one `split_adjust` payload per split. Raises PaperRefusedError when a saved price and
    the stored prices disagree after the splits."""
    last = state.last_session
    events: list[dict[str, Any]] = []
    if last is None:
        return state, events
    refs = references(state, vehicle)
    for symbol in sorted({ref.symbol for ref in refs}):
        due = sorted(
            (s for s in known.get(symbol, [])
             if last < s.ex_date <= target and (symbol, s.ex_date) not in applied),
            key=lambda s: s.ex_date,
        )
        factor = math.prod(1.0 / s.ratio for s in due)
        for ref in (r for r in refs if r.symbol == symbol):
            view = market.at(ref.day)
            snap = view.benchmark() if ref.vehicle else view.snapshot(symbol)
            if snap is None or ref.price <= 0.0:
                continue
            observed = snap.close / ref.price
            if not SCALE_BAND[0] <= observed / factor <= SCALE_BAND[1]:
                splits = ", ".join(f"{s.ratio:g}-for-1 on {s.ex_date}" for s in due) or "none"
                raise PaperRefusedError(
                    f"{where}: {symbol} stored close on {ref.day} is {observed:.4g}x the "
                    f"{ref.price:.4f} in the saved state, and the splits since {last} ({splits}) "
                    f"explain {factor:.4g}x. Not stepped: its prices and saved state are on "
                    "different scales (check the split in the stored prices and rerun)."
                )
        for split in due:
            state = rescale(state, symbol, split.ratio, vehicle)
            events.append(
                {"symbol": symbol, "ex_date": split.ex_date.isoformat(), "ratio": split.ratio}
            )
    return state, events
